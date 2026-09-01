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

from monitor import (  # noqa: E402
    extract_rub_pair,
    extract_source_timestamp,
    merge_last_good,
    normalize_pair,
    parse_decimal_tokens,
)


class TestMergeLastGood(unittest.TestCase):
    def test_fresh_current_is_kept(self):
        current = {
            "fetched_at": "2026-09-01T10:00:00+05:00",
            "status": "ok",
            "rates": {"transfer": {"buy": 0.1}},
        }
        previous = {
            "fetched_at": "2026-09-01T09:00:00+05:00",
            "rates": {"transfer": {"buy": 0.09}},
        }
        result = merge_last_good(current, previous)
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("stale", result["rates"]["transfer"])
        self.assertEqual(result["last_success_at"], current["fetched_at"])

    def test_retained_rates_are_stamped_stale(self):
        current = {"fetched_at": "2026-09-01T10:00:00+05:00", "status": "no_rate", "rates": {}}
        previous = {
            "fetched_at": "2026-09-01T09:00:00+05:00",
            "last_success_at": "2026-09-01T09:00:00+05:00",
            "rates": {"transfer": {"buy": 0.0925, "sell": 0.0940, "buy_per_1000": 92.5}},
        }
        result = merge_last_good(current, previous)
        self.assertEqual(result["status"], "stale")
        self.assertTrue(result["rates"]["transfer"]["stale"])
        self.assertEqual(result["rates"]["transfer"]["stale_from"], "2026-09-01T09:00:00+05:00")
        self.assertEqual(result["rates"]["transfer"]["buy"], 0.0925)

    def test_no_previous_leaves_current_untouched(self):
        current = {"fetched_at": "2026-09-01T10:00:00+05:00", "status": "no_rate", "rates": {}}
        result = merge_last_good(current, None)
        self.assertIs(result, current)


class TestParsers(unittest.TestCase):
    def test_parse_decimal_tokens(self):
        self.assertEqual(parse_decimal_tokens("RUB 0.0925 0.0940"), [0.0925, 0.0940])
        self.assertEqual(parse_decimal_tokens("0,0925"), [0.0925])
        self.assertEqual(parse_decimal_tokens("5.0 15.0"), [])
        self.assertEqual(parse_decimal_tokens("0.0925 0.0925"), [0.0925])

    def test_normalize_pair_buy_sell(self):
        pair = normalize_pair([0.0925, 0.0940], "raw", "buy_sell")
        self.assertEqual((pair["buy"], pair["sell"]), (0.0925, 0.0940))

    def test_normalize_pair_sell_buy(self):
        pair = normalize_pair([0.0940, 0.0925], "raw", "sell_buy")
        self.assertEqual((pair["buy"], pair["sell"]), (0.0925, 0.0940))

    def test_normalize_pair_single_value(self):
        pair = normalize_pair([0.0925], "raw", "buy_sell")
        self.assertEqual(pair["buy"], 0.0925)
        self.assertIsNone(pair["sell"])

    def test_extract_rub_pair(self):
        text = "Курс НБТ\nПокупка Продажа\nRUB 0.0925 0.0940"
        pair = extract_rub_pair(text)
        self.assertEqual((pair["buy"], pair["sell"]), (0.0925, 0.0940))

    def test_extract_source_timestamp_dmy(self):
        self.assertIsNotNone(extract_source_timestamp("Курс на 25.08.2026, 14:06:47"))

    def test_extract_source_timestamp_ru_month(self):
        self.assertIsNotNone(extract_source_timestamp("21:04, 25 августа 2026"))


if __name__ == "__main__":
    unittest.main()
