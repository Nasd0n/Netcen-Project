"""Scout: a small, explainable path-finding assistant for the Escape Plan client.

Scout searches the board the player can currently see with breadth-first
search and suggests one move. It never moves for you.

In Classic both players see everything, so Scout also asks the exact game
solver (solver.classic_outcomes) where each step leads: it takes a forced win
when there is one, never walks into a forced loss when it can avoid it, and
uses breadth-first distances to choose between the safe steps (the prisoner
heads for the tunnel, the warder closes in).

In Night Escape it plays by the night rules: the prisoner goes for the key
first, then the tunnel, and stays out of the warder's step-and-grab reach
(the squares it could grab now or after one step; walls block it);
a warder who can't see the prisoner follows the footprints, or guards the
tunnel if there are none.
"""

from __future__ import annotations

from collections import deque
from functools import lru_cache

from solver import classic_outcomes, reach


DIRECTIONS = (
    ("up", (-1, 0)),
    ("down", (1, 0)),
    ("left", (0, -1)),
    ("right", (0, 1)),
)
DIAGONALS = (
    ("up-left", (-1, -1)),
    ("up-right", (-1, 1)),
    ("down-left", (1, -1)),
    ("down-right", (1, 1)),
)
ALL_NAMES = {name for name, _ in DIRECTIONS + DIAGONALS}
WAIT = "stay"          # the move name for waiting (game.WAIT)


def _inside(position: tuple[int, int], size: int) -> bool:
    row, col = position
    return 0 <= row < size and 0 <= col < size


def _path(
    board: list[str],
    start: tuple[int, int],
    target: tuple[int, int],
    blocked: set[tuple[int, int]],
) -> list[tuple[int, int]] | None:
    """Return a shortest path including start and target, if one exists."""

    size = len(board)
    queue = deque([start])
    previous: dict[tuple[int, int], tuple[int, int] | None] = {start: None}

    while queue:
        current = queue.popleft()
        if current == target:
            result = []
            cursor: tuple[int, int] | None = current
            while cursor is not None:
                result.append(cursor)
                cursor = previous[cursor]
            return list(reversed(result))

        row, col = current
        for _, (row_delta, col_delta) in DIRECTIONS:
            neighbor = (row + row_delta, col + col_delta)
            if not _inside(neighbor, size):
                continue
            if neighbor in blocked or neighbor in previous:
                continue
            if board[neighbor[0]][neighbor[1]] == "#":
                continue
            previous[neighbor] = current
            queue.append(neighbor)

    return None


def _direction_between(start: tuple[int, int], next_position: tuple[int, int]) -> str:
    delta = (next_position[0] - start[0], next_position[1] - start[1])
    for name, expected in DIRECTIONS + DIAGONALS:
        if delta == expected:
            return name
    return "stay"


def _walls(board: list[str]) -> set[tuple[int, int]]:
    return {(r, c) for r, line in enumerate(board) for c, tile in enumerate(line) if tile == "#"}


def grab_zone(board: list[str], warder: tuple[int, int]) -> set[tuple[int, int]]:
    """Night: every square the warder could grab now, or after one step (walls block sight)."""

    walls, size = _walls(board), len(board)
    spots = [warder] + [
        (warder[0] + dr, warder[1] + dc) for _, (dr, dc) in DIRECTIONS
        if _inside((warder[0] + dr, warder[1] + dc), size)
        and board[warder[0] + dr][warder[1] + dc] not in "#T"
    ]
    return {
        (spot[0] + dr, spot[1] + dc)
        for spot in spots for dr in (-1, 0, 1) for dc in (-1, 0, 1)
        if _inside((spot[0] + dr, spot[1] + dc), size)
        and reach(spot, (spot[0] + dr, spot[1] + dc), walls)
    }


def advise(state: dict, player_id: str) -> tuple[str, str | None]:
    """Scout's hint as (text, move). The move is a direction, "stay" or None."""

    text = suggest_move(state, player_id) or "Scout has no idea."
    words = text.split()
    if words and words[0].rstrip(":") == "Wait":
        return text, WAIT
    if len(words) < 2 or words[0] not in {"Move", "Grab"}:
        return text, None
    word = words[1].rstrip(":").lower()
    return text, (word if word in ALL_NAMES else None)


def suggest_direction(state: dict, player_id: str) -> str | None:
    """Just the move Scout would pick ("up", "down-left", "stay", ...), if any."""

    return advise(state, player_id)[1]


def suggest_move(state: dict, player_id: str) -> str | None:
    """Suggest a legal move for the selected player from the current state."""

    if state.get("status") != "playing":
        return "Scout AI is available when a match is in progress."
    if state.get("turn_player_id") != player_id:
        return "Scout AI is ready when it is your turn."

    board = state.get("board", [])
    players = state.get("players", [])
    player = next((item for item in players if item.get("id") == player_id), None)
    opponent = next((item for item in players if item.get("id") != player_id), None)
    if not board or player is None or opponent is None:
        return "Scout AI is waiting for both players."
    if player.get("row") is None or player.get("col") is None:
        return "Scout AI is waiting for your starting position."

    size = len(board)
    night = state.get("mode") == "night"
    start = (player["row"], player["col"])
    opponent_position = None
    if opponent.get("row") is not None and opponent.get("col") is not None:
        opponent_position = (opponent["row"], opponent["col"])
    role = player.get("role")
    tunnel = state.get("tunnel", {})
    target = (tunnel.get("row"), tunnel.get("col"))
    if target[0] is None or target[1] is None:
        return "Scout AI cannot see the tunnel yet."

    if not night and opponent_position is not None and role in ("warder", "prisoner"):
        return _classic_advice(board, role, start, opponent_position, target, bool(state.get("allow_wait")))

    if role == "prisoner":
        night_info = state.get("night") or {}
        goal, goal_name = target, "tunnel"
        blocked = set()
        if night and not night_info.get("has_key"):
            blocked.add(target)                      # the tunnel is locked for now
            if night_info.get("key"):
                goal, goal_name = tuple(night_info["key"]), "key"
        if night and goal_name == "tunnel" and abs(goal[0] - start[0]) + abs(goal[1] - start[1]) == 1:
            return _prisoner_text(start, [start, goal], goal, goal_name)   # out before any grab
        hint = night_info.get("start_hint") if night else None
        if opponent_position is None and hint:
            # The start hint: the warder is somewhere in this block. Go around it,
            # with a square to spare if the yard allows.
            area = {(hint["top"] + dr, hint["left"] + dc)
                    for dr in range(hint["size"]) for dc in range(hint["size"])}
            wide = {(r + dr, c + dc) for r, c in area for dr in (-1, 0, 1) for dc in (-1, 0, 1)}
            for avoid in (wide, area):
                path = _path(board, start, goal, blocked | (avoid - {start}))
                if path and len(path) >= 2:
                    return _prisoner_text(start, path, goal, goal_name, " around the warder's area")
        if opponent_position is not None:
            blocked.add(opponent_position)
            if night:
                # The warder can step once and then grab: stay out of that zone.
                danger = grab_zone(board, opponent_position)
                path = _path(board, start, goal, blocked | (danger - {start}))
                if path and len(path) >= 2:
                    return _prisoner_text(start, path, goal, goal_name)
                return _back_off(state, board, start, opponent_position, blocked)
        path = _path(board, start, goal, blocked)
        if path is None or len(path) < 2:
            if state.get("allow_wait"):
                return "Wait: no safe route to the %s is open right now." % goal_name
            return "No safe route to the %s is visible." % goal_name
        return _prisoner_text(start, path, goal, goal_name)

    if role == "warder":
        if opponent_position is None and (state.get("night") or {}).get("sensed"):
            return "You hear them right beside you in the smoke! Guess a red square to grab."
        if opponent_position is None:
            night_info = state.get("night") or {}
            prints = night_info.get("footprints") or []
            footprint = min(prints, key=lambda f: f[2]) if prints else None
            if footprint:
                goal = (footprint[0], footprint[1])
                path = _path(board, start, goal, {target})
                if path and len(path) >= 2:
                    return "Move %s: follow the footprints (%d steps away)." % (
                        _direction_between(start, path[1]).upper(), len(path) - 1)
                if path and len(path) == 1:
                    return "You're standing on the footprints. They went somewhere close."
            best = None
            for _, (dr, dc) in DIRECTIONS:
                guard = (target[0] + dr, target[1] + dc)
                if not _inside(guard, size) or board[guard[0]][guard[1]] == "#":
                    continue
                path = _path(board, start, guard, {target})
                if path and (best is None or len(path) < len(best)):
                    best = path
            if best and len(best) >= 2:
                return "Move %s: can't see them, so guard the tunnel." % (
                    _direction_between(start, best[1]).upper())
            if state.get("allow_wait"):
                return "Wait: you're guarding the tunnel. Stay put and listen."
            return "You're guarding the tunnel. Stay close and listen."

        if night and reach(start, opponent_position, _walls(board)):
            return "Grab %s: click the locked-on prisoner to catch them!" % (
                _direction_between(start, opponent_position).upper())
        path = _path(board, start, opponent_position, {target})
        if path is None or len(path) < 2:
            if state.get("allow_wait"):
                return "Wait: no direct route to the prisoner is open right now."
            return "No direct route to the prisoner is visible."
        direction = _direction_between(start, path[1])
        if path[1] == opponent_position:
            return "Move %s to catch the prisoner." % direction.upper()
        return "Move %s: shortest route to the prisoner is %d steps." % (
            direction.upper(),
            len(path) - 1,
        )

    return "Scout AI is waiting for your role."


@lru_cache(maxsize=16)
def _classic_table(size: int, walls: frozenset, tunnel: tuple[int, int], allow_wait: bool) -> dict:
    return classic_outcomes(size, set(walls), tunnel, allow_wait)


def _distances(board: list[str], start: tuple[int, int], blocked: set) -> dict:
    size = len(board)
    distance = {start: 0}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for _, (dr, dc) in DIRECTIONS:
            cell = (current[0] + dr, current[1] + dc)
            if _inside(cell, size) and board[cell[0]][cell[1]] != "#" \
                    and cell not in blocked and cell not in distance:
                distance[cell] = distance[current] + 1
                queue.append(cell)
    return distance


def _classic_advice(board, role, me, other, tunnel, allow_wait) -> str:
    """Classic: rank each step (and waiting) with the exact solver, then by distance."""

    size = len(board)
    walls = frozenset((r, c) for r in range(size) for c in range(size) if board[r][c] == "#")
    table = _classic_table(size, walls, tunnel, allow_wait)
    warder = role == "warder"
    options = []
    for name, (dr, dc) in DIRECTIONS:
        cell = (me[0] + dr, me[1] + dc)
        if not _inside(cell, size) or cell in walls:
            continue
        if warder and cell == other:
            return "Move %s to catch the prisoner." % name.upper()
        if not warder and cell == tunnel:
            return "Move %s to enter the tunnel and escape." % name.upper()
        if (warder and cell == tunnel) or (not warder and cell == other):
            continue
        options.append((name, cell))
    if allow_wait:
        options.append((WAIT, me))
    if not options:
        return "You have no legal step, so your turn will pass."

    side, reply = ("W", "P") if warder else ("P", "W")
    if warder:
        distance = _distances(board, other, {tunnel})          # how close each square is to them
    else:
        distance = _distances(board, tunnel, {other})          # how close each square is to the tunnel

    def rank(option):
        name, cell = option
        position = (cell, other, reply) if warder else (other, cell, reply)
        outcome = table.get(position)
        if outcome is None:
            value, plies = 1, 0                               # nobody can force it from here
        elif outcome[0] == side:
            value, plies = 2, -outcome[1]                     # a forced win: the quicker the better
        else:
            value, plies = 0, outcome[1]                      # a forced loss: hold out longest
        return value, plies, -distance.get(cell, 99), name != WAIT

    name, cell = max(options, key=rank)
    value, plies, _, _ = rank((name, cell))
    head = "Wait" if name == WAIT else "Move %s" % name.upper()
    if value == 2:
        moves = -plies // 2 + 1
        goal = "catch" if warder else "escape"
        return "%s: you can force a%s %s in %d move%s." % (
            head, "" if goal == "catch" else "n", goal, moves, "" if moves == 1 else "s")
    if value == 0:
        if warder:
            return "%s: they can force an escape now, so make it as long as you can." % head
        return "%s: the warder can force a catch now; this keeps you free longest." % head
    if warder:
        if name == WAIT:
            return "Wait: holding still keeps the tunnel covered."
        return "%s: closes in without letting them force an escape." % head
    if name == WAIT:
        return "Wait: every step toward the tunnel walks into a trap right now."
    return "%s: the tunnel is %d steps away and this square is safe." % (head, distance.get(cell, 0) + 1)


def _back_off(state, board, start, warder, blocked) -> str:
    """Night prisoner with no safe route: hold still, or step away from the warder."""

    night_info = state.get("night") or {}
    barricades = {(r, c) for r, c, _ in night_info.get("barricades", [])}
    options = [(None, start)] if state.get("allow_wait") else []
    for name, (dr, dc) in DIRECTIONS:
        cell = (start[0] + dr, start[1] + dc)
        if _inside(cell, len(board)) and board[cell[0]][cell[1]] != "#" \
                and cell not in blocked and cell not in barricades:
            options.append((name, cell))
    if not options:
        return "Every route passes too close to the warder. Use an item if you have one."
    # Furthest from the warder wins; staying put wins ties (it leaves no new footprint).
    name, _ = max(options, key=lambda o: (max(abs(o[1][0] - warder[0]), abs(o[1][1] - warder[1])),
                                          o[0] is None))
    if name is None:
        return "Wait: every route passes too close to the warder, so hold still."
    return "Move %s: every route is too close to the warder, so back away." % name.upper()


def _prisoner_text(start, path, goal, goal_name="tunnel", route_note="") -> str:
    direction = _direction_between(start, path[1])
    if path[1] == goal:
        if goal_name == "key":
            return "Move %s to grab the key!" % direction.upper()
        return "Move %s to enter the tunnel and escape." % direction.upper()
    return "Move %s: shortest safe route to the %s%s is %d steps." % (
        direction.upper(),
        goal_name,
        route_note,
        len(path) - 1,
    )
