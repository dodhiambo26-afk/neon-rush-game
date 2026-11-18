import ast
import pathlib
import unittest


def load_tournament_config():
    source = pathlib.Path(__file__).with_name("capacity_tournaments.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "TOURNAMENTS"
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("TOURNAMENTS configuration was not found")


TOURNAMENTS = load_tournament_config()


class CapacityTournamentConfigurationTests(unittest.TestCase):
    def test_exactly_two_capacity_tournaments_are_configured(self):
        self.assertEqual(set(TOURNAMENTS), {"tournament_10k", "tournament_1k"})

    def test_10k_capacity_fee_and_prizes(self):
        cfg = TOURNAMENTS["tournament_10k"]
        self.assertEqual(cfg["target"], 10000)
        self.assertEqual((cfg["entry_kes"], cfg["entry_usd"], cfg["entry_stars"]), (50, "0.80", 50))
        self.assertEqual([p["amount_kes"] for p in cfg["prizes"]], [100000, 50000, 30000, 20000, 15000])
        self.assertEqual(sum(p["amount_kes"] for p in cfg["prizes"]), 215000)

    def test_1k_capacity_fee_and_prizes(self):
        cfg = TOURNAMENTS["tournament_1k"]
        self.assertEqual(cfg["target"], 1000)
        self.assertEqual((cfg["entry_kes"], cfg["entry_usd"], cfg["entry_stars"]), (50, "0.80", 50))
        self.assertEqual([p["amount_kes"] for p in cfg["prizes"]], [10000, 5000, 3000, 2000, 1500])
        self.assertEqual(sum(p["amount_kes"] for p in cfg["prizes"]), 21500)

    def test_tournament_sessions_are_independent(self):
        self.assertNotEqual(
            TOURNAMENTS["tournament_10k"]["session_key"],
            TOURNAMENTS["tournament_1k"]["session_key"],
        )


if __name__ == "__main__":
    unittest.main()
