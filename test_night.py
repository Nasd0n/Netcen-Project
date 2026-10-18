"""Night Escape rules (10x10, key, two-way darkness, step-and-lunge, items)."""

import json
import time
import unittest

from ai import suggest_direction, suggest_move
from game import (
    BARRICADE_ROUNDS, FOOTPRINT_AGE, LOCKDOWN_ROUNDS, SMOKE_TURNS, START_HINT_SIZE, START_HINT_TURNS,
    EscapeGame, manhattan, ring_distance,
)
from solver import prisoner_forced_wins


class NightTests(unittest.TestCase):
    def setUp(self):
        self.game = EscapeGame(seed=5, mode="night")
        self.game.add_player("w", "Wendy")
        self.game.add_player("p", "Pat")
        self.game.start_match(forced_warder_id="w")
        # An open, fixed yard keeps the tests easy to follow.
        self.game.obstacles = {(0, 9), (9, 0)}
        self.game.tunnel = (9, 9)
        self.game.key = (0, 5)
        self.game.has_key = False
        self.warder = self.game.players["w"]
        self.prisoner = self.game.players["p"]
        for player in (self.warder, self.prisoner):
            player.boxes, player.items = {}, []

    def place(self, warder, prisoner, turn="w"):
        self.warder.position, self.prisoner.position = warder, prisoner
        self.game.trail = [(prisoner, False)]
        self.game.lunge = False
        self.game.turn_player_id = turn
        self.game.turn_deadline = time.monotonic() + 10

    def view(self, who):
        state = self.game.snapshot(who)
        return state, {p["role"]: p for p in state["players"]}

    # ----- darkness -------------------------------------------------------

    def test_warder_cannot_see_a_distant_prisoner(self):
        self.place((0, 0), (4, 4))
        state, players = self.view("w")
        self.assertIsNone(players["prisoner"]["row"])
        self.assertNotIn("P", "".join(state["board"]))
        self.assertNotIn("[4, 4]", json.dumps(state))

    def test_prisoner_sees_the_warder_only_within_two(self):
        self.place((0, 0), (4, 4))
        state, players = self.view("p")
        self.assertIsNone(players["warder"]["row"])
        self.assertIsNone(state["night"]["lantern"])
        self.place((2, 2), (4, 4))
        _, players = self.view("p")
        self.assertEqual(players["warder"]["row"], 2)

    def test_warder_moves_are_not_described_either(self):
        self.place((0, 0), (6, 6))
        accepted, message = self.game.move("w", "right")
        self.assertTrue(accepted)
        self.assertNotIn("right", message)

    # ----- key and tunnel -------------------------------------------------

    def test_key_is_the_prisoners_secret(self):
        self.place((0, 0), (6, 6))
        self.assertIsNone(self.view("w")[0]["night"]["key"])
        self.assertEqual(self.view("p")[0]["night"]["key"], [0, 5])

    def test_tunnel_is_locked_until_the_key_is_found(self):
        self.place((0, 0), (9, 8), turn="p")
        accepted, message = self.game.move("p", "right")
        self.assertFalse(accepted)
        self.assertIn("locked", message)
        self.game.has_key = True
        accepted, _ = self.game.move("p", "right")
        self.assertTrue(accepted)
        self.assertEqual(self.game.finish_reason, "escaped")

    def test_stepping_on_the_key_picks_it_up(self):
        self.place((9, 5), (1, 5), turn="p")
        self.game.move("p", "up")
        self.assertTrue(self.game.has_key)
        self.assertIsNone(self.game.key)
        self.assertTrue(self.view("p")[0]["night"]["has_key"])
        self.assertIsNone(self.view("w")[0]["night"]["has_key"])

    # ----- catching -------------------------------------------------------

    def test_step_that_spots_the_prisoner_gives_one_grab(self):
        self.place((3, 3), (5, 4))   # two away: not in the light yet
        accepted, message = self.game.move("w", "down")
        self.assertTrue(accepted)
        self.assertIn("spotted", message)
        self.assertEqual(self.game.status, "playing")        # no automatic catch
        self.assertEqual(self.game.turn_player_id, "w")      # still the warder's turn
        self.assertTrue(self.view("w")[0]["night"]["lunge"])
        accepted, _ = self.game.move("w", "left")            # no second walk
        self.assertFalse(accepted)
        accepted, message = self.game.catch("w", [5, 4])     # click the locked-on square
        self.assertTrue(accepted)
        self.assertEqual(self.game.winner_id, "w")
        self.assertIn("grabbed", message)

    def test_hesitating_after_the_step_lets_them_go(self):
        self.place((3, 3), (5, 4))
        self.game.move("w", "down")
        self.game.turn_deadline = time.monotonic() - 1
        message = self.game.expire_turn()
        self.assertIn("slipped away", message)
        self.assertEqual(self.game.turn_player_id, "p")
        self.assertFalse(self.game.lunge)

    def test_prisoner_in_the_light_must_be_clicked(self):
        self.place((3, 3), (5, 4), turn="p")
        accepted, _ = self.game.move("p", "up")      # (4, 4): diagonal-adjacent
        self.assertTrue(accepted)
        self.assertEqual(self.game.status, "playing")    # nothing automatic
        self.assertEqual(self.game.turn_player_id, "w")
        self.assertEqual(self.view("w")[1]["prisoner"]["row"], 4)
        accepted, _ = self.game.move("w", "down-right")  # Q/E/Z/C keys = click that square
        self.assertTrue(accepted)
        self.assertEqual(self.game.winner_id, "w")

    def test_no_lunge_when_the_prisoner_is_out_of_reach(self):
        self.place((3, 3), (6, 6))
        self.game.move("w", "down")
        self.assertEqual(self.game.status, "playing")

    def test_grabbing_an_empty_square(self):
        self.place((3, 3), (7, 7))
        accepted, _ = self.game.catch("w", [4, 4])
        self.assertFalse(accepted)                      # you can see nobody is there
        accepted, _ = self.game.catch("w", [5, 5])
        self.assertFalse(accepted)                      # not next to you

    def test_smoke_far_away_is_simply_unknown(self):
        self.place((3, 3), (7, 7))
        self.game.smoke = 2
        self.assertFalse(self.view("w")[0]["night"]["sensed"])
        accepted, _ = self.game.catch("w", [4, 4])
        self.assertFalse(accepted)                      # no guessing when nobody is near
        accepted, _ = self.game.move("w", "down")       # and the warder walks normally
        self.assertTrue(accepted)
        self.assertEqual(self.warder.position, (4, 3))

    def test_smoke_beside_the_warder_allows_a_guess(self):
        self.place((3, 3), (4, 4))
        self.game.smoke = 2
        state, players = self.view("w")
        self.assertTrue(state["night"]["sensed"])
        self.assertIsNone(players["prisoner"]["row"])   # still hidden
        accepted, message = self.game.catch("w", [2, 2])
        self.assertTrue(accepted)
        self.assertIn("missed", message)                # a wrong guess ends the turn
        self.assertEqual(self.game.turn_player_id, "p")

    def test_step_next_to_a_smoked_prisoner_gives_one_guess(self):
        self.place((3, 3), (5, 4))
        self.game.smoke = 2
        accepted, message = self.game.move("w", "down")
        self.assertIn("hears", message)
        self.assertTrue(self.game.lunge)
        accepted, _ = self.game.catch("w", [5, 4])
        self.assertTrue(accepted)
        self.assertEqual(self.game.winner_id, "w")

    # ----- items ----------------------------------------------------------

    def test_boxes_are_private_and_show_their_item(self):
        self.place((0, 0), (6, 6), turn="p")
        self.prisoner.boxes = {(6, 7): "sprint"}
        self.warder.boxes = {(0, 1): "barricade"}
        self.assertEqual(self.view("p")[0]["night"]["my_boxes"], [[6, 7, "sprint"]])
        self.assertEqual(self.view("w")[0]["night"]["my_boxes"], [[0, 1, "barricade"]])
        self.game.move("p", "right")
        self.assertEqual(self.prisoner.items, ["sprint"])      # exactly what the box showed
        self.game.move("w", "right")
        self.assertEqual(self.warder.items, ["barricade"])

    def test_full_backpack_leaves_the_box(self):
        self.place((0, 0), (6, 6), turn="p")
        self.prisoner.items = ["smoke", "sprint"]
        self.prisoner.boxes = {(6, 7): "smoke"}
        self.game.move("p", "right")
        self.assertEqual(self.prisoner.boxes, {(6, 7): "smoke"})

    def test_new_match_setup(self):
        for seed in range(10):
            game = EscapeGame(seed=seed, mode="night")
            game.add_player("w", "W"); game.add_player("p", "P")
            game.start_match(forced_warder_id="w")
            warder, prisoner = game.players["w"], game.players["p"]
            self.assertEqual(prisoner.items, ["smoke"])          # starts with a smoke bomb
            self.assertEqual(warder.items, [])
            self.assertEqual(len(warder.boxes) + len(prisoner.boxes), 4)
            self.assertEqual(sorted(prisoner.boxes.values()), ["smoke", "sprint"])
            self.assertEqual(sorted(warder.boxes.values()), ["barricade", "searchlight"])
            self.assertFalse(set(warder.boxes) & set(prisoner.boxes))

    def test_smoke_hides_even_up_close(self):
        self.place((3, 3), (5, 4), turn="p")
        self.prisoner.items = ["smoke"]
        self.game.use_item("p", "smoke")
        self.game.move("p", "up")                    # (4, 4): in the light, but smoked
        self.assertEqual(ring_distance(self.prisoner.position, self.warder.position), 1)
        self.assertEqual(self.game.status, "playing")
        self.assertEqual(self.game.smoke, SMOKE_TURNS - 1)
        self.assertIsNone(self.view("w")[1]["prisoner"]["row"])
        self.assertTrue(self.game.trail[-1][1])     # no footprint from this move

    def test_blind_grab_into_smoke_can_catch(self):
        self.place((3, 3), (4, 4))
        self.game.smoke = 2
        accepted, message = self.game.move("w", "down-right")
        self.assertTrue(accepted)
        self.assertEqual(self.game.winner_id, "w")

    def test_searchlight_lasts_two_warder_turns(self):
        self.place((0, 0), (6, 6))
        self.warder.items = ["searchlight"]
        self.game.use_item("w", "searchlight")
        self.assertEqual(self.view("w")[1]["prisoner"]["row"], 6)
        self.game.move("w", "right")                    # warder turn 1 ends
        self.game.move("p", "left")
        self.assertEqual(self.view("w")[1]["prisoner"]["row"], 6)   # still lit on turn 2
        self.game.move("w", "right")                    # warder turn 2 ends
        self.game.move("p", "left")
        self.assertIsNone(self.view("w")[1]["prisoner"]["row"])

    def test_barricade_blocks_then_crumbles(self):
        self.place((4, 4), (8, 8))
        self.warder.items = ["barricade"]
        accepted, _ = self.game.use_item("w", "barricade", [9, 9])
        self.assertFalse(accepted)                   # never on the tunnel
        accepted, _ = self.game.use_item("w", "barricade", [8, 8])
        self.assertFalse(accepted)                   # too far away
        accepted, _ = self.game.use_item("w", "barricade", [5, 5])
        self.assertTrue(accepted)
        self.assertIn(("barricade" not in self.warder.items), (True,))
        self.game.move("w", "up")
        self.game.turn_player_id = "w"
        self.warder.position = (5, 4)
        accepted, message = self.game.move("w", "right")
        self.assertFalse(accepted)
        self.assertIn("barricade", message)
        for _ in range(BARRICADE_ROUNDS):
            self.game._end_prisoner_turn(self.prisoner, self.warder)
            self.game.status, self.game.turn_player_id = "playing", "w"
        self.assertEqual(self.game.barricades, {})

    def test_sprint_moves_twice_in_one_turn(self):
        self.place((0, 0), (6, 6), turn="p")
        self.prisoner.items = ["sprint"]
        self.game.use_item("p", "sprint")
        self.game.move("p", "right")
        self.assertEqual(self.game.turn_player_id, "p")
        self.game.move("p", "right")
        self.assertEqual(self.prisoner.position, (6, 8))
        self.assertEqual(self.game.turn_player_id, "w")

    # ----- footprints, lockdown, timeouts --------------------------------

    def test_footprint_shows_where_they_were_two_turns_ago(self):
        self.place((0, 0), (6, 3), turn="p")
        visited = [(6, 3)]
        for step in ("right", "right", "down"):
            self.game.turn_player_id = "p"
            self.game.move("p", step)
            visited.append(self.prisoner.position)
        prints = self.view("w")[0]["night"]["footprints"]
        self.assertEqual(len(prints), 1)
        self.assertEqual(tuple(prints[0][:2]), visited[-1 - FOOTPRINT_AGE])

    def test_warder_sees_the_footprint_anywhere(self):
        self.place((0, 0), (6, 3), turn="p")
        for step in ("right", "right"):
            self.game.turn_player_id = "p"
            self.game.move("p", step)
        prints = self.view("w")[0]["night"]["footprints"]
        self.assertEqual(prints, [[6, 3, FOOTPRINT_AGE]])      # far outside the lantern
        self.assertGreater(ring_distance((6, 3), self.warder.position), 1)

    def test_lockdown_hands_the_win_to_the_warder(self):
        self.place((0, 0), (6, 6), turn="p")
        self.game.rounds = LOCKDOWN_ROUNDS - 1
        self.game.move("p", "left")
        self.assertEqual(self.game.finish_reason, "lockdown")
        self.assertEqual(self.game.winner_id, "w")

    def test_timeout_takes_a_random_step(self):
        self.place((0, 0), (6, 6))
        self.game.turn_deadline = time.monotonic() - 1
        message = self.game.expire_turn()
        self.assertIn("ran out of time", message)
        self.assertEqual(manhattan(self.warder.position, (0, 0)), 1)
        self.assertEqual(self.game.turn_player_id, "p")

    def test_timeout_step_avoids_the_light_when_it_can(self):
        for _ in range(20):
            self.place((5, 3), (5, 5), turn="p")
            self.game.status = "playing"
            self.game.turn_deadline = time.monotonic() - 1
            self.game.expire_turn()
            self.assertGreater(ring_distance(self.prisoner.position, (5, 3)), 1)

    def test_timeout_never_catches(self):
        for _ in range(20):
            self.place((5, 5), (5, 6))                  # prisoner right next to the warder
            self.game.status, self.game.winner_id = "playing", None
            self.game.turn_deadline = time.monotonic() - 1
            self.game.expire_turn()
            self.assertEqual(self.game.status, "playing")
            self.assertNotEqual(self.warder.position, (5, 6))

    def test_classic_timeout_also_takes_a_random_step(self):
        game = EscapeGame(seed=2)
        game.add_player("a", "A"); game.add_player("b", "B")
        game.start_match(forced_warder_id="a")
        before = game.players["a"].position
        game.turn_deadline = time.monotonic() - 1
        game.expire_turn()
        self.assertEqual(manhattan(before, game.players["a"].position), 1)

    # ----- waiting --------------------------------------------------------

    def test_prisoner_wait_ends_the_turn_like_a_step(self):
        self.place((0, 0), (6, 6), turn="p")
        self.game.smoke = 2
        rounds = self.game.rounds
        accepted, _ = self.game.move("p", "stay")
        self.assertTrue(accepted)
        self.assertEqual(self.prisoner.position, (6, 6))
        self.assertEqual(self.game.turn_player_id, "w")
        self.assertEqual(self.game.rounds, rounds + 1)          # counts toward the lockdown
        self.assertEqual(self.game.smoke, 1)                     # smoke counts down
        self.assertEqual(self.game.trail[-1], ((6, 6), True))    # recorded (hidden by the smoke)

    def test_prisoner_wait_leaves_a_footprint(self):
        self.place((0, 0), (6, 3), turn="p")
        self.game.move("p", "stay")
        for step in ("right", "right"):
            self.game.turn_player_id = "p"
            self.game.move("p", step)
        self.assertEqual(self.view("w")[0]["night"]["footprints"], [[6, 3, FOOTPRINT_AGE]])
        self.assertEqual(self.game.trail[1], ((6, 3), False))

    def test_wait_counts_toward_the_lockdown(self):
        self.place((0, 0), (6, 6), turn="p")
        self.game.rounds = LOCKDOWN_ROUNDS - 1
        accepted, message = self.game.move("p", "stay")
        self.assertTrue(accepted)
        self.assertEqual(self.game.finish_reason, "lockdown")
        self.assertIn("Lockdown", message)

    def test_wait_ends_a_sprint_and_ticks_barricades(self):
        self.place((0, 0), (6, 6), turn="p")
        self.prisoner.items = ["sprint"]
        self.game.barricades = {(2, 2): 1}
        self.game.use_item("p", "sprint")
        self.game.move("p", "right")                 # first sprint step: still the prisoner's turn
        self.assertEqual(self.game.turn_player_id, "p")
        accepted, _ = self.game.move("p", "stay")    # waiting ends the sprint
        self.assertTrue(accepted)
        self.assertEqual(self.prisoner.position, (6, 7))
        self.assertEqual(self.game.turn_player_id, "w")
        self.assertEqual(self.game.sprint_steps, 0)
        self.assertEqual(self.game.barricades, {})

    def test_warder_wait_never_gives_the_extra_grab(self):
        self.place((3, 3), (4, 4))                   # prisoner in the light at the start of the turn
        self.game.searchlight = 2
        accepted, _ = self.game.move("w", "stay")
        self.assertTrue(accepted)
        self.assertFalse(self.game.lunge)
        self.assertEqual(self.game.status, "playing")
        self.assertEqual(self.game.turn_player_id, "p")
        self.assertEqual(self.game.searchlight, 1)   # the searchlight counts down as usual

    def test_grabbing_at_the_start_of_the_turn_still_works(self):
        self.place((3, 3), (4, 4))
        accepted, _ = self.game.catch("w", [4, 4])
        self.assertTrue(accepted)
        self.assertEqual(self.game.winner_id, "w")

    def test_no_wait_after_a_step_that_spots_them(self):
        self.place((3, 3), (5, 4))
        self.game.move("w", "down")                  # spotted: one click to grab
        accepted, _ = self.game.move("w", "stay")
        self.assertFalse(accepted)
        self.assertTrue(self.game.lunge)

    def test_hidden_wait_looks_exactly_like_a_dark_move(self):
        def warder_turn(action):
            game = EscapeGame(seed=5, mode="night")
            game.add_player("w", "Wendy"); game.add_player("p", "Pat")
            game.start_match(forced_warder_id="w")
            game.obstacles, game.tunnel, game.key = {(0, 9), (9, 0)}, (9, 9), (0, 5)
            for player in game.players.values():
                player.boxes, player.items = {}, []
            game.players["w"].position, game.players["p"].position = (1, 1), (7, 7)
            game.trail, game.turn_player_id = [((7, 7), False)], "w"
            game.rounds = 5                          # mid-match: the start hint is over
            game.turn_deadline = time.monotonic() + 10
            accepted, message = game.move("w", action)
            self.assertTrue(accepted)
            view = game.snapshot("p")
            view.pop("turn_seconds_left")
            return message, view

        moved, waited = warder_turn("right"), warder_turn("stay")
        self.assertEqual(moved[0], waited[0])
        self.assertIn("moved in the dark", waited[0])
        self.assertEqual(moved[1], waited[1])

    def test_visible_wait_is_announced(self):
        self.place((4, 4), (6, 6))                   # the prisoner can see the warder (2 away)
        accepted, message = self.game.move("w", "stay")
        self.assertTrue(accepted)
        self.assertEqual(message, "Wendy waited. Pat's turn.")

    def test_scout_can_tell_the_warder_to_wait_at_the_tunnel(self):
        self.place((8, 9), (2, 2))                   # already guarding, prisoner out of sight
        self.game.trail = [((2, 2), True)]
        self.assertEqual(suggest_direction(self.game.snapshot("w"), "w"), "stay")

    # ----- start hint -----------------------------------------------------

    def opening(self, seed):
        """A fresh night match after the warder's first move."""

        game = EscapeGame(seed=seed, mode="night")
        game.add_player("w", "Wendy"); game.add_player("p", "Pat")
        game.start_match(forced_warder_id="w")
        self.assertIsNone(game.snapshot("p")["night"]["start_hint"])   # nothing before that move
        for step in ("up", "down", "left", "right", "stay"):
            if game.move("w", step)[0]:
                break
        self.assertEqual(game.turn_player_id, "p")
        return game

    def test_prisoner_gets_a_3x3_block_holding_the_warder(self):
        for seed in range(10):
            game = self.opening(seed)
            hint = game.snapshot("p")["night"]["start_hint"]
            row, col = game.players["w"].position
            self.assertEqual(hint["size"], START_HINT_SIZE)
            self.assertTrue(hint["fresh"])
            self.assertTrue(hint["top"] <= row < hint["top"] + 3 and hint["left"] <= col < hint["left"] + 3)
            self.assertTrue(0 <= hint["top"] <= game.size - 3 and 0 <= hint["left"] <= game.size - 3)

    def test_the_warder_never_gets_the_hint(self):
        game = self.opening(4)
        self.assertIsNone(game.snapshot("w")["night"]["start_hint"])

    def test_the_hint_is_chosen_once(self):
        game = self.opening(6)
        first = game.snapshot("p")["night"]["start_hint"]
        for _ in range(20):
            self.assertEqual(game.snapshot("p")["night"]["start_hint"], first)
        game.move("p", "stay")                        # prisoner's first turn ends
        for step in ("up", "down", "left", "right", "stay"):
            if game.move("w", step)[0]:               # the warder moves on...
                break
        faded = game.snapshot("p")["night"]["start_hint"]
        self.assertEqual((faded["top"], faded["left"]), (first["top"], first["left"]))   # ...same block
        self.assertFalse(faded["fresh"])

    def test_the_hint_disappears_after_its_turns(self):
        game = self.opening(7)
        for _ in range(START_HINT_TURNS):
            self.assertIsNotNone(game.snapshot("p")["night"]["start_hint"])
            game.move("p", "stay")
            game.turn_player_id = "p"                 # skip the warder's turn
        self.assertIsNone(game.snapshot("p")["night"]["start_hint"])

    def test_the_warder_is_not_always_in_the_middle(self):
        offsets = set()
        for seed in range(40):
            game = self.opening(seed)
            hint = game.snapshot("p")["night"]["start_hint"]
            row, col = game.players["w"].position
            offsets.add((row - hint["top"], col - hint["left"]))
        self.assertGreater(len(offsets), 4)

    def test_scout_steers_around_the_hint(self):
        self.place((4, 5), (7, 5), turn="p")          # key at (0, 5), warder 3 away: out of sight
        self.game.start_hint, self.game.rounds = (3, 4), 0   # block: rows 3-5, columns 4-6
        state = self.game.snapshot("p")
        self.assertIsNone(next(p for p in state["players"] if p["role"] == "warder")["row"])
        direction = suggest_direction(state, "p")
        self.assertIn(direction, {"left", "right"})   # not straight up toward the block
        self.assertIn("around the warder's area", suggest_move(state, "p"))

    # ----- walls block sight and grabs -----------------------------------

    def test_no_grab_between_two_walls_that_touch_at_a_corner(self):
        self.game.obstacles |= {(4, 5), (5, 4)}
        self.place((4, 4), (5, 5))
        state, roles = self.view("w")
        self.assertIsNone(roles["prisoner"]["row"])                 # out of sight round the corner
        accepted, message = self.game.catch("w", (5, 5))
        self.assertFalse(accepted)
        self.assertIn("Two walls block that corner", message)
        self.assertEqual(self.game.status, "playing")
        self.game.searchlight = 2                                   # even when the yard is lit
        self.assertFalse(self.game.catch("w", (5, 5))[0])
        self.assertNotEqual(suggest_direction(self.game.snapshot("w"), "w"), "down-right")
        self.game.obstacles.discard((5, 4))                         # one wall: you can reach past it
        self.assertTrue(self.game.catch("w", (5, 5))[0])

    def test_a_step_round_a_corner_gives_no_grab(self):
        self.game.obstacles |= {(4, 5), (5, 4)}
        self.place((3, 4), (5, 5))
        accepted, message = self.game.move("w", "down")             # now diagonal, across the corner
        self.assertTrue(accepted)
        self.assertFalse(self.game.lunge)
        self.assertEqual(self.game.turn_player_id, "p")
        self.assertIn("moved in the dark", message)

    def test_smoke_guesses_cannot_reach_round_a_corner(self):
        self.game.obstacles |= {(4, 5), (5, 4)}
        self.place((4, 4), (5, 5))
        self.game.smoke = 2
        self.assertFalse(self.game.snapshot("w")["night"]["sensed"])
        self.assertFalse(self.game.catch("w", (5, 5))[0])

    def test_neither_side_sees_through_walls(self):
        self.place((5, 7), (5, 5))
        self.assertIsNotNone(self.view("p")[1]["warder"]["row"])    # 2 squares away, in plain view
        self.game.obstacles.add((5, 6))
        self.assertIsNone(self.view("p")[1]["warder"]["row"])       # a wall in between
        self.assertNotIn("W", "".join(self.view("p")[0]["board"]))
        self.place((6, 7), (5, 5))                                  # the line still crosses (5, 6)
        self.assertIsNone(self.view("p")[1]["warder"]["row"])
        self.game.obstacles = {(4, 5), (5, 4)}
        self.place((4, 4), (5, 5), turn="p")
        self.assertIsNone(self.view("p")[1]["warder"]["row"])       # the corner hides both ways
        self.assertIsNone(self.view("w")[1]["prisoner"]["row"])

    def test_scout_red_zone_follows_the_walls(self):
        from ai import grab_zone
        board = ["." * 10] * 10
        zone = grab_zone(board, (5, 5))
        self.assertEqual(len(zone), 21)                             # the 5x5 block minus its 4 corners
        self.assertIn((7, 6), zone)
        self.assertNotIn((7, 7), zone)                              # one step then a grab can't reach it
        walled = [row[:6] + ("#" if 4 <= r <= 6 else ".") + row[7:] for r, row in enumerate(board)]
        zone = grab_zone(walled, (5, 5))
        self.assertFalse({(4, 7), (5, 7), (6, 7)} & zone)           # behind the wall

    # ----- dealing --------------------------------------------------------

    def test_deals_are_fair(self):
        for seed in range(15):
            game = EscapeGame(seed=seed, mode="night")
            game.add_player("w", "W"); game.add_player("p", "P")
            game.start_match(forced_warder_id="w")
            w, p = game.players["w"].position, game.players["p"].position
            self.assertEqual(game.size, 10)
            self.assertEqual(len(game.obstacles), 20)
            self.assertGreater(ring_distance(w, p), 2)
            self.assertGreaterEqual(manhattan(w, game.tunnel), 3)
            wins = prisoner_forced_wins(game.size, game.obstacles, game.tunnel, game.key)
            self.assertNotIn((w, p, 0), wins)

    @staticmethod
    def deal(seed):
        game = EscapeGame(seed=seed, mode="night")
        game.add_player("w", "W"); game.add_player("p", "P")
        started = time.perf_counter()
        game.start_match(forced_warder_id="w")
        return game, time.perf_counter() - started

    @staticmethod
    def reaches_tunnel(game, blocked):
        """Plain BFS from the key, with ``blocked`` as an extra wall."""
        seen, pending = {game.key}, [game.key]
        while pending:
            r, c = pending.pop()
            for cell in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
                if cell not in seen and cell != blocked and cell not in game.obstacles \
                        and 0 <= cell[0] < game.size and 0 <= cell[1] < game.size:
                    seen.add(cell)
                    pending.append(cell)
        return game.tunnel in seen

    def test_the_key_always_has_two_separate_ways_out(self):
        for seed in range(40):
            game, _ = self.deal(seed)
            key, size = game.key, game.size
            exits = [(key[0] + dr, key[1] + dc) for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1))]
            exits = [c for c in exits if 0 <= c[0] < size and 0 <= c[1] < size and c not in game.obstacles]
            self.assertGreaterEqual(len(exits), 2, seed)
            self.assertTrue(self.reaches_tunnel(game, None), seed)
            # No single square (a warder or a barricade) cuts the key off from the tunnel.
            for row in range(size):
                for col in range(size):
                    if (row, col) not in game.obstacles and (row, col) not in (key, game.tunnel):
                        self.assertTrue(self.reaches_tunnel(game, (row, col)), (seed, row, col))

    def test_dead_ends_pockets_and_one_way_tunnels_are_refused(self):
        game = self.game                                # open yard, tunnel at (9, 9)
        self.assertTrue(game._key_has_two_routes((0, 5)))
        game.obstacles = {(0, 1)}
        self.assertFalse(game._key_has_two_routes((0, 0)))          # dead end: only (1, 0) is open
        game.obstacles = {(0, 2), (1, 2), (2, 0), (2, 2)}
        self.assertFalse(game._key_has_two_routes((0, 0)))          # pocket: (2, 1) is the only way out,
        self.assertFalse(game._key_has_two_routes((1, 1)))          # even with two or three exits
        game.obstacles = {(8, 9)}
        self.assertFalse(game._key_has_two_routes((0, 5)))          # the tunnel has one way in, (9, 8)

    def test_one_lantern_never_watches_every_way_out(self):
        game = self.game
        self.assertTrue(game._fair_key_spot((0, 5)))
        # Two separate routes from (0, 0), but a warder on (2, 2) lights both
        # without lighting the key: along row 0 into (1, 3), and down into (2, 1).
        game.obstacles = {(0, 4), (1, 4), (3, 0)}
        self.assertTrue(game._key_has_two_routes((0, 0)))
        self.assertTrue(game._one_lantern_watches_key((0, 0)))
        self.assertFalse(game._fair_key_spot((0, 0)))
        for seed in range(20):
            dealt, _ = self.deal(seed)
            self.assertFalse(dealt._one_lantern_watches_key(dealt.key), seed)

    def test_dealing_a_night_match_stays_quick(self):
        times = [self.deal(seed)[1] for seed in range(100, 140)]
        self.assertLess(max(times), 0.5)
        self.assertLess(sum(times) / len(times), 0.15)

    # ----- Scout ----------------------------------------------------------

    def test_scout_prisoner_heads_for_the_key_first(self):
        self.place((9, 2), (3, 5), turn="p")
        self.assertEqual(suggest_direction(self.game.snapshot("p"), "p"), "up")

    def test_scout_prisoner_avoids_the_lunge_zone(self):
        self.place((2, 5), (0, 3), turn="p")         # warder visible, next to the key
        direction = suggest_direction(self.game.snapshot("p"), "p")
        if direction:
            dr, dc = {"up": (-1, 0), "down": (1, 0), "left": (0, -1), "right": (0, 1)}[direction]
            target = (0 + dr, 3 + dc)
            self.assertGreater(ring_distance(target, (2, 5)), 2)

    def test_scout_warder_follows_footprints_in_the_dark(self):
        self.place((5, 2), (6, 6))
        self.game.trail = [((6, 3), False), ((6, 4), False), ((6, 5), False)]
        self.assertIn(suggest_direction(self.game.snapshot("w"), "w"), {"down", "right"})

    def test_scout_warder_points_at_the_lock_on(self):
        self.place((3, 3), (4, 4))
        self.assertEqual(suggest_direction(self.game.snapshot("w"), "w"), "down-right")

if __name__ == "__main__":
    unittest.main()
