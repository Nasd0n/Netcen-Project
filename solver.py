"""Exact game solver for Night Escape (retrograde analysis).

Works backwards from every finished position to label each position as a
forced win for the prisoner, a forced win for the warder, or neither. The
server uses it when dealing a new match: any start where the prisoner has a
guaranteed escape (even with perfect play from the warder) is thrown away and
dealt again, so no match is decided before the first move.

The model is the perfect-information core of Night Escape: straight moves,
the warder catches anything within the 8 squares around it at the start of
its turn or right after its step, and the tunnel is locked until the
prisoner has picked up the key. Items and darkness are left out; they only
make a guaranteed escape harder, never easier.
"""

from __future__ import annotations

from collections import deque

Cell = tuple[int, int]
STEPS = ((-1, 0), (1, 0), (0, -1), (0, 1))


def ring(a: Cell, b: Cell) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def prisoner_forced_wins(size: int, walls: set[Cell], tunnel: Cell, key: Cell | None) -> set[tuple]:
    """All (warder, prisoner, has_key) positions, warder to move, that the prisoner wins by force."""

    free = [(r, c) for r in range(size) for c in range(size) if (r, c) not in walls]
    open_ = set(free)
    nb = {x: [(x[0] + dr, x[1] + dc) for dr, dc in STEPS if (x[0] + dr, x[1] + dc) in open_] for x in free}
    keys = (0, 1) if key is not None else (1,)

    def warder_moves(w, p):
        return [x for x in nb[w] if x != tunnel and x != p]

    def prisoner_moves(w, p, k):
        return [x for x in nb[p] if x != w and (x != tunnel or k)]

    def threat(w, p):
        return ring(w, p) <= 1 or any(ring(s, p) <= 1 for s in warder_moves(w, p))

    result: dict[tuple, str] = {}
    left: dict[tuple, int] = {}
    queue: deque = deque()
    states = [(w, p, k) for w in free for p in free for k in keys
              if w != p and w != tunnel and p != tunnel
              and not (key is not None and p == key and k == 0)]  # standing on the key = holding it

    for w, p, k in states:
        moves = prisoner_moves(w, p, k)
        left[(w, p, "W", k)] = len(warder_moves(w, p))
        left[(w, p, "P", k)] = len([x for x in moves if x != tunnel])
        if threat(w, p):
            result[(w, p, "W", k)] = "W"
            queue.append((w, p, "W", k))
        if k and tunnel in moves:
            result[(w, p, "P", k)] = "P"
            queue.append((w, p, "P", k))
    for w, p, k in states:
        state = (w, p, "P", k)
        if left[state] == 0 and state not in result:
            result[state] = "W"
            queue.append(state)

    while queue:
        w, p, turn, k = queue.popleft()
        winner = result[(w, p, turn, k)]
        if turn == "P":
            # The warder just moved from w0 to w.
            for w0 in nb[w]:
                if w0 == tunnel or w0 == p:
                    continue
                state = (w0, p, "W", k)
                if state in result or state not in left:
                    continue
                if winner == "W":
                    result[state] = "W"
                    queue.append(state)
                else:
                    left[state] -= 1
                    if left[state] == 0 and not threat(w0, p):
                        result[state] = "P"
                        queue.append(state)
        else:
            # The prisoner just moved from p0 to p (picking up the key if p is the key).
            before = (0, 1) if (key is not None and p == key and k == 1) else (k,)
            for p0 in nb[p]:
                if p0 == w or p0 == tunnel:
                    continue
                for k0 in before:
                    state = (w, p0, "P", k0)
                    if state in result or state not in left:
                        continue
                    if winner == "P":
                        result[state] = "P"
                        queue.append(state)
                    else:
                        left[state] -= 1
                        if left[state] == 0:
                            result[state] = "W"
                            queue.append(state)

    return {(w, p, k) for (w, p, t, k), who in result.items() if t == "W" and who == "P"}
