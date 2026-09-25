"""One synchronous application slot, independent input and protocol output."""

from __future__ import annotations

import queue
import threading
import time
import uuid
from collections import OrderedDict

from mirrorly.application import setup

from . import backup as backup_wire
from . import catalog, creation, preflight, protocol, snapshots, summary
from .lifecycle import LifecycleGate
from .transport import Outbound

METHODS = [
    "ping",
    "status",
    "worker.shutdown",
    "setup.preflight",
    "setup.create",
    "tasks.list",
    "backup.run",
    "backup.summary",
    "snapshots.list",
]
CAPABILITIES = dict.fromkeys(
    [
        "resume_interaction",
        "phase_progress",
        "item_progress",
        "byte_progress",
        "current_item",
        "cooperative_cancel",
    ],
    False,
)
CAPABILITIES["resume_interaction"] = True
RESUME_SECONDS = 600


class ResumeInteractionUnavailable(Exception):
    """A required business answer was not available; never substitute a choice."""


LIMITS = {
    "handshake_bytes": protocol.HANDSHAKE_BYTES,
    "frame_bytes": protocol.FRAME_BYTES,
    "max_depth": protocol.MAX_DEPTH,
    "max_collection": protocol.MAX_COLLECTION,
    "max_nodes": protocol.MAX_NODES,
    "max_string": protocol.MAX_STRING,
    "terminal_cache": 32,
}


class WorkerHost:
    def __init__(
        self,
        read,
        write,
        qualification,
        *,
        service=None,
        create_service=None,
        backup_service=None,
        resume_seconds=RESUME_SECONDS,
        shutdown_seconds=1,
    ):
        self.read = read
        self.qualification = qualification
        self.service = service or setup.preflight_setup
        self.create_service = create_service or setup.create_backup
        self.backup_service = backup_service or backup_wire.backup.run_backup
        self.resume_seconds = resume_seconds
        self.shutdown_seconds = shutdown_seconds
        self.session = uuid.uuid4().hex
        self.initialized = False
        self.lost = threading.Event()
        self.inbox = queue.Queue(32)
        self.completion = queue.Queue(1)
        self.out = Outbound(write, self.lost)
        self.high_water = 0
        self.terminals = OrderedDict()
        self.active = None
        self.executor = None
        self.terminal_delivery = None
        self.protocol_failed = False
        self.lifecycle_gate = None
        self.resume_lock = threading.Lock()
        self.pending_resume = None

    def _message(
        self,
        kind,
        payload,
        request=None,
        operation=None,
        *,
        interaction=None,
        version=protocol.VERSION,
    ):
        return protocol.message(
            kind,
            self.session,
            payload,
            request,
            operation,
            interaction=interaction,
            version=version,
        )

    def _resume_decision(self, rid, oid, decision):
        if self.lost.is_set():
            raise ResumeInteractionUnavailable("Backup Resume interaction channel unavailable")
        pending = {
            "request_id": rid,
            "operation_id": oid,
            "interaction_id": uuid.uuid4().hex,
            "event": threading.Event(),
            "answer": None,
        }
        with self.resume_lock:
            if self.pending_resume is not None:
                raise ResumeInteractionUnavailable("Another Resume decision is pending")
            self.pending_resume = pending
        deadline = time.monotonic() + self.resume_seconds
        try:
            self.out.send(
                self._message(
                    "interaction_request",
                    {
                        "kind": "backup.resume",
                        "snapshot_id": decision.snapshot_id,
                        "created_at": decision.created_at,
                        "deadline_seconds": self.resume_seconds,
                    },
                    rid,
                    oid,
                    interaction=pending["interaction_id"],
                )
            )
            while not pending["event"].wait(0.05):
                if self.lost.is_set() or time.monotonic() >= deadline:
                    raise ResumeInteractionUnavailable("Backup Resume interaction unavailable")
            if pending["answer"] not in ("resume", "decline_resume"):
                raise ResumeInteractionUnavailable("Backup Resume interaction unavailable")
            return pending["answer"] == "resume"
        finally:
            with self.resume_lock:
                if self.pending_resume is pending:
                    self.pending_resume = None

    def _interaction_response(self, message):
        with self.resume_lock:
            pending = self.pending_resume
            if pending is None or pending["event"].is_set():
                return  # A duplicate after consumption cannot alter the answer.
            if any(
                message[key] != pending[key]
                for key in ("request_id", "operation_id", "interaction_id")
            ):
                pending["answer"] = None
            elif (
                set(message["payload"]) != {"kind", "answer"}
                or message["payload"]["kind"] != "backup.resume"
            ):
                pending["answer"] = None
            else:
                pending["answer"] = message["payload"]["answer"]
            pending["event"].set()

    def _backup_notice(self, rid, oid, kind, value):
        try:
            self.out.notice(self._message("event", {"kind": kind, "facts": value}, rid, oid))
        except Exception:
            pass  # No diagnostic delivery error may enter the Backup transaction.

    def _execute_backup(self, rid, oid, request):
        outcome, value = backup_wire.invoke(
            self.backup_service,
            request,
            lambda decision: self._resume_decision(rid, oid, decision),
            lambda cfg, repo: self._backup_notice(
                rid,
                oid,
                "backup.relocated",
                {"task_name": cfg.name, "repository_path": str(repo.path)},
            ),
            lambda notice: self._backup_notice(
                rid,
                oid,
                "backup.resume_notice",
                {
                    "kind": notice.kind,
                    "snapshot_id": notice.snapshot_id,
                    "materialized": notice.materialized,
                    "missing": notice.missing,
                    "untrusted": notice.untrusted,
                    "uncertified": notice.uncertified,
                },
            ),
        )
        try:
            payload, error = backup_wire.project(request, outcome, value)
            protocol.encode(
                self._message(
                    "response",
                    {"phase": "terminal", "result": payload, "error": error},
                    rid,
                    oid,
                )
            )
        except Exception as exc:
            payload = {
                "outcome": "unreported",
                "dry_run": request.dry_run,
                "facts": backup_wire.acknowledged(outcome, value),
            }
            error = {
                "kind": "worker",
                "code": "result_projection_failed",
                "technical": preflight.technical(exc),
            }
        self.completion.put_nowait((rid, oid, payload, error))

    def _response(self, rid, phase, *, result=None, error=None, operation=None, terminal=False):
        value = self._message(
            "response",
            {
                "phase": phase,
                "result": result,
                "error": error,
            },
            rid,
            operation,
        )
        return self.out.send(value, terminal=terminal)

    def _reject(self, rid, code, **facts):
        self._response(
            rid,
            "rejected",
            error={
                "kind": "admission",
                "code": code,
                "application_invoked": False,
                **facts,
            },
        )

    def _fault(self, reason):
        self.protocol_failed = True
        self.out.send(
            self._message(
                "protocol_error",
                {
                    "code": "protocol_fault",
                    "fatal": True,
                    "message": reason[:1024],
                },
                version=protocol.VERSION if self.initialized else None,
            )
        )
        self.lost.set()

    def _read_loop(self):
        framer = protocol.Framer()
        try:
            while not self.lost.is_set():
                data = self.read(4096)
                if not data:
                    framer.finish()
                    self.inbox.put(("eof", None))
                    return
                limit = protocol.FRAME_BYTES if self.initialized else protocol.HANDSHAKE_BYTES
                for raw in framer.feed(data, limit):
                    self.inbox.put(("message", protocol.parse(raw)))
        except Exception as exc:
            self.inbox.put(("fault", str(exc)))

    def _lookup(self, rid):
        number = protocol.request_number(rid)
        if self.active and self.active["request_id"] == rid:
            return {"state": "accepted", "application_invoked": True, **self.active}
        if rid in self.terminals:
            return {
                "state": "terminal",
                "application_invoked": True,
                "operation_id": self.terminals[rid]["operation_id"],
                "terminal_available": True,
                "terminal": self.terminals[rid],
            }
        # IDs skipped by an increasing client are also permanently inadmissible.
        # Without cached detail, do not claim whether an old ID actually executed.
        return {
            "state": "never_seen" if number > self.high_water else "stale_result_not_cached",
            "reusable": number > self.high_water,
            "application_invoked": False if number > self.high_water else None,
            "terminal_available": False,
            "terminal": None,
        }

    def _execute(self, rid, oid, request):
        try:
            value = self.service(request)
        except Exception as exc:
            payload = {"outcome": "failed", "preflight": None}
            error = {
                "kind": "application",
                "code": "preflight_failure",
                "stage": None,
                "technical": preflight.technical(exc),
            }
        else:
            try:
                payload = {"outcome": "succeeded", "preflight": preflight.project(value)}
                error = None
                protocol.encode(
                    self._message(
                        "response",
                        {
                            "phase": "terminal",
                            "result": payload,
                            "error": None,
                        },
                        rid,
                        oid,
                    )
                )
            except Exception as exc:
                # The application returned. Projection failure must not rewrite that fact.
                payload = {"application_returned": True, "preflight": None}
                error = {
                    "kind": "worker",
                    "code": "result_projection_failed",
                    "technical": preflight.technical(exc),
                }
        self.completion.put_nowait((rid, oid, payload, error))

    def _execute_create(self, rid, oid, request, approved):
        outcome, value = creation.invoke(self.create_service, request, approved)
        try:
            payload, error = creation.project(request, outcome, value)
            protocol.encode(
                self._message(
                    "response",
                    {
                        "phase": "terminal",
                        "result": payload,
                        "error": error,
                    },
                    rid,
                    oid,
                )
            )
        except Exception as exc:
            # Keep the acknowledged application outcome even if its full projection
            # could not be encoded. No second call, filesystem inspection or rollback.
            payload = {
                "application_outcome": outcome,
                "setup": creation.acknowledged_effects(outcome, value),
            }
            error = {
                "kind": "worker",
                "code": "result_projection_failed",
                "technical": preflight.technical(exc),
            }
        self.completion.put_nowait((rid, oid, payload, error))

    def _execute_catalog(self, rid, oid, request):
        try:
            payload = {"outcome": "succeeded", "catalog": catalog.execute(request)}
            error = None
            protocol.encode(
                self._message(
                    "response", {"phase": "terminal", "result": payload, "error": None}, rid, oid
                )
            )
        except Exception as exc:
            payload = {"outcome": "failed", "catalog": None}
            error = {
                "kind": "application",
                "code": "catalog_unavailable",
                "technical": preflight.technical(exc),
            }
        self.completion.put_nowait((rid, oid, payload, error))

    def _execute_summary(self, rid, oid, request):
        try:
            payload = {"outcome": "succeeded", "summary": summary.execute(request)}
            error = None
            protocol.encode(
                self._message(
                    "response", {"phase": "terminal", "result": payload, "error": None}, rid, oid
                )
            )
        except Exception as exc:
            payload = {"outcome": "failed", "summary": None}
            error = {
                "kind": "application",
                "code": "summary_unavailable",
                "technical": preflight.technical(exc),
            }
        self.completion.put_nowait((rid, oid, payload, error))

    def _execute_snapshots(self, rid, oid, request):
        try:
            payload = {"outcome": "succeeded", "page": snapshots.execute(request)}
            error = None
            protocol.encode(
                self._message(
                    "response", {"phase": "terminal", "result": payload, "error": None}, rid, oid
                )
            )
        except Exception as exc:
            payload = {"outcome": "failed", "page": None}
            error = {
                "kind": "application",
                "code": "snapshots_unavailable",
                "technical": preflight.technical(exc),
            }
        self.completion.put_nowait((rid, oid, payload, error))

    def _finish_operation(self):
        try:
            rid, oid, result, error = self.completion.get_nowait()
        except queue.Empty:
            return
        terminal = {"result": result, "error": error, "operation_id": oid}
        self.terminals[rid] = terminal
        if len(self.terminals) > LIMITS["terminal_cache"]:
            self.terminals.popitem(last=False)
        self.terminal_delivery = self._response(
            rid,
            "terminal",
            result=result,
            error=error,
            operation=oid,
            terminal=True,
        )
        # The application call ended; delivery bookkeeping must not race a client
        # that has already received terminal and sends its next operation.
        self.active = None

    def _handle(self, message):
        if message["session_id"] != self.session:
            raise protocol.ProtocolFault("Wrong session")
        if message["message_type"] == "interaction_response":
            if not self.initialized or message["protocol_version"] != protocol.VERSION:
                raise protocol.ProtocolFault("Interaction outside initialized session")
            self._interaction_response(message)
            return False
        if message["operation_id"] is not None or message["interaction_id"] is not None:
            raise protocol.ProtocolFault("Client operation/interaction IDs are unsupported")
        rid = message["request_id"]
        number = protocol.request_number(rid)
        if number <= self.high_water:
            self._reject(rid, "stale_request_id")
            return False
        self.high_water = number
        if message["protocol_version"] != protocol.VERSION:
            self._reject(rid, "unsupported_version")
            return False
        payload = message["payload"]
        if message["message_type"] == "initialize":
            if self.initialized:
                self._reject(rid, "already_initialized")
            elif payload != {"required_capabilities": []}:
                self._reject(rid, "unsupported_capability_or_initialize")
            else:
                self.initialized = True
                self._response(
                    rid,
                    "terminal",
                    result={
                        "version": protocol.VERSION,
                        "methods": METHODS,
                        "capabilities": CAPABILITIES,
                        "limits": LIMITS,
                    },
                )
            return False
        if message["message_type"] != "request":
            raise protocol.ProtocolFault("Expected initialize/request")
        if not self.initialized:
            self._reject(rid, "not_initialized")
            return False
        if (
            set(payload) != {"method", "params"}
            or not isinstance(payload["method"], str)
            or not isinstance(payload["params"], dict)
        ):
            self._reject(rid, "invalid_parameters")
            return False
        method, params = payload["method"], payload["params"]
        if method not in METHODS:
            self._reject(rid, "unsupported_method")
            return False
        if method in (
            "setup.preflight",
            "setup.create",
            "tasks.list",
            "backup.run",
            "backup.summary",
            "snapshots.list",
        ):
            if self.active and method not in ("setup.create", "backup.run"):
                self._reject(rid, "busy")
                return False
            try:
                if method == "setup.create":
                    request, approved = creation.request(params)
                elif method == "backup.run":
                    request = backup_wire.request(params)
                elif method == "backup.summary":
                    request = summary.request(params)
                elif method == "snapshots.list":
                    request = snapshots.request(params)
                elif method == "tasks.list":
                    request = catalog.request(params)
                else:
                    request = preflight.request(params)
            except ValueError:
                self._reject(rid, "invalid_parameters")
                return False
            if self.active:
                self._reject(rid, "busy")
                return False
            if method == "setup.create" or (method == "backup.run" and not request.dry_run):
                try:
                    self.lifecycle_gate.require_ownership()
                except RuntimeError:
                    self._reject(
                        rid,
                        "mutation_gate_unavailable",
                        lifecycle_gate=self.lifecycle_gate.observe(),
                    )
                    return False
            oid = uuid.uuid4().hex
            self.active = {"request_id": rid, "operation_id": oid}
            self._response(rid, "accepted", operation=oid)
            if self.lost.is_set() and method not in ("setup.create", "backup.run"):
                self.active = None
                return False
            # Once mutation is admitted, failed accepted delivery cannot undo it.
            execute = (
                self._execute_create
                if method == "setup.create"
                else self._execute_backup
                if method == "backup.run"
                else self._execute_catalog
                if method == "tasks.list"
                else self._execute_summary
                if method == "backup.summary"
                else self._execute_snapshots
                if method == "snapshots.list"
                else self._execute
            )
            args = (
                (rid, oid, request, approved) if method == "setup.create" else (rid, oid, request)
            )
            self.executor = threading.Thread(target=execute, args=args, name="application-executor")
            self.executor.start()
            return False
        if params and not (method == "status" and set(params) == {"request_id"}):
            self._reject(rid, "invalid_parameters")
            return False
        if method == "ping":
            self._response(rid, "terminal", result={"reply": "pong"})
        elif method == "status":
            try:
                lookup = self._lookup(params["request_id"]) if params else None
            except protocol.ProtocolFault:
                self._reject(rid, "invalid_parameters")
                return False
            self._response(
                rid,
                "terminal",
                result={
                    "initialized": self.initialized,
                    "channel_healthy": not self.lost.is_set(),
                    "state": "busy"
                    if self.active
                    else "terminal_pending"
                    if self.terminal_delivery
                    else "idle",
                    "active": self.active,
                    "highest_seen_request_id": str(self.high_water),
                    "lifecycle_gate": self.lifecycle_gate.observe(),
                    "request": lookup,
                },
            )
        elif self.active:
            self._reject(rid, "busy")
        else:
            self.terminals.clear()
            delivered = self._response(rid, "terminal", result={"shutdown": "idle"})
            delivered.wait(self.shutdown_seconds)
            return True
        return False

    def run(self):
        # This thread retains ownership while joining any surviving executor on EOF.
        self.lifecycle_gate = LifecycleGate()
        self.lifecycle_gate.try_acquire()
        try:
            return self._run_session()
        finally:
            self.lifecycle_gate.close()

    def _run_session(self):
        self.out.send(
            self._message(
                "hello",
                {
                    "supported_versions": [protocol.VERSION],
                    "worker": "production",
                    "qualification": self.qualification,
                    "limits": LIMITS,
                },
                version=None,
            ),
            limit=protocol.HANDSHAKE_BYTES,
        )
        reader = threading.Thread(target=self._read_loop, daemon=True, name="protocol-reader")
        reader.start()
        try:
            while True:
                self._finish_operation()
                if self.terminal_delivery and self.terminal_delivery.is_set():
                    self.terminal_delivery = None
                self.out.check_stall()
                if self.lost.is_set():
                    break
                try:
                    kind, value = self.inbox.get(timeout=0.02)
                except queue.Empty:
                    continue
                if kind == "eof":
                    self.lost.set()
                elif kind == "fault":
                    self._fault(value)
                else:
                    try:
                        if self._handle(value):
                            break
                    except protocol.ProtocolFault as exc:
                        self._fault(str(exc))
        finally:
            # Channel loss never interrupts an active synchronous application call.
            with self.resume_lock:
                if self.pending_resume is not None:
                    self.pending_resume["answer"] = None
                    self.pending_resume["event"].set()
            if self.executor:
                self.executor.join()
                self._finish_operation()
            deadline = time.monotonic() + self.shutdown_seconds
            while not self.out.frames.empty() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.out.close()
        return 2 if self.protocol_failed else 0
