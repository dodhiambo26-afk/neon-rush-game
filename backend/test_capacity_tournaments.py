import unittest

import capacity_tournaments


class CapacityTournamentConfigurationTests(unittest.TestCase):
    def test_exactly_two_capacity_tournaments_are_configured(self):
        self.assertEqual(
            set(capacity_tournaments.TOURNAMENTS),
            {"tournament_10k", "tournament_1k"},
        )

    def test_10k_capacity_fee_and_prizes(self):
        cfg = capacity_tournaments.TOURNAMENTS["tournament_10k"]
        self.assertEqual(cfg["target"], 10000)
        self.assertEqual((cfg["entry_kes"], cfg["entry_usd"], cfg["entry_stars"]), (50, "0.80", 50))
        self.assertEqual([p["amount_kes"] for p in cfg["prizes"]], [100000, 50000, 30000, 20000, 15000])
        self.assertEqual(sum(p["amount_kes"] for p in cfg["prizes"]), 215000)

    def test_1k_capacity_fee_and_prizes(self):
        cfg = capacity_tournaments.TOURNAMENTS["tournament_1k"]
        self.assertEqual(cfg["target"], 1000)
        self.assertEqual((cfg["entry_kes"], cfg["entry_usd"], cfg["entry_stars"]), (50, "0.80", 50))
        self.assertEqual([p["amount_kes"] for p in cfg["prizes"]], [10000, 5000, 3000, 2000, 1500])
        self.assertEqual(sum(p["amount_kes"] for p in cfg["prizes"]), 21500)

    def test_tournament_sessions_are_independent(self):
        self.assertNotEqual(
            capacity_tournaments.TOURNAMENTS["tournament_10k"]["session_key"],
            capacity_tournaments.TOURNAMENTS["tournament_1k"]["session_key"],
        )

    def test_unknown_tournament_is_rejected(self):
        with self.assertRaises(ValueError):
            capacity_tournaments._config("weekly")


if __name__ == "__main__":
    unittest.main()
