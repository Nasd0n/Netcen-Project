import unittest

from ai import suggest_move


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


if __name__ == "__main__":
    unittest.main()
