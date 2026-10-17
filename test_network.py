"""End-to-end checks over real TLS sockets on localhost."""

import queue
import shutil
import socket
import ssl
import tempfile
import threading
import time
import unittest
from pathlib import Path

import security
from protocol import Channel, open_tls
from server import GameServer


class Player:
    def __init__(self, port: int, cert: Path = security.CERT_FILE) -> None:
        self.messages: "queue.Queue[dict]" = queue.Queue()
        conn = open_tls("127.0.0.1", port, security.client_context(cert))
        security.check_pinned(conn, cert)
        self.channel = Channel(conn, self.messages.put, lambda reason: self.messages.put({"type": "closed"}))
        threading.Thread(target=self.channel.run, daemon=True).start()

    def wait_for(self, kind: str, check=lambda m: True, timeout: float = 5) -> dict:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                message = self.messages.get(timeout=0.1)
            except queue.Empty:
                continue
            if message.get("type") == kind and check(message):
                return message
        raise AssertionError("never got a %r message" % kind)


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.server = GameServer(host="127.0.0.1", port=0)
        self.server.start()
        self.port = self.server.port

    def tearDown(self):
        self.server.stop()

    def test_two_players_join_and_see_each_other(self):
        alice = Player(self.port)
        hello = alice.wait_for("hello")
        self.assertIn(hello["tls"], {"TLSv1.2", "TLSv1.3"})
        self.assertEqual(hello["online"], [])

        alice.channel.send({"type": "join", "nickname": "Alice"})
        self.assertEqual(alice.wait_for("welcome")["nickname"], "Alice")

        bob = Player(self.port)
        self.assertEqual(bob.wait_for("hello")["online"], ["Alice"])
        bob.channel.send({"type": "join", "nickname": "Bob"})

        state = bob.wait_for("state", lambda m: m["state"]["status"] == "playing")["state"]
        names = sorted(p["nickname"] for p in state["players"])
        self.assertEqual(names, ["Alice", "Bob"])
        warder = next(p for p in state["players"] if p["role"] == "warder")
        self.assertEqual(state["turn_player_id"], warder["id"])

    def test_wrong_turn_is_refused(self):
        alice, bob = Player(self.port), Player(self.port)
        alice.wait_for("hello"); bob.wait_for("hello")
        alice.channel.send({"type": "join", "nickname": "Alice"})
        alice.wait_for("welcome")
        bob.channel.send({"type": "join", "nickname": "Bob"})
        message = bob.wait_for("state", lambda m: m["state"]["status"] == "playing")
        waiting = bob if message["state"]["turn_player_id"] != message["you"] else alice
        waiting.channel.send({"type": "move", "direction": "up"})
        self.assertIn("not your turn", waiting.wait_for("error")["message"])

    def test_night_warder_never_receives_the_hidden_prisoner(self):
        self.server.game.mode = "night"
        alice, bob = Player(self.port), Player(self.port)
        alice.wait_for("hello"); bob.wait_for("hello")
        alice.channel.send({"type": "join", "nickname": "Alice"})
        alice.wait_for("welcome")
        bob.channel.send({"type": "join", "nickname": "Bob"})
        views = {}
        for player in (alice, bob):
            message = player.wait_for("state", lambda m: m["state"]["status"] == "playing")
            views[message["you"]] = message["state"]
        with self.server.lock:
            roles = {p.role: p for p in self.server.game.players.values()}
        warder_view = views[roles["warder"].player_id]
        hidden = next(p for p in warder_view["players"] if p["role"] == "prisoner")
        self.assertEqual(warder_view["mode"], "night")
        self.assertIsNone(hidden["row"])
        self.assertNotIn("P", "".join(warder_view["board"]))
        # ...and it works the other way too: they start out of each other's sight.
        prisoner_view = views[roles["prisoner"].player_id]
        self.assertIsNone(next(p for p in prisoner_view["players"] if p["role"] == "warder")["row"])
        self.assertNotIn("W", "".join(prisoner_view["board"]))
        self.assertIsNone(prisoner_view["night"]["lantern"])
        # The key is the prisoner's secret.
        self.assertIsNone(warder_view["night"]["key"])
        self.assertIsNotNone(prisoner_view["night"]["key"])

    def test_players_pick_sides_over_tls(self):
        alice, bob = Player(self.port), Player(self.port)
        alice.wait_for("hello"); bob.wait_for("hello")
        alice.channel.send({"type": "join", "nickname": "Alice", "side": "prisoner"})
        alice.wait_for("welcome")
        bob.channel.send({"type": "join", "nickname": "Bob"})
        message = bob.wait_for("state", lambda m: m["state"]["status"] == "playing")
        players = {p["nickname"]: p for p in message["state"]["players"]}
        self.assertEqual(players["Alice"]["role"], "prisoner")
        self.assertEqual(players["Alice"]["side"], "prisoner")
        self.assertEqual(players["Bob"]["side"], "any")
        self.assertIn("Sides as picked", message["message"])

        for bad in ("boss", ["warder"], None):
            bob.channel.send({"type": "side", "side": bad})
            self.assertIn("Pick Warder, Prisoner or Either", bob.wait_for("error")["message"])

        bob.channel.send({"type": "side", "side": "prisoner"})
        update = alice.wait_for("state", lambda m: any(
            p["nickname"] == "Bob" and p["side"] == "prisoner" for p in m["state"]["players"]))
        players = {p["nickname"]: p for p in update["state"]["players"]}
        self.assertEqual(players["Alice"]["role"], "prisoner")     # roles hold until the next match
        self.assertIn("Bob wants to play Prisoner", update["message"])

    def test_join_with_an_unknown_side_is_refused(self):
        eve = Player(self.port)
        eve.wait_for("hello")
        eve.channel.send({"type": "join", "nickname": "Eve", "side": "king"})
        self.assertIn("Pick Warder", eve.wait_for("error")["message"])
        eve.channel.send({"type": "join", "nickname": "Eve", "side": "warder"})
        self.assertEqual(eve.wait_for("welcome")["nickname"], "Eve")

    def test_plain_tcp_is_refused(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as raw:
            raw.sendall(b'{"type":"join","nickname":"Mallory"}\n')
            raw.settimeout(5)
            try:
                data = raw.recv(1024)
            except (ConnectionResetError, socket.timeout):
                data = b""
        self.assertNotIn(b"hello", data)

    def test_oversized_message_disconnects(self):
        eve = Player(self.port)
        eve.wait_for("hello")
        eve.channel.send({"type": "join", "nickname": "x" * 10000})
        eve.wait_for("closed")

    def test_client_refuses_a_server_with_another_certificate(self):
        other_dir = Path(tempfile.mkdtemp())
        try:
            original = (security.CERT_FILE, security.KEY_FILE, security.CERT_DIR)
            security.CERT_DIR = other_dir
            security.CERT_FILE = other_dir / "server.crt"
            security.KEY_FILE = other_dir / "server.key"
            try:
                security.ensure_certificate()
                other_cert = security.CERT_FILE
            finally:
                security.CERT_FILE, security.KEY_FILE, security.CERT_DIR = original
            with self.assertRaises(ssl.SSLError):
                Player(self.port, cert=other_cert)
        finally:
            shutil.rmtree(other_dir)


if __name__ == "__main__":
    unittest.main()
