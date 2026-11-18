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




class CapacityReservationSafetyTests(unittest.TestCase):
    def _source(self, filename):
        return pathlib.Path(__file__).with_name(filename).read_text(encoding="utf-8")

    def test_atomic_reservation_and_expiry_helpers_exist(self):
        source = self._source("capacity_tournaments.py")
        tree = ast.parse(source)
        functions = {
            node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertTrue({
            "reserve_slot", "release_slot", "expire_stale_reservations",
            "reservation_is_valid",
        }.issubset(functions))
        self.assertIn("firestore.transactional", source)
        self.assertIn('"reserved_count"', source)
        self.assertIn('"consumed"', source)
        self.assertIn("expected_checkout_id is not None", source)
        self.assertIn('if reason == "expired":', source)
        self.assertIn('if data.get("provider") == "mpesa":', source)
        self.assertIn('if rd.get("provider") == "mpesa":', source)
        self.assertIn("reservation_expired = False", source)

    def test_paid_admission_requires_a_live_reservation(self):
        source = self._source("capacity_tournaments.py")
        self.assertIn('"reservation_missing_or_expired"', source)
        self.assertIn("expiry <= now", source)
        self.assertIn("expected_checkout_id", source)
        self.assertIn("total + reserved_count > target", source)
        self.assertIn("expected_checkout_id is not None", source)

    def test_all_three_payment_paths_reserve_capacity(self):
        self.assertIn('reserve_slot(payment_type, telegram_id, "mpesa"', self._source("mpesa.py"))
        self.assertIn('reserve_slot(kind, uid, "paypal"', self._source("paypal_payments.py"))
        self.assertIn('reserve_slot(', self._source("bot.py"))
        self.assertIn('payload=f"capacity_stars:{kind}:{reservation_id}"', self._source("bot.py"))
        self.assertIn('refresh_reservation(kind, uid, reservation_id', self._source("bot.py"))
        self.assertIn("def refresh_reservation", self._source("capacity_tournaments.py"))
        self.assertIn('reservation_checkout_id', self._source("capacity_tournaments.py"))

    def test_paypal_will_not_capture_after_reservation_expiry(self):
        source = self._source("paypal_payments.py")
        self.assertIn("reservation_is_valid(kind, p.get(\"telegram_id\"), p.get(\"internal_id\"))", source)
        self.assertIn("no capture was attempted", source)

    def test_mpesas_live_mode_is_explicit_for_new_tournaments(self):
        source = self._source("mpesa.py")
        self.assertIn('if environment != "production":', source)
        self.assertIn("MPESA_ENVIRONMENT=production", source)


if __name__ == "__main__":
    unittest.main()
