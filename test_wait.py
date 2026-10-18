"""Waiting instead of moving (ALLOW_WAIT). Night Escape waits are in test_night.py."""

import time
import unittest
from unittest import mock

import game as game_module
from game import EscapeGame


class WaitTests(unittest.TestCase):
    """Classic: either player may stay put instead of moving (ALLOW_WAIT)."""

    def setUp(self):
        self.game = EscapeGame(seed=7)
        self.game.add_player("one", "Alice")
        self.game.add_player("two", "Bob")
        self.game.start_match()

    def test_waiting_passes_the_turn_without_moving(self):
        mover = self.game.turn_player_id
        before = self.game.players[mover].position
        accepted, message = self.game.move(mover, "stay")
        self.assertTrue(accepted)
        self.assertEqual(self.game.players[mover].position, before)
        self.assertNotEqual(self.game.turn_player_id, mover)
        self.assertEqual(self.game.status, "playing")
        self.assertIn("waited", message)

    def test_both_sides_can_wait(self):
        first = self.game.turn_player_id
        self.game.move(first, "stay")
        accepted, _ = self.game.move(self.game.turn_player_id, "stay")
        self.assertTrue(accepted)
        self.assertEqual(self.game.turn_player_id, first)

    def test_you_cannot_wait_out_of_turn(self):
        waiting = next(pid for pid in self.game.players if pid != self.game.turn_player_id)
        accepted, message = self.game.move(waiting, "stay")
        self.assertFalse(accepted)
        self.assertIn("not your turn", message)

    def test_wait_can_be_switched_off(self):
        mover = self.game.turn_player_id
        with mock.patch.object(game_module, "ALLOW_WAIT", False):
            accepted, message = self.game.move(mover, "stay")
            self.assertFalse(accepted)
            self.assertIn("move every turn", message)
            self.assertFalse(self.game.snapshot()["allow_wait"])
        self.assertEqual(self.game.turn_player_id, mover)
        self.assertTrue(self.game.snapshot()["allow_wait"])

    def test_timeout_is_still_a_random_step_not_a_wait(self):
        mover = self.game.turn_player_id
        before = self.game.players[mover].position
        self.game.turn_deadline = time.monotonic() - 1
        self.game.expire_turn()
        after = self.game.players[mover].position
        self.assertEqual(abs(before[0] - after[0]) + abs(before[1] - after[1]), 1)


if __name__ == "__main__":
    unittest.main()
