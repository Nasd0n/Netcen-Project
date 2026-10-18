import unittest

from ai import suggest_direction, suggest_move


class ScoutAiTests(unittest.TestCase):
    def test_prisoner_hint_moves_toward_tunnel(self):
        state = {
            "status": "playing",
            "turn_player_id": "p",
            "board": [
                ".....",
                ".....",
                ".....",
                ".....",
                ".....",
            ],
            "tunnel": {"row": 4, "col": 4},
            "players": [
                {"id": "p", "role": "prisoner", "row": 0, "col": 0},
                {"id": "w", "role": "warder", "row": 2, "col": 2},
            ],
        }
        self.assertTrue(
            any(direction in suggest_move(state, "p") for direction in ("UP", "DOWN", "LEFT", "RIGHT"))
        )

    def test_warder_hint_can_catch_adjacent_prisoner(self):
        state = {
            "status": "playing",
            "turn_player_id": "w",
            "board": [
                ".....",
                ".WP..",
                ".....",
                ".....",
                "....T",
            ],
            "tunnel": {"row": 4, "col": 4},
            "players": [
                {"id": "w", "role": "warder", "row": 1, "col": 1},
                {"id": "p", "role": "prisoner", "row": 1, "col": 2},
            ],
        }
        self.assertIn("RIGHT", suggest_move(state, "w"))

    def test_hint_does_not_send_a_move_outside_your_turn(self):
        state = {
            "status": "playing",
            "turn_player_id": "other",
            "board": ["....."] * 5,
            "tunnel": {"row": 4, "col": 4},
            "players": [
                {"id": "me", "role": "prisoner", "row": 0, "col": 0},
                {"id": "other", "role": "warder", "row": 2, "col": 2},
            ],
        }
        self.assertIn("your turn", suggest_move(state, "me"))

    def test_scout_says_wait_when_no_route_is_open(self):
        state = {
            "status": "playing",
            "turn_player_id": "p",
            "allow_wait": True,
            "board": [
                "P#...",
                "W....",
                ".....",
                ".....",
                "....T",
            ],
            "tunnel": {"row": 4, "col": 4},
            "players": [
                {"id": "p", "role": "prisoner", "row": 0, "col": 0},
                {"id": "w", "role": "warder", "row": 1, "col": 0},
            ],
        }
        self.assertEqual(suggest_direction(state, "p"), "stay")
        state["allow_wait"] = False                   # original rules: never suggest waiting
        self.assertIsNone(suggest_direction(state, "p"))


if __name__ == "__main__":
    unittest.main()
