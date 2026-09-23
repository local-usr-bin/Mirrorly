"""Windows worker-lifetime admission, independent of task/repository locks.

The host/control thread owns the mutex and must join all application execution
before closing it. No GUI handle, pipe lifetime, PID or random session names it.
"""

from __future__ import annotations

import ctypes
import os
import threading
from ctypes import wintypes as w


class _Luid(ctypes.Structure):
    _fields_ = [("low", w.DWORD), ("high", w.LONG)]


class _Statistics(ctypes.Structure):
    _fields_ = [
        ("token_id", _Luid),
        ("authentication_id", _Luid),
        ("expiration", ctypes.c_longlong),
        ("type", w.DWORD),
        ("impersonation", w.DWORD),
        ("charged", w.DWORD),
        ("available", w.DWORD),
        ("groups", w.DWORD),
        ("privileges", w.DWORD),
        ("modified", _Luid),
    ]


def _function(dll, name, result, *args):
    function = getattr(dll, name)
    function.restype, function.argtypes = result, list(args)
    return function


class _Windows:
    def __init__(self):
        if os.name != "nt":
            raise OSError("Worker lifecycle admission requires Windows")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        self.process = _function(kernel, "GetCurrentProcess", w.HANDLE)
        self.close = _function(kernel, "CloseHandle", w.BOOL, w.HANDLE)
        self.free = _function(kernel, "LocalFree", w.HANDLE, w.HANDLE)
        self.open_token = _function(
            advapi, "OpenProcessToken", w.BOOL, w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)
        )
        self.info = _function(
            advapi,
            "GetTokenInformation",
            w.BOOL,
            w.HANDLE,
            w.DWORD,
            w.LPVOID,
            w.DWORD,
            ctypes.POINTER(w.DWORD),
        )
        self.sid_text = _function(
            advapi, "ConvertSidToStringSidW", w.BOOL, w.LPVOID, ctypes.POINTER(w.LPWSTR)
        )
        self.create = _function(
            kernel, "CreateMutexExW", w.HANDLE, w.LPVOID, w.LPCWSTR, w.DWORD, w.DWORD
        )
        self.wait = _function(kernel, "WaitForSingleObject", w.DWORD, w.HANDLE, w.DWORD)
        self.release = _function(kernel, "ReleaseMutex", w.BOOL, w.HANDLE)

    def identity(self):
        token = w.HANDLE()
        if not self.open_token(self.process(), 0x0008, ctypes.byref(token)):  # TOKEN_QUERY
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            size = w.DWORD()
            self.info(token, 1, None, 0, ctypes.byref(size))  # TokenUser
            if ctypes.get_last_error() != 122 or not size.value:  # insufficient buffer
                raise ctypes.WinError(ctypes.get_last_error())
            user = ctypes.create_string_buffer(size.value)
            if not self.info(token, 1, user, size, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            sid = ctypes.cast(user, ctypes.POINTER(w.LPVOID))[0]
            text = w.LPWSTR()
            if not self.sid_text(sid, ctypes.byref(text)):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                user_sid = text.value
            finally:
                self.free(ctypes.cast(text, w.HANDLE))
            stats = _Statistics()
            if not self.info(
                token, 10, ctypes.byref(stats), ctypes.sizeof(stats), ctypes.byref(size)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            auth = stats.authentication_id
            logon = f"{auth.high & 0xFFFFFFFF:08x}{auth.low:08x}"
            return f"Local\\Mirrorly.ProductionWorker.v1.{user_sid}.{logon}"
        finally:
            self.close(token)


class LifecycleGate:
    """A single host thread owns/releases; errors fail closed for future mutation.

    readonly startup remains usable even if platform/security prevents inspection.
    observe() is only a point-in-time fact; require_ownership() is the future
    admission check, on the control thread, before dispatch to the executor.
    """

    def __init__(self):
        self.name = None
        self.handle = None
        self.held = False
        self.abandoned = False
        self.error = None
        self.thread = threading.get_ident()
        try:
            self.api = _Windows()
            self.name = self.api.identity()
            # SYNCHRONIZE | MUTEX_MODIFY_STATE; default token DACL, non-inheritable.
            self.handle = self.api.create(None, self.name, 0, 0x00100001)
            if not self.handle:
                raise ctypes.WinError(ctypes.get_last_error())
        except OSError as exc:
            self.error = str(exc)

    def _same_thread(self):
        if threading.get_ident() != self.thread:
            raise RuntimeError("Lifecycle gate must remain on its host/control thread")

    def try_acquire(self):
        self._same_thread()
        if self.held:
            return True  # Do not recursively acquire the native mutex.
        if self.error or not self.handle:
            return False
        result = self.api.wait(self.handle, 0)
        if result in (0, 0x80):  # WAIT_OBJECT_0 / WAIT_ABANDONED: both acquire ownership.
            self.held = True
            self.abandoned |= result == 0x80
            return True
        if result != 0x102:  # WAIT_TIMEOUT means another owner, not an OS error.
            self.error = str(ctypes.WinError(ctypes.get_last_error()))
        return False

    def require_ownership(self):
        if not self.try_acquire():
            raise RuntimeError("Production worker lifecycle gate is not owned")

    def _release(self):
        if not self.api.release(self.handle):
            raise ctypes.WinError(ctypes.get_last_error())
        self.held = False

    def observe(self):
        self._same_thread()
        state = "held" if self.held else "unavailable"
        if not self.held and self.try_acquire():
            # Probe availability without claiming retained ownership. Never authorize
            # a future mutation from this racy observation alone.
            self._release()
            state = "available"
        if self.error or not self.handle:
            state = "error"
        return {
            "state": state,
            "identity": self.name,
            "scope": "windows_user_logon_session",
            "owned": self.held,
            "abandoned_observed": self.abandoned,
            "error": self.error,
        }

    def close(self):
        self._same_thread()
        if self.held:
            self._release()
        if self.handle:
            self.api.close(self.handle)
            self.handle = None
