"""Escape Plan server: TLS socket server + a small dashboard window.

Run ``python3 server.py``. The first run creates ``certs/server.crt``; copy
that one file to the other computer so its client can verify this server.
"""

from __future__ import annotations

import itertools
import socket
import ssl
import threading
import time
import uuid
import tkinter as tk
from tkinter import messagebox
from typing import Callable, Optional

import security
from config import SERVER_PORT, WEB_PORT
from game import DIAGONALS, MODE_NAMES, MODES, SIDE_NAMES, SIDES, EscapeGame
from protocol import IDLE_LIMIT, Channel
from tunnel import Tunnel
from web import WebHost, WebSocketChannel
from theme import (
    BAD, CARD, FAINT, GOOD, INK, LINE, NIGHT_CHIP, NIGHT_YARD, PAPER, SOFT, SUN_SOFT, YARD,
    Fonts, draw_barrier, draw_crate, draw_key, draw_searchlight, draw_smoke, draw_sprint, PillButton, card, draw_prisoner, draw_tunnel, draw_wall, draw_warder,
)


ITEM_DRAW = {"searchlight": draw_searchlight, "barricade": draw_barrier,
             "smoke": draw_smoke, "sprint": draw_sprint}

MAX_CONNECTIONS = 8          # players + people sitting on the name screen
HANDSHAKE_TIMEOUT = 8
JOIN_TIMEOUT = 90
MESSAGES_PER_SECOND = 8
MOVES = {"up", "down", "left", "right", *DIAGONALS}


class ClientSession:
    _numbers = itertools.count(1)

    def __init__(self, address: tuple, tls_version: str) -> None:
        self.address = address
        self.tls_version = tls_version
        self.player_id = uuid.uuid4().hex[:12]
        self.guest_number = next(self._numbers)
        self.nickname: Optional[str] = None
        self.connected_at = time.time()
        self.channel: Optional[Channel] = None

    @property
    def label(self) -> str:
        return self.nickname or "Guest %d" % self.guest_number

    def send(self, payload: dict) -> None:
        if self.channel is not None:
            self.channel.send(payload)


class GameServer:
    """Accepts TLS clients and runs the one shared game."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = SERVER_PORT,
        on_event: Callable[[str, object], None] = lambda kind, value: None,
    ) -> None:
        self.host = host
        self.port = port
        self.on_event = on_event
        self.game = EscapeGame()
        self.lock = threading.RLock()
        self.clients: dict[str, ClientSession] = {}
        self.stop_event = threading.Event()
        self.listener: Optional[socket.socket] = None
        self.tls: Optional[ssl.SSLContext] = None
        self.fingerprint = ""

    # ----- lifecycle -------------------------------------------------------

    def start(self) -> None:
        if security.ensure_certificate():
            self.log("Created a new TLS certificate in certs/.")
        self.tls = security.server_context()
        self.fingerprint = security.pinned_fingerprint()

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.host, self.port))
        listener.listen(8)
        listener.settimeout(0.5)
        self.listener = listener
        self.port = listener.getsockname()[1]

        threading.Thread(target=self._accept_loop, daemon=True).start()
        threading.Thread(target=self._timer_loop, daemon=True).start()
        self.log("Listening on port %d (TLS only)." % self.port)

    def stop(self) -> None:
        self.stop_event.set()
        if self.listener is not None:
            try:
                self.listener.close()
            except OSError:
                pass
        with self.lock:
            sessions = list(self.clients.values())
        for session in sessions:
            if session.channel:
                session.channel.close()

    # ----- networking ------------------------------------------------------

    def _accept_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                raw, address = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._serve, args=(raw, address), daemon=True).start()

    def _serve(self, raw: socket.socket, address: tuple) -> None:
        with self.lock:
            full = len(self.clients) >= MAX_CONNECTIONS
        if full:
            self.log("Turned away %s: too many connections." % address[0])
            raw.close()
            return

        raw.settimeout(HANDSHAKE_TIMEOUT)
        try:
            conn = self.tls.wrap_socket(raw, server_side=True)
        except (ssl.SSLError, OSError):
            self.log("Refused %s: it didn't speak TLS." % address[0])
            raw.close()
            return
        conn.settimeout(10)

        session = ClientSession(address, conn.version() or "TLS")
        session.channel = Channel(
            conn,
            on_message=lambda message: self._on_message(session, message),
            on_close=lambda reason: self._on_close(session, reason),
            idle_limit=IDLE_LIMIT,
            max_per_second=MESSAGES_PER_SECOND,
        )
        self._run_session(session)

    def serve_web(self, conn: socket.socket, who: str) -> None:
        """A browser that came in through the HTTPS tunnel (see web.py)."""

        with self.lock:
            full = len(self.clients) >= MAX_CONNECTIONS
        if full:
            self.log("Turned away a browser from %s: too many connections." % who)
            conn.close()
            return
        session = ClientSession((who, 0), "HTTPS link")
        session.channel = WebSocketChannel(
            conn,
            on_message=lambda message: self._on_message(session, message),
            on_close=lambda reason: self._on_close(session, reason),
            idle_limit=IDLE_LIMIT,
            max_per_second=MESSAGES_PER_SECOND,
        )
        self._run_session(session)

    def _run_session(self, session: ClientSession) -> None:
        address = session.address
        with self.lock:
            self.clients[session.player_id] = session
            online = self._player_names()
            room_full = len(self.game.players) >= 2
        self.log("%s connected from %s over %s." % (session.label, address[0], session.tls_version))
        session.send({
            "type": "hello",
            "online": online,
            "room_full": room_full,
            "tls": session.tls_version,
        })
        self._notify_dashboard()
        threading.Thread(target=self._join_deadline, args=(session,), daemon=True).start()
        session.channel.run()

    def _join_deadline(self, session: ClientSession) -> None:
        time.sleep(JOIN_TIMEOUT)
        if session.nickname is None and session.channel is not None:
            session.send({"type": "error", "message": "Timed out waiting for a nickname."})
            session.channel.close()

    def _on_message(self, session: ClientSession, message: dict) -> None:
        kind = message.get("type")

        if kind == "quit":
            session.channel.close()
            return
        if session.nickname is None:
            if kind != "join":
                session.send({"type": "error", "message": "Please choose a nickname first."})
                return
            self._join(session, message.get("nickname", ""), message.get("side", "any"))
            return

        if kind == "move":
            direction = message.get("direction")
            if direction not in MOVES:
                session.send({"type": "error", "message": "That isn't a direction."})
                return
            with self.lock:
                before = (self.game.turn_player_id, self.game.status)
                accepted, text = self.game.move(session.player_id, direction)
                # A refused move can still have changed things (the turn ran out).
                broadcast = accepted or before != (self.game.turn_player_id, self.game.status)
            if broadcast:
                self.broadcast(text)
            else:
                session.send({"type": "error", "message": text})
        elif kind == "catch":
            with self.lock:
                before = (self.game.turn_player_id, self.game.status)
                accepted, text = self.game.catch(session.player_id, message.get("target"))
                broadcast = accepted or before != (self.game.turn_player_id, self.game.status)
            if broadcast:
                self.broadcast(text)
            else:
                session.send({"type": "error", "message": text})
        elif kind == "item":
            with self.lock:
                accepted, text = self.game.use_item(
                    session.player_id, str(message.get("item", ""))[:20], message.get("target"))
            if accepted:
                self.broadcast(text)
            else:
                session.send({"type": "error", "message": text})
        elif kind == "side":
            side = message.get("side")
            if not isinstance(side, str) or side not in SIDES:
                session.send({"type": "error", "message": "Pick Warder, Prisoner or Either."})
                return
            with self.lock:
                changed = self.game.players[session.player_id].side != side
                text = self.game.set_side(session.player_id, side)
            if changed:
                self.broadcast(text)
        elif kind == "rematch":
            with self.lock:
                accepted, text = self.game.request_rematch(session.player_id)
            if accepted:
                self.broadcast(text)
            else:
                session.send({"type": "error", "message": text})
        else:
            session.send({"type": "error", "message": "Unknown request."})

    def _join(self, session: ClientSession, nickname: object, side: object = "any") -> None:
        if not isinstance(side, str) or side not in SIDES:
            session.send({"type": "error", "message": "Pick Warder, Prisoner or Either."})
            return
        try:
            with self.lock:
                self.game.add_player(session.player_id, str(nickname)[:64], side)
                session.nickname = self.game.players[session.player_id].nickname
                started = len(self.game.players) == 2 and self.game.start_match()
                note = self.game.role_note
        except ValueError as error:
            session.send({"type": "error", "message": str(error)})
            return

        self.log("%s joined the game." % session.nickname)
        session.send({"type": "welcome", "nickname": session.nickname})
        if started:
            self.broadcast("Both players are here. Match on! The warder moves first. " + note)
        else:
            self.broadcast("%s is waiting for an opponent." % session.nickname)

    def _on_close(self, session: ClientSession, reason: str) -> None:
        with self.lock:
            self.clients.pop(session.player_id, None)
            was_player = session.player_id in self.game.players
            if was_player:
                self.game.remove_player(session.player_id)
        self.log("%s left (%s)." % (session.label, reason))
        if was_player:
            self.broadcast("%s left the game." % session.nickname)
        else:
            self._notify_dashboard()

    # ----- game broadcast --------------------------------------------------

    def _player_names(self) -> list[str]:
        return [player.nickname for player in self.game.players.values()]

    def connected_clients(self) -> list[dict]:
        with self.lock:
            return [
                {
                    "id": session.player_id,
                    "nickname": session.label,
                    "joined": session.nickname is not None,
                    "address": session.address[0],
                    "tls": session.tls_version,
                }
                for session in self.clients.values()
            ]

    def broadcast(self, message: Optional[str] = None) -> None:
        with self.lock:
            room = [
                {"id": s.player_id, "nickname": s.label, "joined": s.nickname is not None}
                for s in self.clients.values()
            ]
            sessions = list(self.clients.values())
            # Each player gets their own view: in Night Escape the warder must
            # not receive the prisoner's position while it's dark.
            views = {s.player_id: self.game.snapshot(s.player_id) for s in sessions if s.nickname}
            state = self.game.snapshot()
            online = self._player_names()
            room_full = len(self.game.players) >= 2

        for session in sessions:
            if session.nickname is None:
                # People still on the name screen only see who's online.
                session.send({"type": "lobby", "online": online, "room_full": room_full})
                continue
            view = views[session.player_id]
            view["connected_clients"] = room
            payload = {"type": "state", "state": view, "you": session.player_id}
            if message:
                payload["message"] = message
            session.send(payload)
        self._notify_dashboard(state)

    def _notify_dashboard(self, state: Optional[dict] = None) -> None:
        if state is None:
            with self.lock:
                state = self.game.snapshot()
        state = dict(state)
        state["connected_clients"] = self.connected_clients()
        self.on_event("state", state)

    def _timer_loop(self) -> None:
        last = None
        while not self.stop_event.wait(0.2):
            with self.lock:
                message = self.game.expire_turn()
                snap = self.game.snapshot()
            signature = (snap["status"], snap["turn_player_id"], snap["turn_seconds_left"])
            if message or signature != last:
                last = signature
                self.broadcast(message)

    def set_mode(self, mode: str) -> None:
        with self.lock:
            if mode == self.game.mode:
                return
            started = self.game.set_mode(mode)
            note = self.game.role_note
        self.log("Switched to %s mode." % MODE_NAMES[mode])
        if started:
            self.broadcast("The server switched to %s. New match - scores kept! %s"
                           % (MODE_NAMES[mode], note))
        else:
            self.broadcast("The next match will be played in %s mode." % MODE_NAMES[mode])

    def reset_game(self) -> None:
        with self.lock:
            self.game.reset_game()
            note = self.game.role_note if self.game.status == "playing" else None
        self.log("Scores reset and a fresh match started.")
        self.broadcast("The server reset the game. Scores are back to 0-0." + (" " + note if note else ""))

    def swap_sides(self) -> None:
        with self.lock:
            swapped = self.game.swap_sides()
        if swapped:
            self.log("Sides will swap next match.")
            self.broadcast("The server asked for swapped sides next match.")
        else:
            self.log("Nothing to swap yet: sides are dealt when a match starts.")

    def log(self, text: str) -> None:
        self.on_event("log", text)


# ---------------------------------------------------------------------------
# Dashboard window
# ---------------------------------------------------------------------------


class ServerDashboard:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        Fonts.load(root)
        root.title("Escape Plan - Server")
        root.geometry("940x800")
        root.minsize(860, 780)
        root.configure(bg=PAPER)
        root.protocol("WM_DELETE_WINDOW", self.shutdown)

        self.events: list[tuple[str, object]] = []
        self.events_lock = threading.Lock()
        self.server = GameServer(on_event=self._queue)

        self.count_var = tk.StringVar(value="0")
        self.match_var = tk.StringVar(value="No match yet")
        self.address_var = tk.StringVar(value="")
        self._build()

        try:
            self.server.start()
            addresses = [a for a in security.local_addresses() if a != "127.0.0.1"]
            self.address_var.set(
                "Clients should use %s, port %d"
                % (" or ".join(addresses) or "127.0.0.1", self.server.port)
            )
            self.fingerprint_label.configure(
                text="Certificate fingerprint  " + security.short_fingerprint(self.server.fingerprint)
            )
        except (OSError, RuntimeError, ssl.SSLError) as error:
            self._append_log("Couldn't start the server: %s" % error)
            self.address_var.set("Server is not running - see the log below.")

        self.web: Optional[WebHost] = None
        self.tunnel: Optional[Tunnel] = None
        self.invite = ""
        if self.server.listener is not None:
            self._start_link()

        root.after(100, self._drain)

    def _start_link(self) -> None:
        try:
            self.web = WebHost(WEB_PORT, self.server.serve_web, self.server.log)
            self.web.start()
        except OSError as error:
            self._show_link_problem("Browser play is off: port %d is busy (%s)." % (WEB_PORT, error))
            return
        self.tunnel = Tunnel(
            self.web.port,
            on_url=lambda url: self._queue("link", url),
            on_problem=lambda text: self._queue("link_problem", text),
        )
        self.tunnel.start()

    # ----- layout ----------------------------------------------------------

    def _build(self) -> None:
        top = tk.Frame(self.root, bg=PAPER)
        top.pack(fill="x", padx=26, pady=(22, 14))
        tk.Label(top, text="Escape Plan", bg=PAPER, fg=INK, font=Fonts.get(24, "bold")).pack(side="left")
        tk.Label(top, text="server", bg=PAPER, fg=SOFT, font=Fonts.get(15)).pack(side="left", padx=(8, 0), pady=(8, 0))

        lock = tk.Frame(top, bg=SUN_SOFT)
        lock.pack(side="right")
        tk.Label(lock, text="Encrypted with TLS", bg=SUN_SOFT, fg=INK,
                 font=Fonts.get(11, "bold"), padx=12, pady=5).pack()

        info = tk.Frame(self.root, bg=PAPER)
        info.pack(fill="x", padx=26)
        tk.Label(info, textvariable=self.address_var, bg=PAPER, fg=SOFT,
                 font=Fonts.get(11)).pack(anchor="w")
        self.fingerprint_label = tk.Label(info, text="", bg=PAPER, fg=FAINT, font=("Menlo", 10))
        self.fingerprint_label.pack(anchor="w", pady=(2, 0))

        # Invite link for browser players
        invite = card(self.root, fill="x", padx=26, pady=(14, 0))
        invite_row = tk.Frame(invite, bg=CARD)
        invite_row.pack(fill="x", padx=18, pady=14)
        self.qr_label = tk.Label(invite_row, bg=CARD)
        self.qr_label.pack(side="right", padx=(12, 0))
        tk.Label(invite_row, text="Invite link", bg=CARD, fg=INK,
                 font=Fonts.get(13, "bold")).pack(anchor="w")
        tk.Label(invite_row, text="Anyone you send this to can play from a browser, "
                 "on a laptop or phone, from anywhere.", bg=CARD, fg=SOFT,
                 font=Fonts.get(11)).pack(anchor="w")
        self.link_label = tk.Label(invite_row, text="Opening a secure tunnel...", bg=CARD,
                                   fg=FAINT, font=("Menlo", 11), wraplength=560, justify="left")
        self.link_label.pack(anchor="w", pady=(8, 8))
        self.copy_button = PillButton(invite_row, "Copy link", command=self.copy_link,
                                      color=INK, width=140, height=36, font_size=11)
        self.copy_button.pack(anchor="w")
        self.copy_button.configure_button(enabled=False)

        body = tk.Frame(self.root, bg=PAPER)
        body.pack(fill="both", expand=True, padx=26, pady=16)

        left = tk.Frame(body, bg=PAPER)
        left.pack(side="left", fill="both", expand=True)
        right = tk.Frame(body, bg=PAPER)
        right.pack(side="right", fill="y", padx=(18, 0))

        # Who's connected
        people = card(left, fill="x")
        head = tk.Frame(people, bg=CARD)
        head.pack(fill="x", padx=18, pady=(14, 6))
        tk.Label(head, textvariable=self.count_var, bg=CARD, fg=INK,
                 font=Fonts.get(30, "bold")).pack(side="left")
        tk.Label(head, text="clients connected right now", bg=CARD, fg=SOFT,
                 font=Fonts.get(12)).pack(side="left", padx=(10, 0), pady=(12, 0))
        self.people_list = tk.Frame(people, bg=CARD)
        self.people_list.pack(fill="x", padx=18, pady=(0, 14))

        # Log
        log_card = card(left, fill="both", expand=True, pady=(14, 0))
        tk.Label(log_card, text="What's been happening", bg=CARD, fg=INK,
                 font=Fonts.get(13, "bold")).pack(anchor="w", padx=18, pady=(14, 6))
        self.log_text = tk.Text(
            log_card, height=4, width=44, bg=CARD, fg=SOFT, borderwidth=0, highlightthickness=0,
            wrap="word", state="disabled", font=Fonts.get(11), padx=4,
        )
        self.log_text.tag_configure("time", foreground=FAINT)
        self.log_text.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        # Live board + reset
        board_card = card(right, fill="x")
        tk.Label(board_card, textvariable=self.match_var, bg=CARD, fg=INK,
                 font=Fonts.get(13, "bold")).pack(anchor="w", padx=16, pady=(14, 8))
        self.mini = tk.Canvas(board_card, width=220, height=220, bg=CARD, highlightthickness=0)
        self.mini.pack(padx=16)
        self.score_label = tk.Label(board_card, text="", bg=CARD, fg=SOFT,
                                    font=Fonts.get(11), justify="left")
        self.score_label.pack(anchor="w", padx=16, pady=(8, 14))

        reset_card = card(right, fill="x", pady=(14, 0))
        mode_card = card(left)
        mode_card.pack(fill="x", pady=(14, 0), before=log_card)
        mode_row = tk.Frame(mode_card, bg=CARD)
        mode_row.pack(fill="x", padx=18, pady=12)
        mode_words = tk.Frame(mode_row, bg=CARD)
        mode_words.pack(side="left", fill="x", expand=True)
        tk.Label(mode_words, text="Game mode", bg=CARD, fg=INK,
                 font=Fonts.get(13, "bold")).pack(anchor="w")
        tk.Label(mode_words, text="Night Escape: 10x10 in the dark, a key,\nstep-and-grab, footprints and items.",
                 bg=CARD, fg=SOFT, font=Fonts.get(11), justify="left").pack(anchor="w")
        modes = tk.Frame(mode_row, bg=CARD)
        modes.pack(side="right")
        self.mode_buttons = {}
        for mode in MODES:
            button = PillButton(modes, MODE_NAMES[mode], command=lambda m=mode: self.server.set_mode(m),
                                width=108, height=34, font_size=11)
            button.pack(side="left", padx=(0, 4) if mode == MODES[0] else 0)
            self.mode_buttons[mode] = button
        self._show_mode("classic")
        tk.Label(reset_card, text="Start over", bg=CARD, fg=INK,
                 font=Fonts.get(13, "bold")).pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(reset_card, text="New map, new roles,\nboth scores back to 0.", bg=CARD,
                 fg=SOFT, font=Fonts.get(11), justify="left").pack(anchor="w", padx=16)
        PillButton(reset_card, "Swap sides next match", command=self.server.swap_sides,
                   color=INK, width=220, height=36, font_size=11).pack(padx=16, pady=(10, 0))
        PillButton(reset_card, "Reset game & scores", command=self.confirm_reset,
                   color=BAD, width=220, height=40).pack(padx=16, pady=(8, 14))

    # ----- events from network threads ------------------------------------

    def _queue(self, kind: str, value: object) -> None:
        with self.events_lock:
            self.events.append((kind, value))

    def _drain(self) -> None:
        with self.events_lock:
            events, self.events = self.events, []
        latest_state = None
        for kind, value in events:
            if kind == "log":
                self._append_log(str(value))
            elif kind == "state":
                latest_state = value
            elif kind == "link":
                self._show_link(str(value))
            elif kind == "link_problem":
                self._show_link_problem(str(value))
        if latest_state is not None:
            self._show_state(latest_state)
        if not self.server.stop_event.is_set():
            self.root.after(100, self._drain)

    def _show_state(self, state: dict) -> None:
        clients = state.get("connected_clients", [])
        self.count_var.set(str(len(clients)))

        for child in self.people_list.winfo_children():
            child.destroy()
        if not clients:
            tk.Label(self.people_list, text="Nobody yet. Start a client to join.",
                     bg=CARD, fg=FAINT, font=Fonts.get(11)).pack(anchor="w")
        roles = {p["id"]: p for p in state.get("players", [])}
        for client in clients:
            row = tk.Frame(self.people_list, bg=CARD)
            row.pack(fill="x", pady=3)
            player = roles.get(client["id"])
            dot = GOOD if client.get("joined") else FAINT
            tk.Label(row, text="●", bg=CARD, fg=dot, font=Fonts.get(12)).pack(side="left")
            tk.Label(row, text=client["nickname"], bg=CARD, fg=INK,
                     font=Fonts.get(12, "bold")).pack(side="left", padx=(6, 8))
            extra = "choosing a name..." if not client.get("joined") else (
                (player or {}).get("role") or "waiting")
            if player and player.get("side", "any") != "any":
                extra += "  ·  wants %s" % SIDE_NAMES[player["side"]]
            tk.Label(row, text=extra, bg=CARD, fg=SOFT, font=Fonts.get(11)).pack(side="left")
            tk.Label(row, text="%s  ·  %s" % (client.get("address", ""), client.get("tls", "")),
                     bg=CARD, fg=FAINT, font=Fonts.get(10)).pack(side="right")

        status = state.get("status")
        number = state.get("match_number", 0)
        self.match_var.set({
            "waiting": "Waiting for two players",
            "playing": "Match %d is on" % number,
            "finished": "Match %d is over" % number,
        }.get(status, "Waiting"))
        players = state.get("players", [])
        scores = "   ".join("%s  %d" % (p["nickname"], p["score"]) for p in players)
        if status == "finished" and state.get("winner_name"):
            scores += "\n%s won this one." % state["winner_name"]
        self.score_label.configure(text=scores or "No scores yet.")
        self._show_mode(state.get("mode", "classic"))
        self._draw_mini(state)

    def _show_mode(self, current: str) -> None:
        for mode, button in self.mode_buttons.items():
            button.configure_button(color=(NIGHT_CHIP if mode == "night" else INK)
                                    if mode == current else "#cbbfa8")

    def _draw_mini(self, state: dict) -> None:
        c = self.mini
        c.delete("all")
        board = state.get("board") or []
        if state.get("status") == "waiting" or not board:
            c.create_text(110, 110, text="The map appears\nonce two players join.",
                          fill=FAINT, font=Fonts.get(12), justify="center")
            return
        size = len(board)
        gap, cell = 4, (220 - 4 * (size + 1)) / size
        night = state.get("night") or {}
        crates = {(x[0], x[1]): x[2] for boxes in (night.get("boxes") or {}).values() for x in boxes}
        barricades = {(r, c) for r, c, _ in night.get("barricades", [])}
        key = tuple(night["key"]) if night.get("key") else None
        lantern = tuple(night["lantern"]) if night.get("lantern") else None
        gap = 4 if size <= 6 else 2
        cell = (220 - gap * (size + 1)) / size
        for r, line in enumerate(board):
            for col, tile in enumerate(line):
                x1, y1 = gap + col * (cell + gap), gap + r * (cell + gap)
                x2, y2 = x1 + cell, y1 + cell
                cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                if tile == "#":
                    draw_wall(c, x1, y1, x2, y2)
                    continue
                lit = lantern is None or max(abs(lantern[0] - r), abs(lantern[1] - col)) <= 1
                c.create_rectangle(x1, y1, x2, y2, outline=LINE,
                                   fill=YARD if not night else ("#71674a" if lit else NIGHT_YARD))
                if (r, col) in crates:
                    ITEM_DRAW[crates[(r, col)]](c, cx, cy, cell * 0.8)
                if (r, col) == key:
                    draw_key(c, cx, cy, cell)
                if (r, col) in barricades:
                    draw_barrier(c, cx, cy, cell)
                if tile == "T":
                    draw_tunnel(c, cx, cy, cell)
                elif tile == "W":
                    draw_warder(c, cx, cy, cell * 0.9)
                elif tile == "P":
                    draw_prisoner(c, cx, cy, cell * 0.9)
                elif tile == "X":
                    draw_warder(c, cx, cy, cell * 0.9)

    def _append_log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, time.strftime("%H:%M  "), "time")
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def confirm_reset(self) -> None:
        if messagebox.askyesno(
            "Reset the game?",
            "This clears both scores and starts a brand new match.",
            parent=self.root,
        ):
            self.server.reset_game()

    def _show_link(self, public_url: str) -> None:
        self.invite = self.web.invite_link(public_url)
        self.link_label.configure(text=self.invite, fg=INK)
        self.copy_button.configure_button(enabled=True)
        self._append_log("Invite link is live: %s" % public_url)
        self._show_qr(self.invite)

    def _show_link_problem(self, text: str) -> None:
        self.link_label.configure(text=text, fg=BAD)
        self._append_log(text)

    def _show_qr(self, text: str) -> None:
        try:
            import qrcode
            from PIL import ImageTk
        except ImportError:
            return  # optional: pip install qrcode
        image = qrcode.make(text, box_size=3, border=2).get_image().convert("RGB")
        self.qr_image = ImageTk.PhotoImage(image)
        self.qr_label.configure(image=self.qr_image)

    def copy_link(self) -> None:
        if self.invite:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.invite)
            self.copy_button.configure_button(text="Copied!")
            self.root.after(1500, lambda: self.copy_button.configure_button(text="Copy link"))

    def shutdown(self) -> None:
        if self.tunnel:
            self.tunnel.stop()
        if self.web:
            self.web.stop()
        self.server.stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    ServerDashboard(root)
    root.mainloop()


if __name__ == "__main__":
    main()
