"""One synchronous readonly application slot, independent input and protocol output."""

from __future__ import annotations

import queue
import threading
import time
import uuid
from collections import OrderedDict

from mirrorly.application import setup

from . import preflight, protocol
from .transport import Outbound

METHODS = ["ping", "status", "worker.shutdown", "setup.preflight"]
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
LIMITS = {
    "handshake_bytes": protocol.HANDSHAKE_BYTES,
    "frame_bytes": protocol.FRAME_BYTES,
    "max_depth": protocol.MAX_DEPTH,
    "max_collection": protocol.MAX_COLLECTION,
    "max_nodes": protocol.MAX_NODES,
    "max_string": protocol.MAX_STRING,
    "max_requests": protocol.MAX_REQUESTS,
    "terminal_cache": 32,
}


class WorkerHost:
    def __init__(self, read, write, qualification, *, service=None, shutdown_seconds=1):
        self.read = read
        self.qualification = qualification
        self.service = service or setup.preflight_setup
        self.shutdown_seconds = shutdown_seconds
        self.session = uuid.uuid4().hex
        self.initialized = False
        self.lost = threading.Event()
        self.inbox = queue.Queue(32)
        self.completion = queue.Queue(1)
        self.out = Outbound(write, self.lost)
        self.high_water = 0
        self.ledger = {}  # Bounded by MAX_REQUESTS; never evict admission knowledge.
        self.terminals = OrderedDict()
        self.active = None
        self.executor = None
        self.terminal_delivery = None
        self.protocol_failed = False

    def _message(self, kind, payload, request=None, operation=None, *, version=protocol.VERSION):
        return protocol.message(kind, self.session, payload, request, operation, version=version)

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

    def _reject(self, rid, code):
        if rid in self.ledger and code != "stale_request_id":
            self.ledger[rid]["state"] = "rejected"
        self._response(
            rid,
            "rejected",
            error={
                "kind": "admission",
                "code": code,
                "application_invoked": False,
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
        record = self.ledger.get(rid)
        if record is None:
            return {
                "state": "never_seen",
                "reusable": protocol.request_number(rid) > self.high_water,
            }
        return {
            **record,
            "terminal_available": rid in self.terminals,
            "terminal": self.terminals.get(rid),
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

    def _finish_operation(self):
        try:
            rid, oid, result, error = self.completion.get_nowait()
        except queue.Empty:
            return
        terminal = {"result": result, "error": error, "operation_id": oid}
        self.terminals[rid] = terminal
        if len(self.terminals) > LIMITS["terminal_cache"]:
            self.terminals.popitem(last=False)
        self.ledger[rid]["state"] = "terminal"
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
        if message["operation_id"] is not None or message["interaction_id"] is not None:
            raise protocol.ProtocolFault("Client operation/interaction IDs are unsupported")
        rid = message["request_id"]
        number = protocol.request_number(rid)
        if number <= self.high_water:
            self._reject(rid, "stale_request_id")
            return False
        if len(self.ledger) >= protocol.MAX_REQUESTS:
            raise protocol.ProtocolFault("Session request limit reached; no further admission")
        self.high_water = number
        self.ledger[rid] = {"state": "received", "application_invoked": False, "operation_id": None}
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
                self.ledger[rid]["state"] = "control"
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
        if method == "setup.preflight":
            if self.active:
                self._reject(rid, "busy")
                return False
            try:
                request = preflight.request(params)
            except ValueError:
                self._reject(rid, "invalid_parameters")
                return False
            oid = uuid.uuid4().hex
            self.active = {"request_id": rid, "operation_id": oid}
            self.ledger[rid] = {
                "state": "accepted",
                "application_invoked": True,
                "operation_id": oid,
            }
            self._response(rid, "accepted", operation=oid)
            if self.lost.is_set():
                self.ledger[rid]["application_invoked"] = False
                self.active = None
                return False
            self.executor = threading.Thread(
                target=self._execute, args=(rid, oid, request), name="application-executor"
            )
            self.executor.start()
            return False
        if params and not (method == "status" and set(params) == {"request_id"}):
            self._reject(rid, "invalid_parameters")
            return False
        self.ledger[rid]["state"] = "control"
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
                    "request": lookup,
                },
            )
        elif self.active:
            self._reject(rid, "busy")
        else:
            self.terminals.clear()
            self.ledger.clear()
            delivered = self._response(rid, "terminal", result={"shutdown": "idle"})
            delivered.wait(self.shutdown_seconds)
            return True
        return False

    def run(self):
        self.out.send(
            self._message(
                "hello",
                {
                    "supported_versions": [protocol.VERSION],
                    "worker": "production-readonly",
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
            if self.executor:
                self.executor.join()
                self._finish_operation()
            deadline = time.monotonic() + self.shutdown_seconds
            while not self.out.frames.empty() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.out.close()
        return 2 if self.protocol_failed else 0
