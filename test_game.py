import unittest
import time

from game import EscapeGame, OBSTACLE_COUNT, SIDES, clean_nickname


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


class SidePickTests(unittest.TestCase):
    """Players pick Warder, Prisoner or Either; picks apply when a match starts."""

    def new_game(self, side_a, side_b, seed=3, mode="classic"):
        game = EscapeGame(seed=seed, mode=mode)
        game.add_player("a", "Alice", side_a)
        game.add_player("b", "Bob", side_b)
        game.start_match()
        return game

    def rematch(self, game, winner):
        game._finish(winner, "escaped")
        game.request_rematch("a")
        accepted, message = game.request_rematch("b")
        self.assertTrue(accepted)
        return message

    def test_picks_that_fit_decide_the_roles(self):
        expected_warder = {
            ("warder", "prisoner"): "a", ("prisoner", "warder"): "b",
            ("warder", "any"): "a", ("any", "warder"): "b",
            ("prisoner", "any"): "b", ("any", "prisoner"): "a",
        }
        for (side_a, side_b), warder in expected_warder.items():
            for seed in range(5):
                game = self.new_game(side_a, side_b, seed)
                self.assertEqual(game.players[warder].role, "warder", (side_a, side_b))
                self.assertEqual(game.turn_player_id, warder)        # the warder still moves first
                self.assertIn("Sides as picked", game.role_note)

    def assert_random(self, games, phrase, ids=("a", "b")):
        """Across seeds both players end up warder, and every note says why."""
        warders = set()
        for game in games:
            warders.add(game.turn_player_id)
            self.assertIn(phrase, game.role_note)
        self.assertEqual(warders, set(ids))

    def test_same_picks_are_random_for_a_first_match(self):
        for pick in SIDES:
            phrase = "picked at random" if pick == "any" else "decided at random"
            self.assert_random((self.new_game(pick, pick, seed) for seed in range(20)), phrase)
        self.assertEqual(self.new_game("warder", "warder").role_note,
                         "You both picked Warder and nobody has won yet, so it was decided at random.")
        self.assertEqual(self.new_game("any", "any").role_note,
                         "Neither of you picked a side, so roles were picked at random.")

    def test_both_want_prisoner_so_the_last_winner_is_the_prisoner(self):
        for winner, loser in (("a", "b"), ("b", "a")):
            game = self.new_game("prisoner", "prisoner")
            message = self.rematch(game, winner)
            self.assertEqual(game.players[winner].role, "prisoner")
            self.assertEqual(game.players[loser].role, "warder")
            self.assertEqual(game.turn_player_id, loser)                # the warder still moves first
            name = game.players[winner].nickname
            self.assertEqual(game.role_note, "You both picked Prisoner. %s won the last match, "
                             "so %s is the prisoner." % (name, name))
            self.assertIn(game.role_note, message)

    def test_both_want_warder_so_the_last_winner_is_the_warder(self):
        for winner, loser in (("a", "b"), ("b", "a")):
            game = self.new_game("warder", "warder")
            self.rematch(game, winner)
            self.assertEqual(game.players[winner].role, "warder")
            self.assertEqual(game.players[loser].role, "prisoner")
            self.assertIn("won the last match", game.role_note)

    def test_the_last_winner_counts_after_a_mode_switch(self):
        game = self.new_game("prisoner", "prisoner")
        game._finish("b", "escaped")
        game.set_mode("night")                                      # new match, scores kept
        self.assertEqual(game.players["b"].role, "prisoner")
        game._finish("a", "caught")
        game.set_mode("classic")
        self.assertEqual(game.players["a"].role, "prisoner")        # the latest winner counts
        self.assertIn("Alice won the last match", game.role_note)

    def test_a_clash_is_random_after_the_last_winner_leaves(self):
        def games(side):
            for seed in range(20):
                game = self.new_game(side, side, seed)
                self.rematch(game, "b")                             # Bob won once...
                game._finish("a", "caught")                         # ...then Alice won and left
                game.remove_player("a")
                self.assertIsNone(game.last_winner_id)
                game.add_player("c", "Cara", side)
                game.start_match()
                yield game
        for side in ("warder", "prisoner"):
            self.assert_random(games(side), "the last winner has left, so it was decided at random",
                               ids=("b", "c"))

    def test_a_new_player_does_not_inherit_the_win(self):
        game = self.new_game("prisoner", "prisoner")
        game._finish("a", "escaped")
        game.remove_player("a")
        game.add_player("c", "Cara", "prisoner")
        game.start_match()
        self.assertEqual(game.role_note,
                         "You both picked Prisoner and nobody has won yet, so it was decided at random.")

    def test_a_clash_is_random_after_a_reset(self):
        def games():
            for seed in range(20):
                game = self.new_game("prisoner", "prisoner", seed)
                game._finish("a", "escaped")
                game.reset_game()                                   # starts the next match at once
                yield game
        self.assert_random(games(), "nobody has won yet, so it was decided at random")

    def test_both_either_keeps_the_old_rule(self):
        self.assert_random((self.new_game("any", "any", seed) for seed in range(20)),
                           "Neither of you picked a side, so roles were picked at random.")
        for winner in ("a", "b"):
            game = self.new_game("any", "any")
            message = self.rematch(game, winner)
            self.assertEqual(game.players[winner].role, "warder")
            self.assertIn("Neither of you picked a side, so last match's winner plays warder.", message)

    def test_picks_beat_the_winner_rule_on_a_rematch(self):
        game = self.new_game("any", "any")
        game.set_side("b", "warder")
        self.rematch(game, "a")
        self.assertEqual(game.players["b"].role, "warder")
        self.assertIn("Sides as picked", game.role_note)

    def test_a_change_waits_for_the_next_match(self):
        game = self.new_game("warder", "prisoner")
        game.set_side("a", "prisoner")
        game.set_side("b", "warder")
        self.assertEqual(game.players["a"].role, "warder")          # this match is untouched
        sides = {p["id"]: p["side"] for p in game.snapshot("b")["players"]}
        self.assertEqual(sides, {"a": "prisoner", "b": "warder"})   # but both can see the new picks
        game.reset_game()                                           # server reset starts the next match
        self.assertEqual(game.players["b"].role, "warder")
        game.set_mode("night")                                      # and so does a mode switch
        self.assertEqual(game.players["b"].role, "warder")

    def test_unknown_sides_are_rejected(self):
        game = self.new_game("any", "any")
        for bad in ("boss", "", "Warder", None):
            with self.assertRaises(ValueError):
                game.set_side("a", bad)
        with self.assertRaises(ValueError):
            EscapeGame().add_player("x", "Xena", "king")
        self.assertEqual(game.players["a"].side, "any")

    def test_picks_work_in_night_escape_and_stay_visible_in_the_dark(self):
        game = self.new_game("prisoner", "any", seed=4, mode="night")
        self.assertEqual(game.players["b"].role, "warder")
        warder_view = game.snapshot("b")
        prisoner = next(p for p in warder_view["players"] if p["id"] == "a")
        self.assertIsNone(prisoner["row"])                  # position hidden...
        self.assertEqual(prisoner["side"], "prisoner")      # ...the pick is not secret

    def test_server_swap_flips_the_picks(self):
        game = self.new_game("any", "any")
        old_warder = game.turn_player_id
        self.assertTrue(game.swap_sides())
        self.assertEqual(game.players[old_warder].side, "prisoner")
        self.rematch(game, old_warder)                       # winner would be warder without the swap
        self.assertEqual(game.players[old_warder].role, "prisoner")


if __name__ == "__main__":
    unittest.main()
