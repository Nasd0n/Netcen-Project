"""Scout: a small, explainable path-finding assistant for the Escape Plan client.

Scout searches the board the player can currently see with breadth-first
search and suggests one move. It never moves for you.

In Night Escape it plays by the night rules: the prisoner goes for the key
first, then the tunnel, and stays out of the warder's step-and-grab reach (2 squares);
a warder who can't see the prisoner follows the footprints, or guards the
tunnel if there are none.
"""

from __future__ import annotations

from collections import deque


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


def _ring(center: tuple[int, int], size: int) -> set[tuple[int, int]]:
    """The 3x3 block around a square (the warder's grab zone at night)."""

    return {
        (center[0] + dr, center[1] + dc)
        for dr in (-1, 0, 1)
        for dc in (-1, 0, 1)
        if _inside((center[0] + dr, center[1] + dc), size)
    }


def suggest_direction(state: dict, player_id: str) -> str | None:
    """Just the direction Scout would pick ("up", "down-left", ...), if any."""

    text = suggest_move(state, player_id) or ""
    words = text.split()
    if len(words) < 2 or words[0] not in {"Move", "Grab"}:
        return None
    word = words[1].rstrip(":").lower()
    return word if word in ALL_NAMES else None


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

    if role == "prisoner":
        night_info = state.get("night") or {}
        goal, goal_name = target, "tunnel"
        blocked = set()
        if night and not night_info.get("has_key"):
            blocked.add(target)                      # the tunnel is locked for now
            if night_info.get("key"):
                goal, goal_name = tuple(night_info["key"]), "key"
        if opponent_position is not None:
            blocked.add(opponent_position)
            if night:
                # The warder can step once and then grab: stay 3+ squares away.
                danger = {
                    (r, c) for r in range(size) for c in range(size)
                    if max(abs(r - opponent_position[0]), abs(c - opponent_position[1])) <= 2
                }
                path = _path(board, start, goal, blocked | (danger - {start}))
                if path and len(path) >= 2:
                    return _prisoner_text(start, path, goal, goal_name)
                return "Every route passes too close to the warder. Wait them out, or use an item."
        path = _path(board, start, goal, blocked)
        if path is None or len(path) < 2:
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
            return "You're guarding the tunnel. Stay close and listen."

        if night and max(abs(opponent_position[0] - start[0]), abs(opponent_position[1] - start[1])) == 1:
            return "Grab %s: click the locked-on prisoner to catch them!" % (
                _direction_between(start, opponent_position).upper())
        path = _path(board, start, opponent_position, {target})
        if path is None or len(path) < 2:
            return "No direct route to the prisoner is visible."
        direction = _direction_between(start, path[1])
        if path[1] == opponent_position:
            return "Move %s to catch the prisoner." % direction.upper()
        return "Move %s: shortest route to the prisoner is %d steps." % (
            direction.upper(),
            len(path) - 1,
        )

    return "Scout AI is waiting for your role."


def _prisoner_text(start, path, goal, goal_name="tunnel") -> str:
    direction = _direction_between(start, path[1])
    if path[1] == goal:
        if goal_name == "key":
            return "Move %s to grab the key!" % direction.upper()
        return "Move %s to enter the tunnel and escape." % direction.upper()
    return "Move %s: shortest safe route to the %s is %d steps." % (
        direction.upper(),
        goal_name,
        len(path) - 1,
    )
