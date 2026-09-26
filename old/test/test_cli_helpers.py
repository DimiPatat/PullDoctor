"""
test_cli_helpers.py

Unit tests for the non-interactive parts of cli_helpers.py. The
interactive prompt (prompt_for_fight_id) isn't covered here since it
reads from stdin -- this only tests resolve_fight_id's explicit-ID
path, which is pure logic.

Run with:
    python test_cli_helpers.py
"""

import unittest

import cli_helpers

FIGHTS = [
    {"id": 1, "name": "Boss A", "kill": False},
    {"id": 2, "name": "Boss A", "kill": True},
    {"id": 3, "name": "Boss B", "kill": False},
]


class TestResolveFightId(unittest.TestCase):
    def test_valid_explicit_id_returned_as_is(self):
        self.assertEqual(cli_helpers.resolve_fight_id(FIGHTS, 2), 2)

    def test_invalid_explicit_id_exits(self):
        with self.assertRaises(SystemExit):
            cli_helpers.resolve_fight_id(FIGHTS, 999)


if __name__ == "__main__":
    unittest.main()
