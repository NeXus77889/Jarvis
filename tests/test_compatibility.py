from __future__ import annotations

import unittest

from tools.computer import execute_local_command


class CompatibilityTests(unittest.TestCase):
    def test_zoom_commands_remain_available(self) -> None:
        spoken: list[str] = []
        keys: list[tuple[str, ...]] = []
        handled = execute_local_command(
            "agranda",
            spoken.append,
            hotkey=lambda *args: keys.append(args),
        )
        self.assertTrue(handled)
        self.assertEqual(keys, [("ctrl", "+")])

    def test_unrelated_phrases_do_not_trigger_zoom(self) -> None:
        spoken: list[str] = []
        self.assertFalse(execute_local_command("la pantalla está normal", spoken.append))
        self.assertEqual(spoken, [])

    def test_non_clock_use_of_tiempo_is_not_interpreted_as_time_query(self) -> None:
        spoken: list[str] = []
        self.assertFalse(execute_local_command("no tengo tiempo para eso", spoken.append))
        self.assertEqual(spoken, [])


if __name__ == "__main__":
    unittest.main()
