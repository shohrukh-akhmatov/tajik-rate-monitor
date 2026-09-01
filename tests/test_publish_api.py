import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (HERE, os.path.join(ROOT, "fixed")):
    if p not in sys.path:
        sys.path.insert(0, p)

from publish_api import build_outputs  # noqa: E402


def rate(service_slug, bank_code, currency, final_rate, status="ok"):
    return {
        "service_slug": service_slug,
        "bank_code": bank_code,
        "currency_code": currency,
        "final_rate": final_rate,
        "status": status,
    }


class TestBuildOutputs(unittest.TestCase):
    def test_nbt_includes_cny_kzt(self):
        calculated = {
            "generated_at": "2026-09-01T09:13:00+05:00",
            "nbt_status": "ok",
            "rates": [
                rate("nbt-reference", "nbt", "RUB", 0.0925),
                rate("nbt-reference", "nbt", "USD", 10.5),
                rate("nbt-reference", "nbt", "EUR", 12.2),
                rate("nbt-reference", "nbt", "CNY", 1.4),
                rate("nbt-reference", "nbt", "KZT", 0.003),
            ],
        }
        outputs = build_outputs(calculated, {})
        codes = {r["currency_code"] for r in outputs["nbt.json"]["rates"]}
        self.assertIn("CNY", codes)
        self.assertIn("KZT", codes)

    def test_rates_endpoint_only_tbank_sberbank(self):
        calculated = {
            "generated_at": "x",
            "rates": [
                rate("t-bank", "oriyon", "RUB", 0.09),
                rate("sberbank", "humo", "RUB", 0.09),
                rate("bank-card", "alif", "RUB", 0.09),
            ],
        }
        outputs = build_outputs(calculated, {})
        slugs = {r["service_slug"] for r in outputs["rates.json"]["rates"]}
        self.assertEqual(slugs, {"t-bank", "sberbank"})

    def test_status_needs_review_when_anomalies(self):
        calculated = {"generated_at": "x", "anomalies": [{"code": "X"}], "rates": []}
        outputs = build_outputs(calculated, {})
        self.assertEqual(outputs["calculated.json"]["status"], "needs_review")
        self.assertEqual(outputs["calculated.json"]["anomaly_count"], 1)

    def test_ok_status_when_clean(self):
        calculated = {"generated_at": "x", "anomalies": [], "rates": []}
        outputs = build_outputs(calculated, {})
        self.assertEqual(outputs["calculated.json"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
