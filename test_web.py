"""Browser path: WebSocket handshake, invite key, origin check, and play."""

import base64
import json
import os
import socket
import struct
import unittest

from server import GameServer
from web import WebHost


def ws_connect(port: int, key: str, origin: str = None):
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    origin = origin or "http://127.0.0.1:%d" % port
    nonce = base64.b64encode(os.urandom(16)).decode()
    sock.sendall((
        "GET /ws?key=%s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nUpgrade: websocket\r\n"
        "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n"
        "Origin: %s\r\n\r\n" % (key, port, nonce, origin)
    ).encode())
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = sock.recv(1)
        if not chunk:
            break
        head += chunk
    return sock, head.decode("latin-1")


def ws_send(sock, payload: dict) -> None:
    data = json.dumps(payload).encode()
    mask = os.urandom(4)
    body = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    head = bytes([0x81, 0x80 | len(data)]) if len(data) < 126 else \
        bytes([0x81, 0x80 | 126]) + struct.pack("!H", len(data))
    sock.sendall(head + mask + body)


def ws_read(sock) -> dict:
    def exact(n):
        out = b""
        while len(out) < n:
            chunk = sock.recv(n - len(out))
            if not chunk:
                raise ConnectionError("closed")
            out += chunk
        return out
    while True:
        first, second = exact(2)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", exact(8))[0]
        body = exact(length)
        if first & 0x0F == 0x1:
            return json.loads(body)


class WebTests(unittest.TestCase):
    def setUp(self):
        self.server = GameServer(host="127.0.0.1", port=0)
        self.server.start()
        self.web = WebHost(0, self.server.serve_web, self.server.log)
        self.web.start()

    def tearDown(self):
        self.web.stop()
        self.server.stop()

    def test_page_is_served_with_security_headers(self):
        sock = socket.create_connection(("127.0.0.1", self.web.port), timeout=5)
        sock.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        data = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
        self.assertIn(b"200 OK", data)
        self.assertIn(b"Content-Security-Policy", data)
        self.assertIn(b"Escape Plan", data)

    def test_only_the_page_is_served(self):
        for path in ("/server.py", "/../certs/server.key", "/web/index.html/.."):
            sock = socket.create_connection(("127.0.0.1", self.web.port), timeout=5)
            sock.sendall(("GET %s HTTP/1.1\r\nHost: x\r\n\r\n" % path).encode())
            self.assertIn(b"404", sock.recv(1024))

    def test_wrong_key_is_refused(self):
        _, head = ws_connect(self.web.port, "not-the-key")
        self.assertIn("403", head)

    def test_other_websites_are_refused(self):
        _, head = ws_connect(self.web.port, self.web.key, origin="https://evil.example")
        self.assertIn("403", head)

    def test_browser_player_joins_and_gets_state(self):
        sock, head = ws_connect(self.web.port, self.web.key)
        self.assertIn("101", head)
        self.assertEqual(ws_read(sock)["type"], "hello")
        ws_send(sock, {"type": "join", "nickname": "Webby"})
        self.assertEqual(ws_read(sock), {"type": "welcome", "nickname": "Webby"})
        state = ws_read(sock)
        self.assertEqual(state["type"], "state")
        self.assertEqual(state["state"]["players"][0]["nickname"], "Webby")


if __name__ == "__main__":
    unittest.main()
