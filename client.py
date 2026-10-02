"""Escape Plan player window.

Run ``python3 client.py``. The server address lives in config.py and the
server's certificate in certs/server.crt, so players only type a nickname.
"""

from __future__ import annotations

import queue
import ssl
import threading
import time
import tkinter as tk
from typing import Optional

import security
from ai import suggest_direction, suggest_move
from config import SERVER_HOST, SERVER_PORT
from protocol import PING_EVERY, Channel, open_tls
from theme import (
    BAD, CARD, FAINT, GOOD, INK, LANTERN, LANTERN_HOVER, LINE, NIGHT_BG, NIGHT_CHIP,
    NIGHT_CHIP_TEXT, NIGHT_YARD, NIGHT_YARD_ALT, PAPER, PRISONER, SOFT, SUN, SUN_SOFT,
    WARDER, YARD, YARD_DARK, ZONE, Fonts, PillButton, card, draw_barrier, draw_crate,
    draw_key, draw_lock, draw_prints, draw_prisoner, draw_reticle, draw_searchlight, draw_smoke,
    draw_sprint, draw_tunnel, draw_wall, draw_warder, rounded_rect,
)

ITEM_INFO = {
    "searchlight": ("Searchlight", "Lights up the whole yard for 2 of your turns.", draw_searchlight),
    "barricade": ("Barricade", "Block a square within 2 of you for 3 rounds.", draw_barrier),
    "smoke": ("Smoke bomb", "Invisible, no footprints, can't be grabbed for 3 moves.", draw_smoke),
    "sprint": ("Sprint", "Take 2 steps this turn.", draw_sprint),
}


BOARD_PX = 500
STEPS = {"up": (-1, 0), "down": (1, 0), "left": (0, -1), "right": (0, 1)}
DIAGONALS = {"up-left": (-1, -1), "up-right": (-1, 1), "down-left": (1, -1), "down-right": (1, 1)}
KEYS = {
    "w": "up", "a": "left", "s": "down", "d": "right",
    "Up": "up", "Left": "left", "Down": "down", "Right": "right",
    "q": "up-left", "e": "up-right", "z": "down-left", "c": "down-right",
}


def ring(a, b) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


class EscapeClient:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        Fonts.load(root)
        root.title("Escape Plan")
        root.geometry("1100x780")
        root.minsize(1000, 760)
        root.configure(bg=PAPER)
        root.protocol("WM_DELETE_WINDOW", self.close)

        self.inbox: "queue.Queue[dict]" = queue.Queue()
        self.channel: Optional[Channel] = None
        self.connected = False
        self.tls_version = ""
        self.nickname = ""
        self.player_id: Optional[str] = None
        self.state: Optional[dict] = None
        self.in_game = False
        self.rematch_sent = False
        self.last_match = 0
        self.last_turn: Optional[str] = None
        self.deadline: Optional[float] = None
        self.hover_cell: Optional[tuple[int, int]] = None
        self.hint_direction: Optional[str] = None
        self.placing = False
        self.feed: list[str] = []

        self._build_login()
        root.bind("<KeyPress>", self._on_key)
        root.after(80, self._drain_inbox)
        root.after(100, self._tick)
        self._connect()

    # ======================================================================
    # Connection
    # ======================================================================

    def _connect(self) -> None:
        self._set_status("Connecting to the game server...", FAINT)
        threading.Thread(target=self._network_thread, daemon=True).start()

    def _network_thread(self) -> None:
        try:
            context = security.client_context()
            conn = open_tls(SERVER_HOST, SERVER_PORT, context)
            security.check_pinned(conn)
        except security.CertificateMissing as error:
            self.inbox.put({"type": "offline", "message": str(error)})
            return
        except ssl.SSLError:
            self.inbox.put({"type": "offline", "message":
                            "The server's certificate doesn't match certs/server.crt, "
                            "so we didn't connect. Copy the current server.crt from the server."})
            return
        except OSError:
            self.inbox.put({"type": "offline", "message":
                            "Can't reach the server at %s:%d. Is it running?"
                            % (SERVER_HOST, SERVER_PORT)})
            return

        channel = Channel(
            conn,
            on_message=self.inbox.put,
            on_close=lambda reason: self.inbox.put(
                {"type": "offline", "message": "Lost the connection to the server."}),
            ping_every=PING_EVERY,
        )
        self.channel = channel
        channel.run()

    def _send(self, payload: dict) -> None:
        if self.channel is not None and self.connected:
            self.channel.send(payload)

    def _drain_inbox(self) -> None:
        try:
            while True:
                self._handle(self.inbox.get_nowait())
        except queue.Empty:
            pass
        if self.root.winfo_exists():
            self.root.after(60, self._drain_inbox)

    def _handle(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "hello":
            self.connected = True
            self.tls_version = str(message.get("tls", "TLS"))
            self._set_status("Connected securely (%s)" % self.tls_version, GOOD)
            self._show_lobby(message.get("online", []), message.get("room_full", False))
            self.join_button.configure_button(enabled=True)
            self.name_entry.focus_set()
        elif kind == "lobby":
            if not self.in_game:
                self._show_lobby(message.get("online", []), message.get("room_full", False))
        elif kind == "welcome":
            self.nickname = message.get("nickname", self.nickname)
            self._build_game()
        elif kind == "state":
            if not self.in_game:
                self._build_game()
            self.player_id = message.get("you", self.player_id)
            if message.get("message"):
                self._add_feed(message["message"])
            self._apply_state(message.get("state", {}))
        elif kind == "error":
            text = message.get("message", "The server said no.")
            if self.in_game:
                self._add_feed(text)
                self._flash_board()
            else:
                self._set_status(text, BAD)
                self.join_button.configure_button(enabled=self.connected, text="Join the game")
                self.name_entry.configure(state="normal")
        elif kind == "offline":
            self.connected = False
            self.channel = None
            text = message.get("message", "Disconnected.")
            if self.in_game:
                self._build_login()
            self._set_status(text, BAD)
            self.join_button.configure_button(enabled=True, text="Try again", color=INK)
            self.join_button.command = self._retry

    def _retry(self) -> None:
        self.join_button.configure_button(enabled=False, text="Join the game")
        self.join_button.command = self.join
        self._connect()

    # ======================================================================
    # Name screen
    # ======================================================================

    def _build_login(self) -> None:
        self._clear()
        self.in_game = False
        self.state = None
        outer = tk.Frame(self.root, bg=PAPER)
        outer.pack(fill="both", expand=True)

        box = card(outer)
        box.place(relx=0.5, rely=0.5, anchor="center")
        inner = tk.Frame(box, bg=CARD, padx=40, pady=30)
        inner.pack()

        art = tk.Canvas(inner, width=380, height=150, bg=CARD, highlightthickness=0)
        art.pack()
        rounded_rect(art, 4, 96, 376, 146, radius=18, fill=YARD, outline="")
        draw_wall(art, 14, 40, 84, 132)
        draw_warder(art, 150, 92, 92)
        draw_prisoner(art, 250, 92, 92)
        draw_tunnel(art, 330, 118, 70)
        art.create_text(200, 16, text="catch  or  escape", fill=FAINT, font=Fonts.get(11))

        tk.Label(inner, text="Escape Plan", bg=CARD, fg=INK,
                 font=Fonts.get(30, "bold")).pack(pady=(14, 0))
        tk.Label(inner, text="One of you guards the yard. The other one digs out.",
                 bg=CARD, fg=SOFT, font=Fonts.get(12)).pack(pady=(2, 22))

        tk.Label(inner, text="What should we call you?", bg=CARD, fg=INK,
                 font=Fonts.get(12, "bold")).pack(anchor="w")
        entry_wrap = tk.Frame(inner, bg=CARD, highlightthickness=2,
                              highlightbackground=LINE, highlightcolor=SUN)
        entry_wrap.pack(fill="x", pady=(6, 14))
        self.name_entry = tk.Entry(
            entry_wrap, bg=CARD, fg=INK, insertbackground=INK, relief="flat",
            font=Fonts.get(15), borderwidth=0, highlightthickness=0,
        )
        self.name_entry.pack(fill="x", padx=10, pady=8)
        self.name_entry.insert(0, self.nickname)
        self.name_entry.bind("<Return>", lambda _e: self.join())

        self.join_button = PillButton(inner, "Join the game", command=self.join,
                                      color=PRISONER, width=380, height=46, font_size=13)
        self.join_button.pack()
        self.join_button.configure_button(enabled=self.connected)

        self.status_label = tk.Label(inner, text="", bg=CARD, fg=FAINT, font=Fonts.get(11),
                                     wraplength=380, justify="center")
        self.status_label.pack(pady=(16, 2))
        self.lobby_label = tk.Label(inner, text="", bg=CARD, fg=SOFT, font=Fonts.get(11),
                                    wraplength=380, justify="center")
        self.lobby_label.pack()

    def _set_status(self, text: str, color: str) -> None:
        if not self.in_game and hasattr(self, "status_label"):
            self.status_label.configure(text="●  " + text, fg=color)

    def _show_lobby(self, online: list, room_full: bool) -> None:
        if room_full:
            text = "%s are already playing. You can wait here until a spot opens." % " and ".join(online)
        elif online:
            text = "%s is here and waiting for you." % online[0]
        else:
            text = "Nobody's here yet, so you'll be first."
        self.lobby_label.configure(text=text)

    def join(self) -> None:
        name = " ".join(self.name_entry.get().split())[:16]
        if not self.connected:
            return
        if not name:
            self._set_status("Type a nickname first.", BAD)
            return
        self.nickname = name
        self._send({"type": "join", "nickname": name})
        self.join_button.configure_button(enabled=False, text="Joining...")
        self.name_entry.configure(state="disabled")

    # ======================================================================
    # Game screen
    # ======================================================================

    def _build_game(self) -> None:
        if self.in_game:
            return
        self._clear()
        self.in_game = True
        self.feed = []
        self.last_match = 0
        self.last_turn = None

        page = tk.Frame(self.root, bg=PAPER)
        page.pack(fill="both", expand=True, padx=26, pady=18)

        top = tk.Frame(page, bg=PAPER)
        top.pack(fill="x")
        tk.Label(top, text="Welcome, %s." % self.nickname, bg=PAPER, fg=INK,
                 font=Fonts.get(24, "bold")).pack(side="left")
        chip = tk.Frame(top, bg=SUN_SOFT)
        chip.pack(side="right")
        tk.Label(chip, text="Secure connection · %s" % self.tls_version, bg=SUN_SOFT,
                 fg=INK, font=Fonts.get(11, "bold"), padx=12, pady=5).pack()
        self.mode_chip = tk.Label(top, text="Night Escape", bg=NIGHT_CHIP, fg=NIGHT_CHIP_TEXT,
                                  font=Fonts.get(11, "bold"), padx=12, pady=5)
        self.subtitle = tk.Label(page, text="", bg=PAPER, fg=SOFT, font=Fonts.get(12),
                                 wraplength=1000, justify="left")
        self.subtitle.pack(anchor="w", pady=(0, 12))

        body = tk.Frame(page, bg=PAPER)
        body.pack(fill="both", expand=True)
        left = tk.Frame(body, bg=PAPER)
        left.pack(side="left", fill="y")
        right = tk.Frame(body, bg=PAPER, width=330)
        right.pack(side="right", fill="both", expand=True, padx=(22, 0))

        self.chips = tk.Frame(left, bg=PAPER)
        self.chips.pack(fill="x", pady=(0, 12))

        self.board = tk.Canvas(left, width=BOARD_PX, height=BOARD_PX, bg=PAPER,
                               highlightthickness=0)
        self.board.pack()
        self.board.bind("<Motion>", self._on_board_hover)
        self.board.bind("<Leave>", lambda _e: self._set_hover(None))
        self.board.bind("<Button-1>", self._on_board_click)

        # Turn + timer
        turn = card(right, fill="x")
        turn_row = tk.Frame(turn, bg=CARD)
        turn_row.pack(fill="x", padx=16, pady=14)
        self.clock = tk.Canvas(turn_row, width=84, height=84, bg=CARD, highlightthickness=0)
        self.clock.pack(side="left")
        words = tk.Frame(turn_row, bg=CARD)
        words.pack(side="left", fill="x", expand=True, padx=(14, 0))
        self.turn_title = tk.Label(words, text="", bg=CARD, fg=INK, font=Fonts.get(18, "bold"),
                                   anchor="w")
        self.turn_title.pack(fill="x")
        self.turn_hint = tk.Label(words, text="", bg=CARD, fg=SOFT, font=Fonts.get(11),
                                  wraplength=200, justify="left", anchor="w")
        self.turn_hint.pack(fill="x")
        self.lockdown_label = tk.Label(words, text="", bg=CARD, fg=BAD, font=Fonts.get(11, "bold"),
                                       anchor="w")
        self.lockdown_label.pack(fill="x")

        # Scout AI
        scout = card(right, fill="x", pady=(12, 0))
        tk.Label(scout, text="Scout, your AI lookout", bg=CARD, fg=INK,
                 font=Fonts.get(13, "bold")).pack(anchor="w", padx=16, pady=(12, 0))
        self.scout_text = tk.Label(
            scout, text="Plans a shortest route for you. It only suggests.",
            bg=CARD, fg=SOFT, font=Fonts.get(11), wraplength=290, justify="left")
        self.scout_text.pack(anchor="w", padx=16, pady=(2, 8))
        self.scout_button = PillButton(scout, "Ask Scout", command=self.ask_scout,
                                       color=WARDER, width=290, height=38, font_size=12)
        self.scout_button.pack(padx=16, pady=(0, 14))

        # Night Escape item (shown only in that mode)
        self.item_card = card(right)
        tk.Label(self.item_card, text="Backpack", bg=CARD, fg=INK,
                 font=Fonts.get(13, "bold")).pack(anchor="w", padx=16, pady=(10, 0))
        self.key_label = tk.Label(self.item_card, text="", bg=CARD, fg=BAD,
                                  font=Fonts.get(11, "bold"), anchor="w")
        self.key_label.pack(fill="x", padx=16)
        self.item_rows = tk.Frame(self.item_card, bg=CARD)
        self.item_rows.pack(fill="x", padx=16)
        self.item_text = tk.Label(self.item_card, text="", bg=CARD, fg=SOFT, font=Fonts.get(10),
                                  wraplength=290, justify="left", anchor="w")
        self.item_text.pack(fill="x", padx=16, pady=(2, 10))
        self.scout_card = scout

        # Rematch
        self.rematch_button = PillButton(right, "Rematch", command=self.rematch,
                                         color=GOOD, width=320, height=46, font_size=13, bg=PAPER)
        self.rematch_button.pack(fill="x", pady=(12, 0))

        # Room + feed
        room = card(right, fill="both", expand=True, pady=(12, 0))
        tk.Label(room, text="In the room", bg=CARD, fg=INK,
                 font=Fonts.get(13, "bold")).pack(anchor="w", padx=16, pady=(12, 2))
        self.room_list = tk.Frame(room, bg=CARD)
        self.room_list.pack(fill="x", padx=16)
        tk.Frame(room, bg=LINE, height=1).pack(fill="x", padx=16, pady=10)
        self.feed_text = tk.Text(room, height=3, width=34, bg=CARD, fg=SOFT, borderwidth=0,
                                 highlightthickness=0, wrap="word", font=Fonts.get(11),
                                 state="disabled", spacing1=2, spacing3=2)
        self.feed_text.tag_configure("new", foreground=INK)
        self.feed_text.pack(fill="both", expand=True, padx=14, pady=(0, 12))

        self._draw_board()

    # ----- state -----------------------------------------------------------

    def _apply_state(self, state: dict) -> None:
        previous_turn = self.last_turn
        self.state = state
        match = state.get("match_number", 0)
        if match != self.last_match:
            self.last_match = match
            self.rematch_sent = False

        turn = state.get("turn_player_id") if state.get("status") == "playing" else None
        seconds = state.get("turn_seconds_left", 0)
        if turn is None:
            self.deadline = None
        elif turn != previous_turn or self.deadline is None or \
                abs((self.deadline - time.monotonic()) - seconds) > 1.0:
            self.deadline = time.monotonic() + seconds
        if turn != previous_turn:
            self.hint_direction = None
            self.placing = False
            if turn == self.player_id:
                self.root.bell()
        self.last_turn = turn

        night = state.get("mode") == "night"
        if night and not self.mode_chip.winfo_ismapped():
            self.mode_chip.pack(side="right", padx=(0, 8))
        elif not night and self.mode_chip.winfo_ismapped():
            self.mode_chip.pack_forget()
        if night and not self.item_card.winfo_ismapped():
            self.item_card.pack(fill="x", pady=(12, 0), after=self.scout_card)
        elif not night and self.item_card.winfo_ismapped():
            self.item_card.pack_forget()

        self._render_chips()
        self._render_turn()
        self._render_room()
        self._render_buttons()
        self._render_item()
        self._draw_board()

    def _me(self) -> dict:
        return self._player(self.player_id)

    def _player(self, player_id) -> dict:
        for player in (self.state or {}).get("players", []):
            if player.get("id") == player_id:
                return player
        return {}

    def _opponent(self) -> dict:
        for player in (self.state or {}).get("players", []):
            if player.get("id") != self.player_id:
                return player
        return {}

    def _my_turn(self) -> bool:
        return bool(self.state and self.state.get("status") == "playing"
                    and self.state.get("turn_player_id") == self.player_id)

    def _render_chips(self) -> None:
        for child in self.chips.winfo_children():
            child.destroy()
        players = sorted((self.state or {}).get("players", []),
                         key=lambda p: 0 if p.get("role") == "warder" else 1)
        if len(players) < 2:
            players = players + [{}]
        for index, player in enumerate(players[:2]):
            active = bool(player) and player.get("id") == (self.state or {}).get("turn_player_id")
            box = tk.Frame(self.chips, bg=CARD, highlightthickness=3 if active else 1,
                           highlightbackground=SUN if active else LINE)
            box.pack(side="left", fill="x", expand=True, padx=(0, 10) if index == 0 else (0, 0))
            avatar = tk.Canvas(box, width=52, height=52, bg=CARD, highlightthickness=0)
            avatar.pack(side="left", padx=(10, 6), pady=8)
            role = player.get("role")
            if role == "warder":
                draw_warder(avatar, 26, 26, 50)
            elif role == "prisoner":
                draw_prisoner(avatar, 26, 26, 50)
            else:
                avatar.create_oval(10, 10, 42, 42, outline=LINE, width=2, dash=(3, 3))
            text = tk.Frame(box, bg=CARD)
            text.pack(side="left", fill="x", expand=True)
            name = player.get("nickname", "Waiting for someone...")
            if player.get("id") == self.player_id:
                name += " (you)"
            tk.Label(text, text=name, bg=CARD, fg=INK if player else FAINT,
                     font=Fonts.get(13, "bold"), anchor="w").pack(fill="x")
            role_text = {"warder": "Warder", "prisoner": "Prisoner"}.get(role, "")
            if (self.state or {}).get("status") == "finished" and player:
                won = player.get("id") == self.state.get("winner_id")
                role_text += "  ·  " + ("Win" if won else "Lost")
            tk.Label(text, text=role_text, bg=CARD,
                     fg=WARDER if role == "warder" else PRISONER,
                     font=Fonts.get(11, "bold"), anchor="w").pack(fill="x")
            if player:
                tk.Label(box, text=str(player.get("score", 0)), bg=CARD, fg=INK,
                         font=Fonts.get(26, "bold")).pack(side="right", padx=14)

    def _render_turn(self) -> None:
        state = self.state or {}
        status = state.get("status")
        me, other = self._me(), self._opponent()
        role = me.get("role")
        if status == "waiting":
            self.subtitle.configure(text="Hang tight, the match starts as soon as a second player joins.")
            self.turn_title.configure(text="Waiting...")
            self.turn_hint.configure(text="Tell a friend to open the game on their computer.")
        elif status == "finished":
            won = state.get("winner_id") == self.player_id
            self.subtitle.configure(text="Match %d is over." % state.get("match_number", 0))
            self.turn_title.configure(text="Win" if won else "Lost", fg=GOOD if won else BAD)
            self.turn_hint.configure(text="Press Rematch if you want another round.")
            self.lockdown_label.configure(text="")
            return
        else:
            self.subtitle.configure(text="You're the %s. %s" % (role, self._goal(role)))
            if self._my_turn():
                self.turn_title.configure(text="Your move")
                if self.placing:
                    self.turn_hint.configure(text="Click an orange square for the barricade (Esc cancels).")
                elif state.get("night") and state["night"].get("sensed"):
                    self.turn_hint.configure(text="You hear someone right beside you in the smoke! "
                                                  "Click a red square to guess (a miss ends your turn).")
                elif state.get("night") and state["night"].get("lunge"):
                    self.turn_hint.configure(text="You spotted them! Click the target to grab before time runs out.")
                elif state.get("night") and role == "warder" and self._lock_on():
                    self.turn_hint.configure(text="The prisoner is in your light! Click the target to grab.")
                elif state.get("night") and role == "warder" and state["night"].get("smoke_turns"):
                    self.turn_hint.configure(text="They threw smoke. You can't see them unless they're right beside you.")
                else:
                    self.turn_hint.configure(text="Click a highlighted square, or use the arrow keys / WASD.")
            else:
                self.turn_title.configure(text="%s's move" % other.get("nickname", "Their"))
                self.turn_hint.configure(text="They have 10 seconds. Plan your next step.")
        self.turn_title.configure(fg=INK)
        night = state.get("night")
        if night and status == "playing":
            left = night.get("rounds_left", 0)
            self.lockdown_label.configure(text="Lockdown in %d rounds%s" % (left, "!" if left <= 5 else ""))
        else:
            self.lockdown_label.configure(text="")

    def _goal(self, role) -> str:
        if (self.state or {}).get("mode") == "night":
            if role == "warder":
                return ("It's dark. Your lantern shows the 8 squares around you. When the prisoner is in "
                        "your light a target locks on: click it to grab. If your step brings them into view "
                        "you get one extra click. Footprints show where they stood 2 turns ago.")
            return ("It's dark. Find the key, then reach the tunnel. The warder can step and then grab "
                    "anything next to them, so stay out of the red zone. You spot the warder from 2 squares away.")
        if role == "prisoner":
            return "Reach the tunnel before the warder catches you."
        return "Catch the prisoner before they reach the tunnel."

    def _render_item(self) -> None:
        night = (self.state or {}).get("night")
        if not night:
            return
        role = self._me().get("role")
        if role == "prisoner":
            has_key = night.get("has_key")
            self.key_label.configure(
                text="You have the key. The tunnel is open!" if has_key else "Find the key first. The tunnel is locked.",
                fg=GOOD if has_key else BAD)
        else:
            self.key_label.configure(text="")
        for child in self.item_rows.winfo_children():
            child.destroy()
        for item in night.get("my_items", []):
            name, blurb, draw = ITEM_INFO[item]
            row = tk.Frame(self.item_rows, bg=CARD)
            row.pack(fill="x", pady=2)
            art = tk.Canvas(row, width=34, height=34, bg=CARD, highlightthickness=0)
            art.pack(side="left")
            draw(art, 17, 17, 34)
            words = tk.Frame(row, bg=CARD)
            words.pack(side="left", fill="x", expand=True, padx=6)
            tk.Label(words, text=name, bg=CARD, fg=INK, font=Fonts.get(11, "bold"), anchor="w").pack(fill="x")
            tk.Label(words, text=blurb, bg=CARD, fg=SOFT, font=Fonts.get(9), anchor="w",
                     wraplength=170, justify="left").pack(fill="x")
            label = ("Cancel" if self.placing else "Place") if item == "barricade" else "Use"
            button = PillButton(row, label, command=lambda i=item: self.use_item(i),
                                color=INK, width=72, height=30, font_size=11)
            button.pack(side="right")
            button.configure_button(enabled=self._my_turn() and not (item == "sprint" and night.get("sprint_steps")))
        notes = []
        if night.get("my_boxes"):
            notes.append("Walk onto a glowing box to pick up the item shown. Only you can see your boxes.")
        if night.get("smoke_turns"):
            notes.append("Smoke is up for %d more prisoner moves." % night["smoke_turns"])
        if night.get("sprint_steps"):
            notes.append("Sprinting: one more step this turn!")
        if night.get("searchlight"):
            notes.append("Searchlight on for %d more warder turn(s)." % night["searchlight"])
        self.item_text.configure(text=" ".join(notes))

    def _render_room(self) -> None:
        for child in self.room_list.winfo_children():
            child.destroy()
        for client in (self.state or {}).get("connected_clients", []):
            row = tk.Frame(self.room_list, bg=CARD)
            row.pack(fill="x", pady=1)
            joined = client.get("joined", True)
            tk.Label(row, text="●", bg=CARD, fg=GOOD if joined else FAINT,
                     font=Fonts.get(10)).pack(side="left")
            label = client.get("nickname", "?")
            if client.get("id") == self.player_id:
                label += " (you)"
            if not joined:
                label += " is picking a name"
            tk.Label(row, text=label, bg=CARD, fg=INK if joined else SOFT,
                     font=Fonts.get(11)).pack(side="left", padx=6)

    def _render_buttons(self) -> None:
        state = self.state or {}
        finished = state.get("status") == "finished"
        voters = state.get("rematch_voters", [])
        if not finished:
            self.rematch_button.configure_button(enabled=False, text="Rematch")
        elif self.rematch_sent or self.player_id in voters:
            self.rematch_button.configure_button(enabled=False, text="Waiting for them to agree...")
        elif voters:
            self.rematch_button.configure_button(enabled=True, text="They want a rematch - you in?")
        else:
            self.rematch_button.configure_button(enabled=True, text="Rematch")
        self.scout_button.configure_button(enabled=self._my_turn())

    def _add_feed(self, text: str) -> None:
        self.feed.insert(0, text)
        del self.feed[6:]
        if not hasattr(self, "feed_text"):
            return
        self.feed_text.configure(state="normal")
        self.feed_text.delete("1.0", tk.END)
        for index, line in enumerate(self.feed):
            self.feed_text.insert(tk.END, line + "\n", "new" if index == 0 else ())
        self.feed_text.configure(state="disabled")

    # ----- board drawing ---------------------------------------------------

    def _cell_box(self, row: int, col: int, size: int) -> tuple[float, float, float, float]:
        gap = 7 if size <= 6 else 3
        cell = (BOARD_PX - gap * (size + 1)) / size
        x1 = gap + col * (cell + gap)
        y1 = gap + row * (cell + gap)
        return x1, y1, x1 + cell, y1 + cell

    def _lock_on(self) -> Optional[tuple[int, int]]:
        """The prisoner's square when the warder can click it to grab."""

        state = self.state or {}
        me, other = self._me(), self._opponent()
        if not state.get("night") or not self._my_turn() or me.get("role") != "warder":
            return None
        if me.get("row") is None or other.get("row") is None:
            return None
        cell = (other["row"], other["col"])
        return cell if ring((me["row"], me["col"]), cell) == 1 else None

    def _targets(self) -> dict[tuple[int, int], str]:
        """Squares you can click this turn: steps, or 'catch' / 'blind' grabs."""

        if not self._my_turn():
            return {}
        state, me, other = self.state, self._me(), self._opponent()
        board = state.get("board", [])
        night = state.get("night") or {}
        if me.get("row") is None:
            return {}
        result = {}
        lock = self._lock_on()
        if lock:
            result[lock] = "catch"
        if night and me.get("role") == "warder" and night.get("sensed"):
            # Someone is hiding right beside you in the smoke: guess. At the start of a turn the
            # straight squares stay walkable; after a step, all 8 are guesses.
            around = list(DIAGONALS.values()) + (list(STEPS.values()) if night.get("lunge") else [])
            for dr, dc in around:
                r, c = me["row"] + dr, me["col"] + dc
                if 0 <= r < len(board) and 0 <= c < len(board) and board[r][c] != "#":
                    result.setdefault((r, c), "blind")
        if night.get("lunge"):
            return result                      # after a step that spots them: grab only
        barricades = {(r, c) for r, c, _ in night.get("barricades", [])}
        for direction, (dr, dc) in STEPS.items():
            r, c = me["row"] + dr, me["col"] + dc
            if not (0 <= r < len(board) and 0 <= c < len(board)) or (r, c) in result:
                continue
            tile = board[r][c]
            if tile == "#" or (r, c) in barricades:
                continue
            if me.get("role") == "warder" and tile == "T":
                continue
            if me.get("role") == "prisoner" and tile == "T" and night and not night.get("has_key"):
                continue
            if me.get("role") == "prisoner" and (r, c) == (other.get("row"), other.get("col")):
                continue
            result[(r, c)] = direction
        return result

    def _placeable(self) -> set[tuple[int, int]]:
        """Squares where the barricade can go (within 2 of the warder)."""

        if not (self.placing and self._my_turn()):
            return set()
        me, board = self._me(), (self.state or {}).get("board", [])
        barricades = {(r, c) for r, c, _ in ((self.state or {}).get("night") or {}).get("barricades", [])}
        return {
            (r, c) for r in range(len(board)) for c in range(len(board))
            if board[r][c] not in "#TW" and (r, c) not in barricades
            and ring((me["row"], me["col"]), (r, c)) <= 2
        }

    def _draw_board(self) -> None:
        canvas = self.board
        canvas.delete("all")
        state = self.state or {}
        night = state.get("night")
        board = state.get("board") or []
        size = len(board) or 5
        rounded_rect(canvas, 0, 0, BOARD_PX, BOARD_PX, radius=22 if size <= 6 else 14,
                     fill=NIGHT_BG if night else YARD_DARK, outline="")
        if state.get("status") == "waiting" or not board:
            draw_tunnel(canvas, BOARD_PX / 2, BOARD_PX / 2 - 30, 140)
            canvas.create_text(BOARD_PX / 2, BOARD_PX / 2 + 70, text="The yard opens when\nyour opponent arrives.",
                               fill=SOFT, font=Fonts.get(15), justify="center")
            return

        targets = {} if self.placing else self._targets()
        placeable = self._placeable()
        me = self._me()
        i_am_prisoner = me.get("role") == "prisoner"
        mine = (me["row"], me["col"]) if me.get("row") is not None else None
        hint_cell = None
        if self.hint_direction and mine:
            dr, dc = STEPS.get(self.hint_direction) or DIAGONALS[self.hint_direction]
            hint_cell = (mine[0] + dr, mine[1] + dc)
        last = state.get("last_move") or {}
        night = night or {}
        lantern = tuple(night["lantern"]) if night.get("lantern") else None
        boxes = {(r, c): item for r, c, item in night.get("my_boxes", [])}
        barricades = {(r, c) for r, c, _ in night.get("barricades", [])}
        key = tuple(night["key"]) if night.get("key") else None
        prints = {(r, c): age for r, c, age in night.get("footprints", [])}
        vision = night.get("prisoner_vision", 2)
        radius = 12 if size <= 6 else 6
        small = size > 6

        for r, line in enumerate(board):
            for c, tile in enumerate(line):
                x1, y1, x2, y2 = self._cell_box(r, c, size)
                cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                cell = x2 - x1
                lit = bool(night) and (night.get("searchlight") or
                                       (lantern is not None and ring(lantern, (r, c)) <= 1))
                if tile == "#":
                    draw_wall(canvas, x1, y1, x2, y2, dim=bool(night) and not lit)
                    continue
                if night:
                    seen = i_am_prisoner and mine is not None and ring(mine, (r, c)) <= vision
                    fill = LANTERN if lit else ("#3a4468" if seen else
                                                (NIGHT_YARD if (r + c) % 2 == 0 else NIGHT_YARD_ALT))
                    if (r, c) in targets and (r, c) == self.hover_cell:
                        fill = LANTERN_HOVER
                    if (r, c) in placeable and (r, c) == self.hover_cell:
                        fill = "#8a6a3a"
                else:
                    fill = YARD if (r + c) % 2 == 0 else "#efe4cf"
                    if (r, c) in targets and (r, c) == self.hover_cell:
                        fill = SUN_SOFT
                rounded_rect(canvas, x1, y1, x2, y2, radius=radius, fill=fill, outline="")
                # Prisoner's red zone: everywhere the warder could step-and-grab next turn.
                if night and i_am_prisoner and lantern and ring(lantern, (r, c)) <= 2 and tile != "W":
                    rounded_rect(canvas, x1 + 3, y1 + 3, x2 - 3, y2 - 3, radius=max(3, radius - 3),
                                 fill="", outline=ZONE, width=2, dash=(4, 3))
                if (r, c) in placeable:
                    rounded_rect(canvas, x1 + 2, y1 + 2, x2 - 2, y2 - 2, radius=max(3, radius - 2),
                                 fill="", outline="#e0a24f", width=3)
                if (r, c) in targets:
                    kind = targets[(r, c)]
                    grab = kind in ("catch", "blind")
                    rounded_rect(canvas, x1 + 2, y1 + 2, x2 - 2, y2 - 2, radius=max(3, radius - 2),
                                 fill="", outline=BAD if grab else SUN, width=3)
                    if kind == "blind":
                        canvas.create_text(cx, y2 - 8, text="Grab?", fill="#ffb3a4",
                                           font=Fonts.get(8 if small else 10, "bold"))
                if (r, c) in boxes:
                    # A glowing box showing the item inside.
                    canvas.create_oval(cx - cell * 0.36, cy - cell * 0.36, cx + cell * 0.36, cy + cell * 0.36,
                                       outline=SUN, width=2, dash=(3, 2))
                    ITEM_INFO[boxes[(r, c)]][2](canvas, cx, cy, cell * 0.78)
                if (r, c) == key:
                    draw_key(canvas, cx, cy, cell)
                if (r, c) in barricades:
                    draw_barrier(canvas, cx, cy, cell)
                if (r, c) in prints and tile != "P":
                    draw_prints(canvas, cx, cy, cell)
                    if i_am_prisoner:
                        canvas.create_text(cx, y2 - 6, text="warder sees", fill="#d9cfb8",
                                           font=Fonts.get(7 if small else 9, "bold"))
                if last.get("from") == [r, c]:
                    mover = self._player(last.get("player_id")).get("role")
                    tone = WARDER if mover == "warder" else PRISONER
                    dot = 3 if small else 4
                    for dx, dy in ((-0.14, 0.08), (0.14, -0.08)):
                        canvas.create_oval(cx + cell * dx - dot, cy + cell * dy - dot * 1.5,
                                           cx + cell * dx + dot, cy + cell * dy + dot * 1.5,
                                           fill=tone, outline="")
                if tile == "T":
                    draw_tunnel(canvas, cx, cy, cell)
                    if night and i_am_prisoner and not night.get("has_key"):
                        draw_lock(canvas, x2 - cell * 0.2, y1 + cell * 0.25, cell)
                elif tile == "W":
                    draw_warder(canvas, cx, cy, cell * 0.92)
                elif tile == "P":
                    draw_prisoner(canvas, cx, cy, cell * 0.92)
                    if night.get("smoke_turns"):
                        draw_smoke(canvas, cx, cy, cell)
                elif tile == "X":
                    draw_prisoner(canvas, cx - cell * 0.14, cy, cell * 0.8)
                    draw_warder(canvas, cx + cell * 0.14, cy, cell * 0.8)
                if targets.get((r, c)) == "catch":
                    draw_reticle(canvas, cx, cy, cell)
                if (r, c) == hint_cell:
                    canvas.create_text(cx, y1 + (7 if small else 12), text="Scout", fill="#9dbbf0",
                                       font=Fonts.get(8 if small else 10, "bold"))
                    rounded_rect(canvas, x1 + 2, y1 + 2, x2 - 2, y2 - 2, radius=max(3, radius - 2),
                                 fill="", outline=WARDER, width=3, dash=(6, 4))

        if state.get("status") == "finished":
            self._draw_result(state)

    def _draw_result(self, state: dict) -> None:
        canvas = self.board
        won = state.get("winner_id") == self.player_id
        me, other = self._me(), self._opponent()
        reason = state.get("finish_reason")
        if reason == "lockdown":
            line = ("Lockdown! The prison sealed before they got out." if won
                    else "Lockdown! The prison sealed before you got out.")
        elif reason == "escaped":
            line = "You made it out through the tunnel." if won else "%s escaped through the tunnel." % other.get("nickname")
        else:
            line = "You caught %s." % other.get("nickname") if won else "%s caught you." % other.get("nickname")
        x1, y1, x2, y2 = 70, 150, BOARD_PX - 70, BOARD_PX - 150
        rounded_rect(canvas, x1 + 4, y1 + 8, x2 + 4, y2 + 8, radius=24, fill="#d8c8aa", outline="")
        rounded_rect(canvas, x1, y1, x2, y2, radius=24, fill=CARD, outline=LINE, width=2)
        canvas.create_text(BOARD_PX / 2, y1 + 48, text="Win" if won else "Lost",
                           fill=GOOD if won else BAD, font=Fonts.get(36, "bold"))
        canvas.create_text(BOARD_PX / 2, y1 + 96, text=line, fill=INK, font=Fonts.get(13))
        canvas.create_text(
            BOARD_PX / 2, y1 + 132,
            text="%s  %d  -  %d  %s" % (me.get("nickname", "You"), me.get("score", 0),
                                         other.get("score", 0), other.get("nickname", "")),
            fill=SOFT, font=Fonts.get(14, "bold"))
        canvas.create_text(BOARD_PX / 2, y2 - 22, text="Winner plays warder next round.",
                           fill=FAINT, font=Fonts.get(11))

    def _flash_board(self) -> None:
        self.board.create_rectangle(2, 2, BOARD_PX - 2, BOARD_PX - 2, outline=BAD, width=4, tags="flash")
        self.root.after(250, lambda: self.board.delete("flash") if self.in_game else None)

    def _draw_clock(self) -> None:
        c = self.clock
        c.delete("all")
        c.create_oval(6, 6, 78, 78, outline=LINE, width=8)
        if self.deadline is None:
            c.create_text(42, 42, text="--", fill=FAINT, font=Fonts.get(18, "bold"))
            return
        left = max(0.0, self.deadline - time.monotonic())
        share = min(1.0, left / 10.0)
        tone = BAD if left <= 3 else (PRISONER if left <= 6 else GOOD)
        if share > 0:
            c.create_arc(6, 6, 78, 78, start=90, extent=-359.9 * share, style="arc",
                         outline=tone, width=8)
        c.create_text(42, 42, text=str(int(left + 0.999)), fill=INK, font=Fonts.get(22, "bold"))

    def _tick(self) -> None:
        if self.in_game:
            self._draw_clock()
        if self.root.winfo_exists():
            self.root.after(100, self._tick)

    # ----- input -----------------------------------------------------------

    def _cell_at(self, x: float, y: float) -> Optional[tuple[int, int]]:
        board = (self.state or {}).get("board") or []
        for r in range(len(board)):
            for c in range(len(board)):
                x1, y1, x2, y2 = self._cell_box(r, c, len(board))
                if x1 <= x <= x2 and y1 <= y <= y2:
                    return r, c
        return None

    def _set_hover(self, cell) -> None:
        if cell != self.hover_cell:
            self.hover_cell = cell
            self._draw_board()
        clickable = cell in self._placeable() if self.placing else cell in self._targets()
        self.board.configure(cursor="hand2" if clickable else "")

    def _on_board_hover(self, event) -> None:
        self._set_hover(self._cell_at(event.x, event.y))

    def _on_board_click(self, event) -> None:
        cell = self._cell_at(event.x, event.y)
        if self.placing:
            if cell in self._placeable():
                self.placing = False
                self._send({"type": "item", "item": "barricade", "target": list(cell)})
            return
        direction = self._targets().get(cell)
        if direction in ("catch", "blind"):
            self._send({"type": "catch", "target": list(cell)})
        elif direction:
            self.move(direction)

    def _on_key(self, event) -> None:
        if not self.in_game:
            return
        if event.keysym == "Escape" and self.placing:
            self.placing = False
            self._render_item()
            self._draw_board()
            return
        direction = KEYS.get(event.keysym) or KEYS.get((event.char or "").lower())
        if direction:
            self.move(direction)

    def move(self, direction: str) -> None:
        if self._my_turn():
            self._send({"type": "move", "direction": direction})

    def rematch(self) -> None:
        if (self.state or {}).get("status") == "finished" and not self.rematch_sent:
            self.rematch_sent = True
            self._send({"type": "rematch"})
            self._render_buttons()

    def use_item(self, item: str) -> None:
        if not self._my_turn():
            return
        if item == "barricade":
            self.placing = not self.placing
            self._render_item()
            self._draw_board()
            return
        self._send({"type": "item", "item": item})

    def ask_scout(self) -> None:
        if self.state is None or self.player_id is None:
            return
        self.hint_direction = suggest_direction(self.state, self.player_id)
        self.scout_text.configure(text=suggest_move(self.state, self.player_id) or "Scout has no idea.")
        self._draw_board()

    # ----- misc ------------------------------------------------------------

    def _clear(self) -> None:
        for child in self.root.winfo_children():
            child.destroy()

    def close(self) -> None:
        if self.channel is not None:
            self._send({"type": "quit"})
            channel = self.channel
            self.root.after(150, channel.close)
            self.root.after(200, self.root.destroy)
        else:
            self.root.destroy()


def main() -> None:
    root = tk.Tk()
    EscapeClient(root)
    root.mainloop()


if __name__ == "__main__":
    main()
