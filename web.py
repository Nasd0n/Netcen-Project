"""Browser players: a tiny HTTP + WebSocket server written on plain sockets.

It only listens on 127.0.0.1, so nothing on the network can reach it
directly. The public HTTPS link comes from the Cloudflare tunnel (tunnel.py),
which forwards to this port from the same computer.

Who can join: the invite link carries a random key in the URL fragment
(``#key=...``). Browsers don't send the fragment when loading the page, so it
stays out of the page request and the Referer header. The page reads it and
passes it when opening the WebSocket (inside HTTPS); without the right key
the upgrade is refused. A new key is made every time the server starts.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import queue
import secrets
import select
import socket
import struct
import threading
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, urlsplit

from security import MAX_MESSAGE_BYTES


WEB_PAGE = Path(__file__).resolve().parent / "web" / "index.html"
WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_HEADER_BYTES = 8192

SECURITY_HEADERS = (
    "Content-Security-Policy: default-src 'none'; script-src 'unsafe-inline'; "
    "style-src 'unsafe-inline'; connect-src 'self'; img-src data:; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'\r\n"
    "X-Content-Type-Options: nosniff\r\n"
    "X-Frame-Options: DENY\r\n"
    "Referrer-Policy: no-referrer\r\n"
    "Cache-Control: no-store\r\n"
)


class WebSocketChannel:
    """Same interface as protocol.Channel, speaking WebSocket frames (RFC 6455)."""

    def __init__(
        self,
        conn: socket.socket,
        on_message: Callable[[dict], None],
        on_close: Callable[[str], None],
        idle_limit: Optional[float] = None,
        max_per_second: Optional[float] = None,
    ) -> None:
        self.conn = conn
        self.on_message = on_message
        self.on_close = on_close
        self.idle_limit = idle_limit
        self.outbox: "queue.Queue[bytes]" = queue.Queue()
        self._closing = threading.Event()
        self._buffer = b""
        self._rate = max_per_second
        self._tokens = (max_per_second or 0) * 2
        self._last_refill = time.monotonic()

    def send(self, payload: dict) -> None:
        if not self._closing.is_set():
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.outbox.put(_frame(0x1, data))

    def close(self) -> None:
        self._closing.set()

    def run(self) -> None:
        reason = "closed"
        last_heard = last_ping = time.monotonic()
        try:
            while not self._closing.is_set():
                self._flush()
                now = time.monotonic()
                if now - last_ping > 15:
                    # Keeps the tunnel from treating the connection as idle.
                    self.outbox.put(_frame(0x9, b"hi"))
                    last_ping = now
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
                self._buffer += chunk
                problem = self._read_frames()
                if problem:
                    reason = problem
                    break
            else:
                self._flush()
                self.conn.sendall(_frame(0x8, struct.pack("!H", 1000)))
        except (OSError, ValueError) as error:
            reason = "connection error (%s)" % error.__class__.__name__
        finally:
            self._closing.set()
            try:
                self.conn.close()
            except OSError:
                pass
            self.on_close(reason)

    def _flush(self) -> None:
        while True:
            try:
                data = self.outbox.get_nowait()
            except queue.Empty:
                return
            self.conn.sendall(data)

    def _read_frames(self) -> Optional[str]:
        while True:
            if len(self._buffer) < 2:
                return None
            first, second = self._buffer[0], self._buffer[1]
            fin, opcode = first & 0x80, first & 0x0F
            masked, length = second & 0x80, second & 0x7F
            offset = 2
            if not masked:
                return "sent an unmasked frame"
            if length == 126:
                if len(self._buffer) < 4:
                    return None
                length = struct.unpack("!H", self._buffer[2:4])[0]
                offset = 4
            elif length == 127:
                return "sent an oversized message"
            if length > MAX_MESSAGE_BYTES:
                return "sent an oversized message"
            if len(self._buffer) < offset + 4 + length:
                return None
            mask = self._buffer[offset:offset + 4]
            body = bytes(b ^ mask[i % 4] for i, b in enumerate(
                self._buffer[offset + 4:offset + 4 + length]))
            self._buffer = self._buffer[offset + 4 + length:]

            if opcode == 0x8:
                return "disconnected"
            if opcode == 0x9:
                self.outbox.put(_frame(0xA, body))
                continue
            if opcode == 0xA:
                continue
            if opcode != 0x1 or not fin:
                return "sent a frame type we don't accept"
            if not self._allow():
                return "sent messages too quickly"
            try:
                value = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                value = {"type": "invalid"}
            if not isinstance(value, dict):
                value = {"type": "invalid"}
            if value.get("type") != "ping":
                self.on_message(value)

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


def _frame(opcode: int, data: bytes) -> bytes:
    head = bytes([0x80 | opcode])
    if len(data) < 126:
        head += bytes([len(data)])
    elif len(data) < 65536:
        head += bytes([126]) + struct.pack("!H", len(data))
    else:
        head += bytes([127]) + struct.pack("!Q", len(data))
    return head + data


class WebHost:
    """Serves the game page and hands WebSocket players to the game server."""

    def __init__(self, port: int, on_player: Callable[[socket.socket, str], None],
                 log: Callable[[str], None]) -> None:
        self.port = port
        self.on_player = on_player
        self.log = log
        self.key = secrets.token_urlsafe(12)
        self.listener: Optional[socket.socket] = None
        self.stop_event = threading.Event()

    def start(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", self.port))
        listener.listen(16)
        listener.settimeout(0.5)
        self.listener = listener
        self.port = listener.getsockname()[1]
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.listener is not None:
            try:
                self.listener.close()
            except OSError:
                pass

    def invite_link(self, public_url: str) -> str:
        return public_url.rstrip("/") + "/#key=" + self.key

    def _accept_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                conn, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        conn.settimeout(10)
        try:
            request = b""
            while b"\r\n\r\n" not in request:
                chunk = conn.recv(2048)
                if not chunk:
                    conn.close()
                    return
                request += chunk
                if len(request) > MAX_HEADER_BYTES:
                    raise ValueError("headers too large")
            head = request.split(b"\r\n\r\n", 1)[0].decode("latin-1")
            lines = head.split("\r\n")
            method, target, _ = (lines[0].split(" ") + ["", "", ""])[:3]
            headers = {}
            for line in lines[1:]:
                name, _, value = line.partition(":")
                headers[name.strip().lower()] = value.strip()
        except (OSError, ValueError, UnicodeDecodeError):
            conn.close()
            return

        path = urlsplit(target).path
        if method == "GET" and path == "/ws" and "websocket" in headers.get("upgrade", "").lower():
            self._upgrade(conn, target, headers)
        elif method in ("GET", "HEAD") and path in ("/", "/index.html"):
            self._send_page(conn, method == "HEAD")
        else:
            self._reply(conn, "404 Not Found", b"Not found")

    def _send_page(self, conn: socket.socket, head_only: bool) -> None:
        body = WEB_PAGE.read_bytes()
        response = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            "Content-Length: %d\r\n%sConnection: close\r\n\r\n" % (len(body), SECURITY_HEADERS)
        ).encode("latin-1")
        try:
            conn.sendall(response + (b"" if head_only else body))
        except OSError:
            pass
        conn.close()

    def _reply(self, conn: socket.socket, status: str, body: bytes) -> None:
        try:
            conn.sendall((
                "HTTP/1.1 %s\r\nContent-Type: text/plain\r\nContent-Length: %d\r\n%s"
                "Connection: close\r\n\r\n" % (status, len(body), SECURITY_HEADERS)
            ).encode("latin-1") + body)
        except OSError:
            pass
        conn.close()

    def _upgrade(self, conn: socket.socket, target: str, headers: dict) -> None:
        who = headers.get("cf-connecting-ip", "browser")
        key = parse_qs(urlsplit(target).query).get("key", [""])[0]
        if not hmac.compare_digest(key.encode(), self.key.encode()):
            self.log("Refused a browser from %s: wrong or missing invite key." % who)
            self._reply(conn, "403 Forbidden", b"This invite link isn't valid.")
            return
        # A page on another site must not be able to open a socket here.
        origin = urlsplit(headers.get("origin", "")).netloc.lower()
        host = headers.get("host", "").lower()
        if not origin or origin != host:
            self.log("Refused a browser from %s: it came from another website." % who)
            self._reply(conn, "403 Forbidden", b"Wrong origin.")
            return
        client_key = headers.get("sec-websocket-key", "")
        if headers.get("sec-websocket-version") != "13" or len(client_key) < 16:
            self._reply(conn, "400 Bad Request", b"Bad WebSocket request.")
            return

        accept = base64.b64encode(
            hashlib.sha1((client_key + WS_MAGIC).encode()).digest()).decode()
        try:
            conn.sendall((
                "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                "Connection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n" % accept
            ).encode("latin-1"))
        except OSError:
            conn.close()
            return
        self.on_player(conn, who)
