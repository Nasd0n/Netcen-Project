"""Newline-delimited JSON over a TLS socket.

Each connection is handled by a single thread that does both the reading and
the writing. OpenSSL connections must not be read and written from two threads
at the same time, so other threads only drop messages into ``outbox``.
"""

from __future__ import annotations

import json
import queue
import select
import socket
import ssl
import threading
import time
from typing import Callable, Optional

from security import MAX_MESSAGE_BYTES


PING_EVERY = 15      # client -> server keep-alive, seconds
IDLE_LIMIT = 45      # server drops a client that has been silent this long


def encode(payload: dict) -> bytes:
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")


class Channel:
    def __init__(
        self,
        conn: ssl.SSLSocket,
        on_message: Callable[[dict], None],
        on_close: Callable[[str], None],
        idle_limit: Optional[float] = None,
        max_per_second: Optional[float] = None,
        ping_every: Optional[float] = None,
    ) -> None:
        self.conn = conn
        self.on_message = on_message
        self.on_close = on_close
        self.idle_limit = idle_limit
        self.ping_every = ping_every
        self.outbox: "queue.Queue[bytes]" = queue.Queue()
        self._closing = threading.Event()
        self._buffer = b""
        # Token bucket: short bursts are fine, floods are not.
        self._rate = max_per_second
        self._tokens = (max_per_second or 0) * 2
        self._last_refill = time.monotonic()

    @property
    def tls_version(self) -> str:
        return self.conn.version() or "TLS"

    def send(self, payload: dict) -> None:
        if not self._closing.is_set():
            self.outbox.put(encode(payload))

    def close(self) -> None:
        self._closing.set()

    def run(self) -> None:
        reason = "closed"
        last_heard = last_sent = time.monotonic()
        try:
            while not self._closing.is_set():
                sent = self._flush()
                now = time.monotonic()
                if sent:
                    last_sent = now
                if self.ping_every and now - last_sent > self.ping_every:
                    self.outbox.put(encode({"type": "ping"}))
                    continue

                if self.conn.pending() == 0:
                    ready, _, _ = select.select([self.conn], [], [], 0.05)
                    if not ready:
                        if self.idle_limit and now - last_heard > self.idle_limit:
                            reason = "went quiet for too long"
                            break
                        continue

                chunk = self.conn.recv(4096)
                if not chunk:
                    reason = "disconnected"
                    break
                last_heard = time.monotonic()
                problem = self._take(chunk)
                if problem:
                    reason = problem
                    break
            else:
                self._flush()
        except (OSError, ssl.SSLError, ValueError) as error:
            reason = "connection error (%s)" % error.__class__.__name__
        finally:
            self._closing.set()
            try:
                self.conn.close()
            except OSError:
                pass
            self.on_close(reason)

    def _flush(self) -> bool:
        sent = False
        while True:
            try:
                data = self.outbox.get_nowait()
            except queue.Empty:
                return sent
            self.conn.sendall(data)
            sent = True

    def _take(self, chunk: bytes) -> Optional[str]:
        self._buffer += chunk
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            if len(line) > MAX_MESSAGE_BYTES:
                return "sent an oversized message"
            if not self._allow():
                return "sent messages too quickly"
            try:
                value = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                value = {"type": "invalid"}
            if not isinstance(value, dict):
                value = {"type": "invalid"}
            if value.get("type") != "ping":
                self.on_message(value)
        if len(self._buffer) > MAX_MESSAGE_BYTES:
            return "sent an oversized message"
        return None

    def _allow(self) -> bool:
        if not self._rate:
            return True
        now = time.monotonic()
        self._tokens = min(self._rate * 2, self._tokens + (now - self._last_refill) * self._rate)
        self._last_refill = now
        if self._tokens < 1:
            return False
        self._tokens -= 1
        return True


def open_tls(host: str, port: int, context: ssl.SSLContext, timeout: float = 8) -> ssl.SSLSocket:
    raw = socket.create_connection((host, port), timeout=timeout)
    try:
        conn = context.wrap_socket(raw, server_hostname=host)
    except Exception:
        raw.close()
        raise
    conn.settimeout(10)
    return conn
