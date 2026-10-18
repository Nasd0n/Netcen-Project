"""The exact Classic solver on small positions you can check by hand."""

import time
import unittest

from game import EscapeGame
from solver import classic_outcomes, clear_line, prisoner_forced_wins, reach

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

    def test_night_solver_knows_two_walls_block_a_diagonal_grab(self):
        # 4x4, walls on both sides of the (1, 1)-(2, 2) diagonal, tunnel open at (3, 3).
        # The warder can't grab across that corner or reach the prisoner in one step,
        # so the prisoner walks round to the tunnel. Before, this counted as a grab.
        wins = prisoner_forced_wins(4, {(1, 2), (2, 1)}, (3, 3), None)
        self.assertIn(((1, 1), (2, 2), 1), wins)
        self.assertNotIn(((1, 1), (2, 2), 1), prisoner_forced_wins(4, {(1, 2)}, (3, 3), None))

    def test_fast_enough_for_every_deal(self):
        game = EscapeGame(seed=1)
        started = time.perf_counter()
        for _ in range(20):
            classic_outcomes(game.size, game.obstacles, game.tunnel, True)
        self.assertLess((time.perf_counter() - started) / 20, 0.05)


class SightTests(unittest.TestCase):
    """solver.clear_line: walls block sight, and so do two walls touching at a corner."""

    def test_neighbours(self):
        self.assertTrue(clear_line((0, 0), (0, 1), set()))
        self.assertTrue(clear_line((0, 0), (1, 1), set()))
        self.assertTrue(clear_line((0, 0), (1, 1), {(0, 1)}))           # one wall: you can see past it
        self.assertFalse(clear_line((0, 0), (1, 1), {(0, 1), (1, 0)}))  # two walls meeting at the corner
        self.assertTrue(clear_line((5, 5), (5, 5), set()))

    def test_two_squares_away(self):
        self.assertFalse(clear_line((0, 0), (0, 2), {(0, 1)}))          # straight through a wall
        self.assertTrue(clear_line((0, 0), (0, 2), {(1, 1)}))
        self.assertFalse(clear_line((0, 0), (2, 2), {(1, 1)}))
        self.assertFalse(clear_line((0, 0), (2, 2), {(1, 2), (2, 1)}))  # squeezes past the far corner
        self.assertTrue(clear_line((0, 0), (2, 2), {(1, 2)}))
        # A knight's jump: the line crosses (0, 1) and (1, 1), but not (1, 0).
        self.assertFalse(clear_line((0, 0), (1, 2), {(0, 1)}))
        self.assertFalse(clear_line((0, 0), (1, 2), {(1, 1)}))
        self.assertTrue(clear_line((0, 0), (1, 2), {(1, 0), (0, 2)}))

    def test_the_target_square_itself_does_not_block(self):
        self.assertTrue(clear_line((0, 0), (0, 1), {(0, 1)}))           # so a lantern can light a wall

    def test_sight_works_both_ways(self):
        import random
        rng = random.Random(4)
        for _ in range(30):
            walls = {(rng.randrange(7), rng.randrange(7)) for _ in range(12)}
            for a in [(r, c) for r in range(7) for c in range(7)]:
                for b in [(a[0] + dr, a[1] + dc) for dr in range(-2, 3) for dc in range(-2, 3)]:
                    self.assertEqual(clear_line(a, b, walls), clear_line(b, a, walls), (a, b, walls))

    def test_reach_is_the_8_squares_around_in_sight(self):
        self.assertTrue(reach((3, 3), (4, 4), set()))
        self.assertFalse(reach((3, 3), (3, 3), set()))
        self.assertFalse(reach((3, 3), (5, 3), set()))
        self.assertFalse(reach((3, 3), (4, 4), {(3, 4), (4, 3)}))


if __name__ == "__main__":
    unittest.main()
