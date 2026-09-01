import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (HERE, os.path.join(ROOT, "fixed")):
    if p not in sys.path:
        sys.path.insert(0, p)

from stub_deps import install
install()

from monitor import merge_last_good  # noqa: E402

from calculate_rates import (  # noqa: E402
    card_rub_row,
    choose_base,
    is_valid_direct_transfer,
    normalize_date,
    pct_change,
    valid_rub,
)

FALLBACK = {"ibt", "spitamen", "vasl"}


def bank_with_transfer(value, stale=False, fallback_source=None):
    transfer = {"buy_per_1000": round(value * 1000, 4)}
    if stale:
        transfer["stale"] = True
    if fallback_source:
        transfer["fallback_source"] = fallback_source
    return {"rates": {"transfer": transfer}}


class TestIsValidDirectTransfer(unittest.TestCase):
    def test_fallback_bank_never_direct(self):
        self.assertFalse(is_valid_direct_transfer("ibt", bank_with_transfer(0.0925), FALLBACK))

    def test_fresh_direct(self):
        self.assertTrue(is_valid_direct_transfer("oriyon", bank_with_transfer(0.0925), FALLBACK))

    def test_stale_is_not_direct(self):
        self.assertFalse(is_valid_direct_transfer("oriyon", bank_with_transfer(0.0925, stale=True), FALLBACK))

    def test_missing_transfer(self):
        self.assertFalse(is_valid_direct_transfer("oriyon", {"rates": {}}, FALLBACK))


class TestChooseBase(unittest.TestCase):
    def test_direct_bank_uses_own_observation(self):
        base, src_bank, kind = choose_base(
            "oriyon", bank_with_transfer(0.0925), 0.0910, "alif_api", FALLBACK, lambda b: None
        )
        self.assertAlmostEqual(base, 0.0925)
        self.assertEqual(src_bank, "oriyon")
        self.assertEqual(kind, "bank_transfer_observation")

    def test_fallback_bank_uses_alif(self):
        base, src_bank, kind = choose_base(
            "ibt", bank_with_transfer(0.0925), 0.0910, "alif_api", FALLBACK, lambda b: None
        )
        self.assertEqual(base, 0.0910)
        self.assertEqual(src_bank, "alif")
        self.assertEqual(kind, "alif_api")

    def test_fallback_bank_no_alif_uses_last_valid(self):
        base, src_bank, kind = choose_base(
            "ibt", bank_with_transfer(0.0925), None, "missing", FALLBACK, lambda b: 0.0905
        )
        self.assertAlmostEqual(base, 0.0905)
        self.assertEqual(kind, "last_valid_route")

    def test_non_fallback_missing_keeps_own_last_valid_not_alif(self):
        # Regression: previously a non-fallback bank whose site was down silently
        # used the Alif rate as its base. Now it keeps its own last-valid base.
        base, src_bank, kind = choose_base(
            "oriyon", {"rates": {}}, 0.0910, "alif_api", FALLBACK, lambda b: 0.0905
        )
        self.assertAlmostEqual(base, 0.0905)
        self.assertEqual(src_bank, "oriyon")
        self.assertEqual(kind, "last_valid_route")

    def test_non_fallback_missing_no_last_valid(self):
        base, src_bank, kind = choose_base(
            "oriyon", {"rates": {}}, 0.0910, "alif_api", FALLBACK, lambda b: None
        )
        self.assertIsNone(base)
        self.assertEqual(kind, "missing")


class TestCallSiteWiring(unittest.TestCase):
    """Lock in the exact lookup keys used at each call site in main()."""

    def test_first_loop_uses_service_slug_key(self):
        last_valid = {("t-bank", "oriyon"): 0.0905}
        base, src_bank, kind = choose_base(
            "oriyon", {"rates": {}}, None, "missing", FALLBACK,
            lambda b: last_valid.get(("t-bank", b)),
        )
        self.assertAlmostEqual(base, 0.0905)
        self.assertEqual(src_bank, "oriyon")
        self.assertEqual(kind, "last_valid_route")

    def test_card_loop_prefers_star_key_then_tbank_key(self):
        last_valid = {("*", "humo"): 0.0910, ("t-bank", "humo"): 0.0900}
        base, _, kind = choose_base(
            "humo", {"rates": {}}, None, "missing", FALLBACK,
            lambda b: last_valid.get(("*", b)) or last_valid.get(("t-bank", b)),
        )
        self.assertAlmostEqual(base, 0.0910)
        self.assertEqual(kind, "last_valid_route")

    def test_missing_key_returns_missing_kind(self):
        base, src_bank, kind = choose_base(
            "oriyon", {"rates": {}}, None, "missing", FALLBACK,
            lambda b: None,
        )
        self.assertIsNone(base)
        self.assertEqual(kind, "missing")


class TestCardRubRow(unittest.TestCase):
    def test_last_valid_row_is_stale(self):
        rules = {"rounding": {"published_rate_decimals": 4}}
        row = card_rub_row("oriyon", {"name": "Oriyonbank"}, 0.0905, "oriyon", "last_valid_route", rules, "t")
        self.assertEqual(row["status"], "stale")
        self.assertEqual(row["service_slug"], "bank-card")

    def test_fresh_row_is_ok(self):
        rules = {"rounding": {"published_rate_decimals": 4}}
        row = card_rub_row("oriyon", {"name": "Oriyonbank"}, 0.0925, "oriyon", "bank_transfer_observation", rules, "t")
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["final_rate"], 0.0925)


class TestEndToEndStale(unittest.TestCase):
    def test_merge_last_good_output_is_rejected_as_direct(self):
        # The actual bug: a failed scrape's retained rate must NOT flow through as
        # a fresh observation. Feed merge_last_good's output into the consumer.
        current = {"fetched_at": "2026-09-02T09:00:00+05:00", "status": "no_rate", "rates": {}}
        previous = {
            "fetched_at": "2026-09-01T09:00:00+05:00",
            "last_success_at": "2026-09-01T09:00:00+05:00",
            "rates": {"transfer": {"buy": 0.0925, "sell": 0.0940, "buy_per_1000": 92.5}},
        }
        merged = merge_last_good(current, previous)
        self.assertEqual(merged["status"], "stale")
        self.assertFalse(is_valid_direct_transfer("oriyon", merged, FALLBACK))


class TestHelpers(unittest.TestCase):
    def test_pct_change(self):
        self.assertAlmostEqual(pct_change(10, 11), 10.0)
        self.assertIsNone(pct_change(0, 5))
        self.assertIsNone(pct_change(5, None))

    def test_valid_rub(self):
        self.assertTrue(valid_rub(0.0925))
        self.assertFalse(valid_rub(5.0))
        self.assertFalse(valid_rub("x"))

    def test_normalize_date(self):
        self.assertEqual(normalize_date("25.08.2026 14:06"), "2026-08-25T14:06:00+05:00")
        self.assertIsNone(normalize_date(None))


if __name__ == "__main__":
    unittest.main()
