"""Bounded, non-blocking producer boundaries. Only pumps touch output streams."""

from __future__ import annotations

import io
import queue
import threading
import time

from . import protocol


class Outbound:
    def __init__(self, write, lost, *, capacity=32, stall_seconds=30):
        self.write = write
        self.lost = lost
        self.capacity = capacity
        self.stall_seconds = stall_seconds
        self.frames = queue.Queue(capacity + 1)  # One reserved terminal position.
        self.notices = queue.Queue(16)
        self.writing_since = None
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self._pump, daemon=True, name="protocol-writer")
        self.thread.start()

    def send(self, value, *, terminal=False, limit=protocol.FRAME_BYTES):
        delivered = threading.Event()
        try:
            data = protocol.encode(value, limit)
            if not terminal and self.frames.qsize() >= self.capacity:
                raise queue.Full
            self.frames.put_nowait((data, delivered))
        except (queue.Full, ValueError, UnicodeError):
            self.lost.set()
        return delivered

    def notice(self, value):
        """Future notice callbacks may drop facts; they never perform pipe IO."""
        try:
            self.notices.put_nowait(protocol.encode(value))
        except Exception:
            return False
        return True

    def _pump(self):
        while not self.closed.is_set():
            try:
                data, delivered = self.frames.get(timeout=0.05)
            except queue.Empty:
                try:
                    data, delivered = self.notices.get_nowait(), None
                except queue.Empty:
                    continue
            try:
                self.writing_since = time.monotonic()
                self.write(data)
                if delivered:
                    delivered.set()
            except Exception:
                self.lost.set()
                return
            finally:
                self.writing_since = None

    def check_stall(self):
        started = self.writing_since
        if started is not None and time.monotonic() - started > self.stall_seconds:
            self.lost.set()

    def close(self):
        self.closed.set()  # Never join an indefinitely blocked pipe writer.


class Diagnostics(io.TextIOBase):
    """Lossy diagnostics; even accidental print/warnings cannot corrupt stdout."""

    def __init__(self, write):
        self.sink = write
        self.chunks = queue.Queue(32)
        self.thread = threading.Thread(target=self._pump, daemon=True, name="diagnostic-writer")
        self.thread.start()

    def write(self, text):
        if text:
            try:
                self.chunks.put_nowait(text[:2048].encode("utf-8", errors="replace"))
            except queue.Full:
                pass
        return len(text)

    def flush(self):
        pass

    def _pump(self):
        while True:
            try:
                self.sink(self.chunks.get())
            except Exception:
                return
