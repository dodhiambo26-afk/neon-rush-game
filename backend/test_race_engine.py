import unittest

from race_engine import simulate_race


class DeterministicRaceTests(unittest.TestCase):
    def test_same_seed_and_inputs_produce_same_result(self):
        inputs = [
            {"tick": 20, "direction": -1},
            {"tick": 90, "direction": 1},
            {"tick": 180, "direction": -1},
        ]
        first = simulate_race(123456789, inputs, 5000)
        second = simulate_race(123456789, inputs, 5000)
        self.assertEqual(first, second)

    def test_different_seed_changes_generated_race(self):
        first = simulate_race(123456789, [], 120)
        second = simulate_race(987654321, [], 120)
        self.assertNotEqual(first, second)

    def test_invalid_direction_is_rejected(self):
        with self.assertRaises(ValueError):
            simulate_race(123, [{"tick": 1, "direction": 0}], 10)

    def test_input_outside_race_is_rejected(self):
        with self.assertRaises(ValueError):
            simulate_race(123, [{"tick": 11, "direction": 1}], 10)


if __name__ == "__main__":
    unittest.main()
