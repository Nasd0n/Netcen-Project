import unittest
import time

from game import EscapeGame, OBSTACLE_COUNT, clean_nickname


class EscapeGameTests(unittest.TestCase):
    def setUp(self):
        self.game = EscapeGame(seed=7)
        self.game.add_player("one", "Alice")
        self.game.add_player("two", "Bob")
        self.game.start_match()

    def test_map_has_five_obstacles_and_one_tunnel(self):
        self.assertEqual(len(self.game.obstacles), OBSTACLE_COUNT)
        self.assertNotIn(self.game.tunnel, self.game.obstacles)

    def test_tunnel_is_randomized_between_matches(self):
        tunnels = {EscapeGame(seed=seed).tunnel for seed in range(10)}
        self.assertGreater(len(tunnels), 1)

    def test_two_players_receive_different_roles_and_positions(self):
        players = list(self.game.players.values())
        self.assertEqual({player.role for player in players}, {"warder", "prisoner"})
        self.assertNotEqual(players[0].position, players[1].position)

    def test_invalid_direction_is_rejected(self):
        accepted, message = self.game.move(self.game.turn_player_id, "diagonal")
        self.assertFalse(accepted)
        self.assertIn("W/A/S/D", message)

    def test_only_current_player_can_move(self):
        other_id = next(
            player_id
            for player_id in self.game.players
            if player_id != self.game.turn_player_id
        )
        accepted, message = self.game.move(other_id, "w")
        self.assertFalse(accepted)
        self.assertIn("not your turn", message)

    def test_prisoner_reaching_tunnel_wins_and_scores(self):
        tunnel_row, tunnel_col = self.game.tunnel
        for direction, (row_delta, col_delta) in (
            ("up", (-1, 0)),
            ("down", (1, 0)),
            ("left", (0, -1)),
            ("right", (0, 1)),
        ):
            target = (tunnel_row - row_delta, tunnel_col - col_delta)
            if self.game._inside(target) and target not in self.game.obstacles:
                break
        warder_start = next(
            (cell for cell in self.game._reachable_from_tunnel(self.game.obstacles)
             if cell != self.game.tunnel and cell != target),
        )
        self.game.players["one"].role = "warder"
        self.game.players["one"].position = warder_start
        self.game.players["two"].role = "prisoner"
        self.game.players["two"].position = target
        self.game.turn_player_id = "two"
        self.game.turn_deadline = time.monotonic() + 10

        accepted, message = self.game.move("two", direction)

        self.assertTrue(accepted)
        self.assertIn("escaped", message)
        self.assertEqual(self.game.status, "finished")
        self.assertEqual(self.game.players["two"].score, 1)

    def test_previous_winner_becomes_warder_after_rematch(self):
        tunnel_row, tunnel_col = self.game.tunnel
        for direction, (row_delta, col_delta) in (
            ("up", (-1, 0)),
            ("down", (1, 0)),
            ("left", (0, -1)),
            ("right", (0, 1)),
        ):
            target = (tunnel_row - row_delta, tunnel_col - col_delta)
            if self.game._inside(target) and target not in self.game.obstacles:
                break
        warder_start = next(
            (cell for cell in self.game._reachable_from_tunnel(self.game.obstacles)
             if cell != self.game.tunnel and cell != target),
        )
        self.game.players["one"].role = "warder"
        self.game.players["one"].position = warder_start
        self.game.players["two"].role = "prisoner"
        self.game.players["two"].position = target
        self.game.turn_player_id = "two"
        self.game.turn_deadline = time.monotonic() + 10
        self.game.move("two", direction)

        self.game.request_rematch("two")
        accepted, _ = self.game.request_rematch("one")

        self.assertTrue(accepted)
        self.assertEqual(self.game.status, "playing")
        self.assertEqual(self.game.players["two"].role, "warder")
        self.assertEqual(self.game.turn_player_id, "two")

    def test_reset_clears_scores_and_starts_a_fresh_match(self):
        self.game.players["one"].score = 4
        self.game.players["two"].score = 2

        self.game.reset_game()

        self.assertEqual(self.game.players["one"].score, 0)
        self.assertEqual(self.game.players["two"].score, 0)
        self.assertEqual(self.game.status, "playing")
        self.assertEqual(self.game.match_number, 2)


    def test_nicknames_are_cleaned_and_unique(self):
        self.assertEqual(clean_nickname("  Al\x00ice \n  B "), "Alice B")
        self.assertEqual(len(clean_nickname("x" * 50)), 16)
        game = EscapeGame(seed=1)
        game.add_player("a", "Alice")
        with self.assertRaises(ValueError):
            game.add_player("b", "alice")
        with self.assertRaises(ValueError):
            game.add_player("c", "\x07\x08")

    def test_starting_spots_are_fair(self):
        for seed in range(40):
            game = EscapeGame(seed=seed)
            game.add_player("a", "A")
            game.add_player("b", "B")
            game.start_match()
            (r1, c1), (r2, c2) = (p.position for p in game.players.values())
            self.assertGreaterEqual(abs(r1 - r2) + abs(c1 - c2), 3)

    def test_catch_records_reason_and_last_move(self):
        warder = self.game.players[self.game.turn_player_id]
        prisoner = next(p for p in self.game.players.values() if p is not warder)
        free = [
            (r, c) for r in range(5) for c in range(5)
            if (r, c) not in self.game.obstacles and (r, c) != self.game.tunnel
        ]
        for cell in free:
            below = (cell[0] + 1, cell[1])
            if below in free:
                warder.position, prisoner.position = cell, below
                break
        accepted, _ = self.game.move(warder.player_id, "down")
        self.assertTrue(accepted)
        snap = self.game.snapshot()
        self.assertEqual(snap["finish_reason"], "caught")
        self.assertEqual(snap["last_move"]["to"], list(prisoner.position))


if __name__ == "__main__":
    unittest.main()
