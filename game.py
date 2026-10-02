"""Server-authoritative game rules for Escape Plan.

Two modes:

* ``classic`` is the assignment's game: 5x5, 5 walls, 1 tunnel.
* ``night`` ("Night Escape") is a bigger, darker variant tuned by simulation:
  10x10 yard, limited vision for both sides, a key that unlocks the tunnel,
  the warder's step-and-grab (clicked by hand), footprints, private item boxes
  and a lockdown.

If a player doesn't move within 10 seconds, they take a random step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil
from random import Random
import time
import unicodedata

from solver import prisoner_forced_wins


TURN_SECONDS = 10
NICKNAME_MAX = 16

MODES = ("classic", "night")
MODE_NAMES = {"classic": "Classic", "night": "Night Escape"}
BOARD_SIZES = {"classic": 5, "night": 10}
WALL_COUNTS = {"classic": 5, "night": 20}      # 20% of the board either way

# Kept for older imports: the Classic board.
BOARD_SIZE = BOARD_SIZES["classic"]
OBSTACLE_COUNT = WALL_COUNTS["classic"]

# Classic starting spots are picked so nobody wins on the very first move.
MIN_START_GAP = 3
MIN_PRISONER_TO_TUNNEL = 3

# Night Escape tuning (see README for the simulation results)
LANTERN_RANGE = 1        # warder sees and catches within the 8 squares around them
PRISONER_VISION = 2      # prisoner sees the warder one square further
FOOTPRINT_AGE = 2        # the warder sees where the prisoner stood 2 turns ago, anywhere
SEARCHLIGHT_TURNS = 2    # the searchlight lights the yard for 2 warder turns
SMOKE_TURNS = 3          # smoke hides the prisoner for 3 of their moves
BARRICADE_ROUNDS = 3
BARRICADE_RANGE = 2      # barricades go on a square within 2 of the warder
BOXES_PER_PLAYER = 2     # 4 boxes on the map in total; each player only sees their own
PRISONER_START_ITEMS = ("smoke",)   # the prisoner opens every match with a smoke bomb
MAX_ITEMS = 2
LOCKDOWN_ROUNDS = 60
NIGHT_START_GAP = 6
NIGHT_WARDER_TO_TUNNEL = 3   # no camping on the tunnel from the first move
ITEMS_FOR_ROLE = {"warder": ("searchlight", "barricade"), "prisoner": ("smoke", "sprint")}
ITEM_NAMES = {"searchlight": "Searchlight", "barricade": "Barricade",
              "smoke": "Smoke bomb", "sprint": "Sprint"}


DIRECTIONS = {
    "up": (-1, 0),
    "w": (-1, 0),
    "down": (1, 0),
    "s": (1, 0),
    "left": (0, -1),
    "a": (0, -1),
    "right": (0, 1),
    "d": (0, 1),
}

DIAGONALS = {
    "up-left": (-1, -1),
    "up-right": (-1, 1),
    "down-left": (1, -1),
    "down-right": (1, 1),
}

Cell = tuple[int, int]


def direction_name(delta: tuple[int, int]) -> str:
    return {(-1, 0): "up", (1, 0): "down", (0, -1): "left", (0, 1): "right"}[delta]


def clean_nickname(raw: object) -> str:
    """Keep printable characters only, squash spaces, and cap the length."""

    text = unicodedata.normalize("NFC", str(raw))
    text = "".join(
        char for char in text if unicodedata.category(char)[0] not in {"C", "Z"} or char == " "
    )
    return " ".join(text.split())[:NICKNAME_MAX]


def ring_distance(a: Cell, b: Cell) -> int:
    """Squares apart counting diagonals as one step (3x3 around a cell = 1)."""

    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def manhattan(a: Cell, b: Cell) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


@dataclass
class Player:
    player_id: str
    nickname: str
    role: str | None = None
    position: Cell | None = None
    score: int = 0
    items: list[str] = field(default_factory=list)
    boxes: dict[Cell, str] = field(default_factory=dict)   # square -> item inside


class EscapeGame:
    """A small, testable game engine used by the socket server."""

    def __init__(self, seed: int | None = None, mode: str = "classic") -> None:
        self.random = Random(seed)
        self.mode = mode if mode in MODES else "classic"
        self.size = BOARD_SIZES[self.mode]
        self.tunnel: Cell = (self.size - 1, self.size - 1)
        self.players: dict[str, Player] = {}
        self.obstacles: set[Cell] = set()
        self.status = "waiting"
        self.turn_player_id: str | None = None
        self.turn_deadline: float | None = None
        self.winner_id: str | None = None
        self.rematch_votes: set[str] = set()
        self.match_number = 0
        self.last_move: dict | None = None
        self.finish_reason: str | None = None
        self._reset_night_state()
        self._generate_map()

    @property
    def night(self) -> bool:
        return self.mode == "night"

    def _reset_night_state(self) -> None:
        self.trail: list[tuple[Cell, bool]] = []   # prisoner's square after each turn, hidden by smoke?
        self.key: Cell | None = None
        self.has_key = False
        self.smoke = 0
        self.searchlight = 0          # warder turns of light left
        self.lunge = False            # warder stepped and spotted the prisoner: one click to grab
        self.sprint_steps = 0
        self.barricades: dict[Cell, int] = {}       # square -> rounds left
        self.rounds = 0

    # ------------------------------------------------------------------
    # Players and matches
    # ------------------------------------------------------------------

    def add_player(self, player_id: str, nickname: str) -> None:
        if len(self.players) >= 2:
            raise ValueError("Sorry, this room already has two players.")
        clean_name = clean_nickname(nickname)
        if not clean_name:
            raise ValueError("Please pick a nickname (letters or numbers).")
        taken = {player.nickname.casefold() for player in self.players.values()}
        if clean_name.casefold() in taken:
            raise ValueError("Someone is already using that name. Try another one.")
        self.players[player_id] = Player(player_id, clean_name)

    def remove_player(self, player_id: str) -> None:
        self.players.pop(player_id, None)
        self._set_waiting()

    def set_mode(self, mode: str) -> bool:
        """Switch mode. Starts a fresh match (scores kept) if two players are in."""

        if mode not in MODES:
            raise ValueError("Unknown mode.")
        self.mode = mode
        self.size = BOARD_SIZES[mode]
        return self.start_match() if len(self.players) == 2 else False

    def start_match(self, forced_warder_id: str | None = None) -> bool:
        if len(self.players) != 2:
            self._set_waiting()
            return False

        self.size = BOARD_SIZES[self.mode]
        player_ids = list(self.players)
        if forced_warder_id not in player_ids:
            forced_warder_id = self.random.choice(player_ids)
        prisoner_id = next(pid for pid in player_ids if pid != forced_warder_id)
        warder, prisoner = self.players[forced_warder_id], self.players[prisoner_id]
        warder.role, prisoner.role = "warder", "prisoner"

        self._reset_night_state()
        if self.night:
            warder_start, prisoner_start = self._deal_night()
        else:
            self._generate_map()
            warder_start, prisoner_start = self._pick_starts(self._free_cells())
        warder.position, prisoner.position = warder_start, prisoner_start

        for player in self.players.values():
            player.items = []
            player.boxes = {}
        self.trail = [(prisoner_start, False)]
        if self.night:
            prisoner.items = list(PRISONER_START_ITEMS)
            self._place_boxes()

        self.status = "playing"
        self.turn_player_id = forced_warder_id
        self.turn_deadline = time.monotonic() + TURN_SECONDS
        self.winner_id = None
        self.rematch_votes.clear()
        self.last_move = None
        self.finish_reason = None
        self.match_number += 1
        return True

    # ------------------------------------------------------------------
    # Turns
    # ------------------------------------------------------------------

    def move(self, player_id: str, direction: str) -> tuple[bool, str]:
        if self.status != "playing":
            return False, "This round isn't running right now."

        timeout_message = self.expire_turn()
        if timeout_message:
            return False, timeout_message

        if player_id != self.turn_player_id:
            return False, "It is not your turn."

        player = self.players.get(player_id)
        if player is None or player.position is None:
            return False, "Player is not ready."

        other = self._other(player_id)
        name = direction.strip().lower()

        if name in DIAGONALS:
            dr, dc = DIAGONALS[name]
            return self.catch(player_id, (player.position[0] + dr, player.position[1] + dc))

        delta = DIRECTIONS.get(name)
        if delta is None:
            return False, "Use the arrow keys or W/A/S/D."

        target = (player.position[0] + delta[0], player.position[1] + delta[1])
        if self.lunge:
            if target == other.position:
                return self.catch(player_id, target)
            return False, "You already moved. Click the prisoner to grab them!"
        problem = self._blocked(player, other, target)
        if problem:
            return False, problem
        return True, self._step(player, other, target, direction_name(delta))

    def _blocked(self, player: Player, other: Player, target: Cell) -> str | None:
        if not self._inside(target):
            return "That's the edge of the yard."
        if target in self.obstacles:
            return "A wall is in the way."
        if target in self.barricades:
            return "A barricade blocks the way."
        if player.role == "warder" and target == self.tunnel:
            return "Warders can't go down the tunnel."
        if player.role == "prisoner" and target == self.tunnel and self.night and not self.has_key:
            return "The tunnel is locked. Find the key first."
        if target == other.position and player.role == "prisoner":
            return "You can't walk into the warder."
        return None

    def _step(self, player: Player, other: Player, target: Cell, word: str) -> str:
        """Apply an accepted step and everything that follows from it."""

        self.last_move = {"player_id": player.player_id,
                          "from": list(player.position), "to": list(target)}
        player.position = target

        if target == other.position:
            self._finish(player.player_id, "caught")
            return f"{player.nickname} caught {other.nickname}!"
        if player.role == "prisoner" and target == self.tunnel:
            self._finish(player.player_id, "escaped")
            return f"{player.nickname} unlocked the tunnel and escaped!" if self.night \
                else f"{player.nickname} slipped into the tunnel and escaped!"

        if not self.night:
            self._switch_turn()
            return f"{player.nickname} moved {word}. {other.nickname}'s turn."

        self._pick_up(player)
        if player.role == "warder":
            # The step-and-grab: if the step brings the prisoner into the light,
            # the warder gets one extra action this turn: click them to grab.
            if self._warder_can_grab():
                self.lunge = True
                self.turn_deadline = time.monotonic() + TURN_SECONDS
                return f"{player.nickname} spotted {other.nickname}! One chance to grab."
            if self._warder_senses():
                self.lunge = True
                self.turn_deadline = time.monotonic() + TURN_SECONDS
                return f"{player.nickname} hears something close in the smoke! One guess to grab."
            self._switch_turn()
            return f"{player.nickname} moved in the dark. {other.nickname}'s turn."

        # Prisoner
        if player.position == self.key:
            self.has_key = True
            self.key = None
        if self.sprint_steps > 1:
            self.sprint_steps -= 1
            self.turn_deadline = time.monotonic() + TURN_SECONDS
            return f"{player.nickname} is sprinting..."
        return self._end_prisoner_turn(player, other)

    def _end_prisoner_turn(self, prisoner: Player, warder: Player) -> str:
        self.sprint_steps = 0
        self.trail.append((prisoner.position, self.smoke > 0))
        self.smoke = max(0, self.smoke - 1)
        self.rounds += 1
        self.barricades = {cell: left - 1 for cell, left in self.barricades.items() if left > 1}
        if self.rounds >= LOCKDOWN_ROUNDS:
            self._finish(warder.player_id, "lockdown")
            return f"Lockdown! The prison is sealed and {warder.nickname} wins."
        self._switch_turn()
        return f"{prisoner.nickname} moved in the dark. {warder.nickname}'s turn."

    def _warder_can_grab(self) -> bool:
        roles = self._roles()
        warder, prisoner = roles.get("warder"), roles.get("prisoner")
        if not warder or not prisoner:
            return False
        seen = self.searchlight or self.smoke == 0
        return seen and ring_distance(warder.position, prisoner.position) <= LANTERN_RANGE

    def _warder_senses(self) -> bool:
        """Prisoner hidden in smoke but right beside the warder: the warder may guess."""

        roles = self._roles()
        warder, prisoner = roles.get("warder"), roles.get("prisoner")
        if not self.night or not warder or not prisoner or self.searchlight or self.smoke == 0:
            return False
        return ring_distance(warder.position, prisoner.position) <= LANTERN_RANGE

    def catch(self, player_id: str, target: object) -> tuple[bool, str]:
        """Night Escape: the warder clicks a square next to them to grab the prisoner."""

        if self.status != "playing":
            return False, "This round isn't running right now."
        timeout_message = self.expire_turn()
        if timeout_message:
            return False, timeout_message
        player = self.players.get(player_id)
        if player is None or player_id != self.turn_player_id:
            return False, "It is not your turn."
        if not self.night or player.role != "warder":
            return False, "You can only move up, down, left or right."
        try:
            cell = (int(target[0]), int(target[1]))
        except (TypeError, ValueError, IndexError, KeyError):
            return False, "Pick a square to grab."
        if not self._inside(cell) or cell in self.obstacles \
                or ring_distance(cell, player.position) != LANTERN_RANGE:
            return False, "You can only grab a square right next to you."

        other = self._other(player_id)
        blind = self.smoke > 0 and not self.searchlight
        if blind and not self._warder_senses():
            return False, "You don't hear anyone close enough to grab."
        if cell == other.position:
            self.last_move = {"player_id": player_id,
                              "from": list(player.position), "to": list(cell)}
            player.position = cell
            self._finish(player_id, "caught")
            if blind:
                return True, f"{player.nickname} grabbed into the smoke and caught {other.nickname}!"
            return True, f"{player.nickname} grabbed {other.nickname}!"
        if blind:
            self._switch_turn()
            return True, f"{player.nickname} grabbed at the smoke and missed. {other.nickname}'s turn."
        return False, "Nobody there to grab."

    def use_item(self, player_id: str, item: str, target: object = None) -> tuple[bool, str]:
        if self.status != "playing" or player_id != self.turn_player_id:
            return False, "You can use items on your turn."
        player = self.players[player_id]
        if item not in player.items:
            return False, "You don't have that item."
        if item == "sprint" and self.sprint_steps:
            return False, "You're already sprinting."

        if item == "barricade":
            try:
                cell = (int(target[0]), int(target[1]))
            except (TypeError, ValueError, IndexError, KeyError):
                return False, "Pick a square for the barricade."
            if not self._inside(cell) or cell in self.obstacles or cell == self.tunnel \
                    or cell == player.position or cell in self.barricades:
                return False, "The barricade can't go there."
            if ring_distance(cell, player.position) > BARRICADE_RANGE:
                return False, "Barricades go within 2 squares of you."
            self.barricades[cell] = BARRICADE_ROUNDS
            message = f"{player.nickname} dropped a barricade."
        elif item == "searchlight":
            self.searchlight = SEARCHLIGHT_TURNS
            message = f"{player.nickname} switched on the searchlight! The yard is lit for 2 turns."
        elif item == "smoke":
            self.smoke = SMOKE_TURNS
            message = f"{player.nickname} threw a smoke bomb!"
        else:
            self.sprint_steps = 2
            message = f"{player.nickname} started sprinting (2 steps this turn)!"
        player.items.remove(item)
        return True, message

    def expire_turn(self) -> str | None:
        """Out of time: take a random step for the player (or end a sprint)."""

        if (
            self.status != "playing"
            or self.turn_deadline is None
            or time.monotonic() < self.turn_deadline
        ):
            return None

        player = self.players[self.turn_player_id]
        other = self._other(player.player_id)
        if self.lunge:
            self._switch_turn()
            return f"{player.nickname} hesitated and {other.nickname} slipped away."
        if player.role == "prisoner" and self.sprint_steps == 1:
            text = self._end_prisoner_turn(player, other) if self.night else ""
            return f"{player.nickname}'s sprint ran out of time. " + text

        choices = self._random_steps(player, other)
        if not choices:
            if player.role == "prisoner" and self.night:
                return f"{player.nickname} froze. " + self._end_prisoner_turn(player, other)
            self._switch_turn()
            return f"{player.nickname} ran out of time and couldn't move. Over to {other.nickname}."
        delta, target = self.random.choice(choices)
        hidden = self.night
        text = self._step(player, other, target, direction_name(delta))
        where = "in the dark" if hidden else direction_name(delta)
        return f"{player.nickname} ran out of time and stumbled {where}. " + text.split(". ", 1)[-1]

    def _random_steps(self, player: Player, other: Player) -> list[tuple[tuple[int, int], Cell]]:
        options = []
        for delta in {(-1, 0), (1, 0), (0, -1), (0, 1)}:
            target = (player.position[0] + delta[0], player.position[1] + delta[1])
            # A timeout never catches anyone: catching is a deliberate click.
            if self._blocked(player, other, target) is None and target != other.position:
                options.append((delta, target))
        options.sort()
        if player.role == "prisoner" and self.night:
            # A random stumble shouldn't walk straight into the lantern if avoidable.
            safe = [o for o in options if ring_distance(o[1], other.position) > LANTERN_RANGE]
            options = safe or options
        return options

    def _pick_up(self, player: Player) -> None:
        # A full backpack leaves the box where it is for later.
        if player.position in player.boxes and len(player.items) < MAX_ITEMS:
            player.items.append(player.boxes.pop(player.position))

    # ------------------------------------------------------------------
    # Rematch / reset
    # ------------------------------------------------------------------

    def request_rematch(self, player_id: str) -> tuple[bool, str]:
        if self.status != "finished":
            return False, "Rematch is available after a match ends."

        self.rematch_votes.add(player_id)
        if len(self.rematch_votes) == len(self.players) == 2:
            previous_winner = self.winner_id
            self.start_match(forced_warder_id=previous_winner)
            return True, "Both players agreed. A new match has started."

        player = self.players[player_id]
        return True, f"{player.nickname} agreed to rematch. Waiting for the other player."

    def reset_game(self) -> None:
        for player in self.players.values():
            player.score = 0
            player.role = None
            player.position = None
            player.items = []
            player.boxes = {}

        self.winner_id = None
        self.rematch_votes.clear()
        self._set_waiting()

        if len(self.players) == 2:
            self.start_match()

    # ------------------------------------------------------------------
    # What each player is allowed to see
    # ------------------------------------------------------------------

    def prisoner_visible_to_warder(self) -> bool:
        if not self.night or self.status != "playing":
            return True
        roles = self._roles()
        warder, prisoner = roles.get("warder"), roles.get("prisoner")
        if not warder or not prisoner or warder.position is None or prisoner.position is None:
            return True
        if self.searchlight:
            return True
        if self.smoke > 0:
            return False
        return ring_distance(warder.position, prisoner.position) <= LANTERN_RANGE

    def warder_visible_to_prisoner(self) -> bool:
        if not self.night or self.status != "playing":
            return True
        roles = self._roles()
        warder, prisoner = roles.get("warder"), roles.get("prisoner")
        if not warder or not prisoner or warder.position is None or prisoner.position is None:
            return True
        return ring_distance(warder.position, prisoner.position) <= PRISONER_VISION

    def footprints(self) -> list[tuple[Cell, int]]:
        """Where the prisoner stood 2 turns ago (unless smoke hid it). Visible anywhere."""

        if not self.night or len(self.trail) <= FOOTPRINT_AGE:
            return []
        square, hidden = self.trail[-1 - FOOTPRINT_AGE]
        prisoner = self._roles().get("prisoner")
        if hidden or (prisoner and square == prisoner.position):
            return []
        return [(square, FOOTPRINT_AGE)]

    def snapshot(self, viewer_id: str | None = None) -> dict:
        """Game state as ``viewer_id`` may see it (None = the server's full view)."""

        viewer = self.players.get(viewer_id) if viewer_id else None
        role = viewer.role if viewer else None
        hidden_role = None
        if role == "warder" and not self.prisoner_visible_to_warder():
            hidden_role = "prisoner"
        elif role == "prisoner" and not self.warder_visible_to_prisoner():
            hidden_role = "warder"

        board = self._board(hidden_role)
        player_data = []
        for player in self.players.values():
            row = col = None
            if player.position is not None and player.role != hidden_role:
                row, col = player.position
            player_data.append(
                {
                    "id": player.player_id,
                    "nickname": player.nickname,
                    "role": player.role,
                    "row": row,
                    "col": col,
                    "score": player.score,
                }
            )

        winner_name = None
        if self.winner_id in self.players:
            winner_name = self.players[self.winner_id].nickname

        remaining = 0
        if self.turn_deadline is not None and self.status == "playing":
            remaining = max(0, ceil(self.turn_deadline - time.monotonic()))

        last_move = self.last_move
        if hidden_role and last_move and last_move["player_id"] in self.players \
                and self.players[last_move["player_id"]].role == hidden_role:
            last_move = None

        state = {
            "status": self.status,
            "mode": self.mode,
            "mode_name": MODE_NAMES[self.mode],
            "size": self.size,
            "match_number": self.match_number,
            "board": ["".join(row) for row in board],
            "players": player_data,
            "tunnel": {"row": self.tunnel[0], "col": self.tunnel[1]},
            "turn_player_id": self.turn_player_id,
            "turn_seconds_left": remaining,
            "winner_id": self.winner_id,
            "winner_name": winner_name,
            "finish_reason": self.finish_reason,
            "last_move": last_move,
            "rematch_votes": len(self.rematch_votes),
            "rematch_voters": sorted(self.rematch_votes),
        }
        if self.night:
            state["night"] = self._night_view(viewer, hidden_role)
        return state

    def _night_view(self, viewer: Player | None, hidden_role: str | None) -> dict:
        roles = self._roles()
        warder = roles.get("warder")
        prisoner_side = viewer is None or viewer.role == "prisoner"
        warder_side = viewer is None or viewer.role == "warder"
        prints = self.footprints()
        lantern = None
        if warder and warder.position and hidden_role != "warder":
            lantern = list(warder.position)
        view = {
            "lantern": lantern,
            "lantern_range": LANTERN_RANGE,
            "prisoner_vision": PRISONER_VISION,
            "footprints": [[c[0], c[1], age] for c, age in prints],
            "barricades": [[c[0], c[1], left] for c, left in sorted(self.barricades.items())],
            "smoke_turns": self.smoke,
            "searchlight": self.searchlight,
            # The warder only learns "someone is right beside me in the smoke".
            "lunge": self.lunge,
            "sensed": self._warder_senses() and (viewer is None or viewer.role == "warder"),
            "sprint_steps": self.sprint_steps if prisoner_side else 0,
            "prisoner_hidden": hidden_role == "prisoner",
            "warder_hidden": hidden_role == "warder",
            "rounds_left": max(0, LOCKDOWN_ROUNDS - self.rounds),
            # Only the prisoner knows about the key.
            "key": list(self.key) if (prisoner_side and self.key) else None,
            "has_key": self.has_key if prisoner_side else None,
            "my_items": list(viewer.items) if viewer else [],
            # Each player sees only their own boxes, and what is inside them.
            "my_boxes": [[c[0], c[1], item] for c, item in sorted(viewer.boxes.items())] if viewer else [],
        }
        if viewer is None:
            view["boxes"] = {p.player_id: [[c[0], c[1], item] for c, item in sorted(p.boxes.items())]
                             for p in self.players.values()}
            view["items"] = {p.player_id: list(p.items) for p in self.players.values()}
        return view

    # ------------------------------------------------------------------
    # Dealing a match
    # ------------------------------------------------------------------

    def _deal_night(self) -> tuple[Cell, Cell]:
        """10x10 map, key and starts. The solver rejects deals the prisoner wins by force."""

        fallback = None
        for _ in range(30):
            self._generate_map()
            free = self._free_cells()
            distance = self._distances_from(self.tunnel)
            for _ in range(40):
                warder, prisoner = self.random.sample(free, 2)
                if (manhattan(warder, prisoner) < NIGHT_START_GAP
                        or manhattan(prisoner, self.tunnel) < NIGHT_START_GAP
                        or ring_distance(warder, prisoner) <= PRISONER_VISION
                        or manhattan(warder, self.tunnel) < NIGHT_WARDER_TO_TUNNEL
                        or distance[warder] > distance[prisoner]):
                    continue
                keys = [c for c in free if c not in (warder, prisoner)
                        and manhattan(c, prisoner) >= NIGHT_START_GAP - 1
                        and manhattan(c, self.tunnel) >= NIGHT_START_GAP - 1
                        and ring_distance(c, warder) > LANTERN_RANGE]
                if not keys:
                    continue
                self.key = self.random.choice(keys)
                fallback = fallback or (warder, prisoner, self.key, set(self.obstacles), self.tunnel)
                wins = prisoner_forced_wins(self.size, self.obstacles, self.tunnel, self.key)
                if (warder, prisoner, 0) not in wins:
                    return warder, prisoner
        if fallback is None:  # practically never: take any legal spots
            free = self._free_cells()
            warder, prisoner, self.key = self.random.sample(free, 3)
            return warder, prisoner
        warder, prisoner, self.key, self.obstacles, self.tunnel = fallback
        return warder, prisoner

    def _place_boxes(self) -> None:
        roles = self._roles()
        taken = {self.tunnel, self.key, *(p.position for p in self.players.values())}
        for player in (roles["warder"], roles["prisoner"]):
            spots = [c for c in self._free_cells() if c not in taken
                     and ring_distance(c, player.position) > 1]
            chosen = self.random.sample(spots, min(BOXES_PER_PLAYER, len(spots)))
            # One box of each item for your role, so you choose which one to go for.
            items = list(ITEMS_FOR_ROLE[player.role])
            self.random.shuffle(items)
            player.boxes = {cell: items[i % len(items)] for i, cell in enumerate(chosen)}
            taken |= set(chosen)

    def _pick_starts(self, free_cells: list[Cell]) -> tuple[Cell, Cell]:
        """Classic: random starting spots that don't hand either side a free win."""

        fair = [
            (warder, prisoner)
            for warder in free_cells
            for prisoner in free_cells
            if manhattan(warder, prisoner) >= MIN_START_GAP
            and manhattan(prisoner, self.tunnel) >= MIN_PRISONER_TO_TUNNEL
        ]
        if fair:
            return self.random.choice(fair)
        warder, prisoner = self.random.sample(free_cells, 2)
        return warder, prisoner

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _roles(self) -> dict[str, Player]:
        return {p.role: p for p in self.players.values() if p.role}

    def _other(self, player_id: str) -> Player:
        return next(p for p in self.players.values() if p.player_id != player_id)

    def _free_cells(self) -> list[Cell]:
        return [
            (row, col)
            for row in range(self.size)
            for col in range(self.size)
            if (row, col) not in self.obstacles and (row, col) != self.tunnel
        ]

    def _distances_from(self, start: Cell) -> dict[Cell, int]:
        distance = {start: 0}
        pending = [start]
        while pending:
            current = pending.pop(0)
            for neighbor in self._neighbors(current):
                if self._inside(neighbor) and neighbor not in self.obstacles and neighbor not in distance:
                    distance[neighbor] = distance[current] + 1
                    pending.append(neighbor)
        return distance

    def _finish(self, winner_id: str, reason: str) -> None:
        self.status = "finished"
        self.finish_reason = reason
        self.winner_id = winner_id
        self.players[winner_id].score += 1
        self.turn_player_id = None
        self.turn_deadline = None
        self.searchlight = 0
        self.lunge = False
        self.sprint_steps = 0

    def _switch_turn(self) -> None:
        ending = self.players.get(self.turn_player_id)
        if ending is not None and ending.role == "warder":
            self.searchlight = max(0, self.searchlight - 1)
        self.lunge = False
        player_ids = list(self.players)
        self.turn_player_id = next(
            pid for pid in player_ids if pid != self.turn_player_id
        )
        self.turn_deadline = time.monotonic() + TURN_SECONDS

    def _set_waiting(self) -> None:
        self.status = "waiting"
        self.turn_player_id = None
        self.turn_deadline = None
        self.winner_id = None
        self.rematch_votes.clear()
        self.last_move = None
        self.finish_reason = None
        self._reset_night_state()
        for player in self.players.values():
            player.role = None
            player.position = None
            player.items = []
            player.boxes = {}

    def _generate_map(self) -> None:
        all_cells = [(row, col) for row in range(self.size) for col in range(self.size)]
        walls = WALL_COUNTS[self.mode]

        # Retry until all non-obstacle blocks are connected, avoiding a
        # randomly generated deadlock. The tunnel is also randomized so every
        # match has a fresh map.
        while True:
            self.tunnel = self.random.choice(all_cells)
            cells = [cell for cell in all_cells if cell != self.tunnel]
            obstacles = set(self.random.sample(cells, walls))
            reachable = self._reachable_from_tunnel(obstacles)
            if len(reachable) == self.size * self.size - walls:
                self.obstacles = obstacles
                return

    def _reachable_from_tunnel(self, obstacles: set[Cell]) -> set[Cell]:
        seen = {self.tunnel}
        pending = [self.tunnel]

        while pending:
            current = pending.pop()
            for neighbor in self._neighbors(current):
                if (
                    not self._inside(neighbor)
                    or neighbor in obstacles
                    or neighbor in seen
                ):
                    continue
                seen.add(neighbor)
                pending.append(neighbor)

        return seen

    @staticmethod
    def _neighbors(position: Cell) -> list[Cell]:
        row, col = position
        return [
            (row - 1, col),
            (row + 1, col),
            (row, col - 1),
            (row, col + 1),
        ]

    def _inside(self, position: Cell) -> bool:
        row, col = position
        return 0 <= row < self.size and 0 <= col < self.size

    def _board(self, hidden_role: str | None = None) -> list[list[str]]:
        board = [["." for _ in range(self.size)] for _ in range(self.size)]

        for row, col in self.obstacles:
            board[row][col] = "#"

        tunnel_row, tunnel_col = self.tunnel
        board[tunnel_row][tunnel_col] = "T"

        for player in self.players.values():
            if player.position is None or player.role is None or player.role == hidden_role:
                continue
            row, col = player.position
            symbol = "W" if player.role == "warder" else "P"
            if board[row][col] in {"W", "P"}:
                symbol = "X"
            board[row][col] = symbol

        return board
