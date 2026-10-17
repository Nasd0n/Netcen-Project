"""Bot-vs-bot balance check for Escape Plan. Not part of the test suite.

Both sides are played by Scout (ai.py), each looking only at its own view
from EscapeGame.snapshot(), with some random moves mixed in so the bots make
human-like mistakes. In Night Escape they also use items in a simple way
(see use_items). The real game engine runs every move, so dealing,
darkness, footprints, items on the map and the lockdown all apply.

    python tools/balance.py                      # both modes, 1000 games each
    python tools/balance.py --mode classic --games 3000 --noise 0.15
    python tools/balance.py --no-wait            # the assignment's must-move rule

The report gives the warder's share of the games that finished; the goal for
Classic is roughly 40-60%.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import game as game_module  # noqa: E402
from ai import suggest_direction  # noqa: E402
from game import EscapeGame  # noqa: E402

STEPS = ["up", "down", "left", "right"]


def random_action(game: EscapeGame, pid: str, rng: random.Random) -> bool:
    """Make any legal move (a wait counts when waiting is allowed)."""

    options = STEPS[:]
    if getattr(game_module, "ALLOW_WAIT", False):
        options.append("stay")
    rng.shuffle(options)
    for option in options:
        if game.move(pid, option)[0]:
            return True
    return False


def use_items(game: EscapeGame, pid: str) -> None:
    """Simple Night item habits: run when the warder is in sight, light up when lost."""

    view = game.snapshot(pid)
    night = view.get("night") or {}
    me = next(p for p in view["players"] if p["id"] == pid)
    other = next(p for p in view["players"] if p["id"] != pid)
    items = night.get("my_items", [])
    if me["role"] == "prisoner" and other["row"] is not None:
        if "smoke" in items and not night.get("smoke_turns"):
            game.use_item(pid, "smoke")
        elif "sprint" in items and not night.get("sprint_steps"):
            game.use_item(pid, "sprint")
    elif me["role"] == "warder" and other["row"] is None and not night.get("footprints") \
            and "searchlight" in items and not night.get("searchlight"):
        game.use_item(pid, "searchlight")


def play(seed: int, mode: str, noise: float, max_moves: int, items: bool = True) -> tuple[str, str, int]:
    """One bot game. Returns (winner role or "none", finish reason, moves)."""

    rng = random.Random(seed * 7919 + 17)
    game = EscapeGame(seed=seed, mode=mode)
    game.add_player("a", "A")
    game.add_player("b", "B")
    game.start_match()
    moves = 0
    while game.status == "playing" and moves < max_moves:
        pid = game.turn_player_id
        game.turn_deadline = time.monotonic() + 60      # bots never time out
        if items and game.night:
            use_items(game, pid)
        done = False
        if rng.random() >= noise:
            direction = suggest_direction(game.snapshot(pid), pid)
            if direction:
                done = game.move(pid, direction)[0]
        if not done:
            done = random_action(game, pid, rng)
        if not done:
            # Nothing legal (e.g. a missed grab chance): let the turn run out.
            game.turn_deadline = time.monotonic() - 1
            game.expire_turn()
        moves += 1
    if game.status != "finished":
        return "none", "unfinished", moves
    return game.players[game.winner_id].role, game.finish_reason, moves


def report(mode: str, games: int, noise: float, max_moves: int, first_seed: int,
           items: bool = True) -> dict:
    results, reasons, lengths = Counter(), Counter(), []
    started = time.perf_counter()
    for seed in range(first_seed, first_seed + games):
        winner, reason, moves = play(seed, mode, noise, max_moves, items)
        results[winner] += 1
        reasons[reason] += 1
        if winner != "none":
            lengths.append(moves)
    finished = results["warder"] + results["prisoner"]
    share = 100 * results["warder"] / finished if finished else 0.0
    print("%-8s %5d games  warder %5.1f%%  prisoner %5.1f%%  unfinished %5.1f%%  "
          "| warder share of finished %5.1f%%  | avg %.0f moves  (%s)  [%.0fs]" % (
              mode, games, 100 * results["warder"] / games, 100 * results["prisoner"] / games,
              100 * results["none"] / games, share,
              sum(lengths) / max(1, len(lengths)),
              ", ".join("%s %d" % item for item in sorted(reasons.items())),
              time.perf_counter() - started))
    return {"results": results, "share": share}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=("classic", "night", "both"), default="both")
    parser.add_argument("--games", type=int, default=1000)
    parser.add_argument("--noise", type=float, default=0.15, help="share of random moves")
    parser.add_argument("--max-moves", type=int, default=None,
                        help="moves (both players) before giving up; default 150 Classic, 400 Night")
    parser.add_argument("--seed", type=int, default=0, help="first game seed")
    parser.add_argument("--no-wait", action="store_true", help="play with ALLOW_WAIT = False")
    parser.add_argument("--no-items", action="store_true", help="Night bots never use their items")
    args = parser.parse_args()
    if args.no_wait:
        game_module.ALLOW_WAIT = False
    print("ALLOW_WAIT = %s, noise %.0f%%, items %s" % (
        getattr(game_module, "ALLOW_WAIT", False), 100 * args.noise, "off" if args.no_items else "on"))
    for mode in (("classic", "night") if args.mode == "both" else (args.mode,)):
        limit = args.max_moves or (150 if mode == "classic" else 400)
        report(mode, args.games, args.noise, limit, args.seed, not args.no_items)


if __name__ == "__main__":
    main()
