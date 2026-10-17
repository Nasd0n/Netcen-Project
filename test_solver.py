"""The exact Classic solver on small positions you can check by hand."""

import time
import unittest

from game import EscapeGame
from solver import classic_outcomes, prisoner_forced_wins

# A 5x5 yard where only the top row is open: a corridor from (0, 0) to (0, 4).
CORRIDOR_WALLS = {(r, c) for r in range(1, 5) for c in range(5)}


class ClassicSolverTests(unittest.TestCase):
    def test_immediate_catch_and_escape(self):
        table = classic_outcomes(5, set(), (4, 4))
        self.assertEqual(table[((2, 2), (2, 3), "W")], ("W", 1))     # step onto the prisoner
        self.assertEqual(table[((0, 0), (4, 3), "P")], ("P", 1))     # step onto the tunnel

    def test_the_warder_cannot_follow_through_the_tunnel(self):
        # Corridor with the tunnel at the far end: a prisoner ahead of the warder walks out.
        table = classic_outcomes(5, CORRIDOR_WALLS, (0, 4))
        self.assertEqual(table[((0, 0), (0, 2), "W")][0], "P")
        # ...but a prisoner behind the warder is trapped in the dead end.
        self.assertEqual(table[((0, 2), (0, 0), "W")][0], "W")

    def test_open_yard_far_apart_is_not_forced(self):
        table = classic_outcomes(5, set(), (4, 4))
        # The prisoner is closer to the tunnel than the warder can get: forced escape.
        self.assertEqual(table[((0, 0), (3, 3), "W")][0], "P")
        # Opposite corners from the tunnel's side: neither side can force it.
        self.assertNotIn(((3, 4), (0, 0), "W"), table)

    def test_winner_hurries_and_loser_stalls(self):
        table = classic_outcomes(5, CORRIDOR_WALLS, (0, 4))
        self.assertEqual(table[((0, 0), (0, 3), "P")], ("P", 1))
        self.assertEqual(table[((0, 0), (0, 2), "W")], ("P", 4))   # W step, P step, W step, P escapes

    def test_waiting_changes_who_can_force_a_win(self):
        with_wait = without_wait = 0
        for seed in range(20):
            game = EscapeGame(seed=seed)
            for allow_wait in (True, False):
                table = classic_outcomes(game.size, game.obstacles, game.tunnel, allow_wait)
                warder_wins = sum(1 for winner, _ in table.values() if winner == "W")
                if allow_wait:
                    with_wait += warder_wins
                else:
                    without_wait += warder_wins
        # Without waiting, the distance parity often traps a prisoner who has to move.
        self.assertLess(with_wait, without_wait)

    def test_night_solver_lets_both_sides_wait(self):
        removed = 0
        for seed in range(12):
            game = EscapeGame(seed=seed, mode="night")
            game.add_player("w", "W"); game.add_player("p", "P")
            game.start_match(forced_warder_id="w")
            args = (game.size, game.obstacles, game.tunnel, game.key)
            with_wait = prisoner_forced_wins(*args, allow_wait=True)
            without_wait = prisoner_forced_wins(*args, allow_wait=False)
            self.assertLessEqual(with_wait, without_wait)
            removed += len(without_wait - with_wait)
        # A warder who may wait can guard, so on some maps escapes stop being forced.
        self.assertGreater(removed, 0)

    def test_fast_enough_for_every_deal(self):
        game = EscapeGame(seed=1)
        started = time.perf_counter()
        for _ in range(20):
            classic_outcomes(game.size, game.obstacles, game.tunnel, True)
        self.assertLess((time.perf_counter() - started) / 20, 0.05)


if __name__ == "__main__":
    unittest.main()
