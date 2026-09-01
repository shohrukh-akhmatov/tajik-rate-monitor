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

from reference_rates import (  # noqa: E402
    normalize_bank_name,
    parse_alif_payload,
    parse_nbt_api,
    parse_nbt_commercial_html,
    parse_nbt_html,
)

NBT_HEADER = (
    "<tr><th>Credit financial institutions</th><th>Interbank Buy</th><th>Interbank Sell</th>"
    "<th>Cash Buy</th><th>Cash Sell</th><th>Non-Cash Buy</th><th>Non-Cash Sell</th>"
    "<th>E-Wallets Buy</th><th>E-Wallets Sell</th><th>Credit Cards Buy</th><th>Credit Cards Sell</th>"
    "<th>NPCR Buy</th><th>NPCR Sell</th><th>Date</th></tr>"
)
NBT_ROW = (
    "<tr><td>Alif Bank</td><td>9.95</td><td>10.05</td><td>9.90</td><td>10.10</td>"
    "<td>9.92</td><td>10.08</td><td>9.93</td><td>10.07</td><td>10.00</td><td>10.20</td>"
    "<td>9.94</td><td>10.06</td><td>25.08.2026</td></tr>"
)


class TestParseNbtApi(unittest.TestCase):
    def test_ok_payload(self):
        data = {
            "success": True,
            "period_info": {"date": "2026-08-25"},
            "data": [
                {"Code": "840", "Nominal": "1", "Rate": "10.50"},
                {"Code": "978", "Nominal": "1", "Rate": "12.20"},
                {"Code": "810", "Nominal": "100", "Rate": "12.00"},
                {"Code": "156", "Nominal": "1", "Rate": "1.40"},
                {"Code": "398", "Nominal": "100", "Rate": "0.30"},
            ],
        }
        out = parse_nbt_api(data)
        self.assertEqual(out["status"], "ok")
        self.assertAlmostEqual(out["rates"]["RUB"]["per_unit"], 0.12)
        self.assertAlmostEqual(out["rates"]["USD"]["per_unit"], 10.50)

    def test_failed_payload(self):
        out = parse_nbt_api({"success": False})
        self.assertEqual(out["status"], "error")

    def test_partial_payload(self):
        data = {"success": True, "data": [{"Code": "840", "Nominal": "1", "Rate": "10.50"}]}
        out = parse_nbt_api(data)
        self.assertEqual(out["status"], "partial")
        self.assertIn("USD", out["rates"])


class TestParseNbtHtml(unittest.TestCase):
    def test_ok_html(self):
        html = (
            "<table><tr><td>840</td><td>1</td><td>USD</td><td>10.50</td></tr>"
            "<tr><td>978</td><td>1</td><td>EUR</td><td>12.20</td></tr>"
            "<tr><td>810</td><td>100</td><td>RUB</td><td>12.00</td></tr>"
            "<tr><td>156</td><td>1</td><td>CNY</td><td>1.40</td></tr>"
            "<tr><td>398</td><td>100</td><td>KZT</td><td>0.30</td></tr></table>"
        )
        out = parse_nbt_html(html)
        self.assertEqual(out["status"], "ok")
        self.assertAlmostEqual(out["rates"]["RUB"]["per_unit"], 0.12)


class TestParseNbtCommercialHtml(unittest.TestCase):
    def test_current_layout(self):
        out = parse_nbt_commercial_html(f"<table>{NBT_HEADER}{NBT_ROW}</table>", "USD")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["commercial_banks"]["alif"]["USD"]["card_buy"], 10.00)
        self.assertEqual(out["commercial_banks"]["alif"]["USD"]["card_sell"], 10.20)
        self.assertEqual(out["commercial_banks"]["alif"]["date"], "25.08.2026")

    def test_survives_column_reorder(self):
        header = (
            "<tr><th>Credit financial institutions</th><th>NPCR Buy</th><th>NPCR Sell</th>"
            "<th>Credit Cards Buy</th><th>Credit Cards Sell</th><th>Date</th></tr>"
        )
        row = (
            "<tr><td>Oriyonbank</td><td>9.94</td><td>10.06</td><td>10.30</td><td>10.50</td><td>25.08.2026</td></tr>"
        )
        out = parse_nbt_commercial_html(f"<table>{header}{row}</table>", "USD")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["commercial_banks"]["oriyon"]["USD"]["card_buy"], 10.30)
        self.assertEqual(out["commercial_banks"]["oriyon"]["USD"]["card_sell"], 10.50)


    def test_legacy_no_header_fallback(self):
        row = NBT_ROW
        out = parse_nbt_commercial_html(f"<table>{row}</table>", "USD")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["commercial_banks"]["alif"]["USD"]["card_buy"], 10.00)
        self.assertEqual(out["commercial_banks"]["alif"]["USD"]["card_sell"], 10.20)

    def test_date_uses_rightmost_date_column(self):
        header = (
            "<tr><th>Credit financial institutions</th><th>Date Updated</th><th>Interbank Buy</th><th>Interbank Sell</th>"
            "<th>Credit Cards Buy</th><th>Credit Cards Sell</th><th>Rate Date</th></tr>"
        )
        row = (
            "<tr><td>Oriyonbank</td><td>01.09.2026</td><td>9.95</td><td>10.05</td>"
            "<td>10.30</td><td>10.50</td><td>25.08.2026</td></tr>"
        )
        out = parse_nbt_commercial_html(f"<table>{header}{row}</table>", "USD")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["commercial_banks"]["oriyon"]["date"], "25.08.2026")

    def test_partial_header_fills_only_missing_side(self):
        header = "<tr><th>Bank</th><th>Credit Cards Buy</th><th>Sell</th><th>Date</th></tr>"
        row = "<tr><td>Eskhata</td><td>10.30</td><td>10.50</td><td>25.08.2026</td></tr>"
        out = parse_nbt_commercial_html(f"<table>{header}{row}</table>", "USD")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["commercial_banks"]["eskhata"]["USD"]["card_buy"], 10.30)
        self.assertEqual(out["commercial_banks"]["eskhata"]["USD"]["card_sell"], 10.50)

    def test_legend_row_not_mistaken_for_header(self):
        legend = "<tr><td>Card Buy Rate</td><td>Card Sell Rate</td><td>Note</td></tr>"
        out = parse_nbt_commercial_html(f"<table>{legend}{NBT_HEADER}{NBT_ROW}</table>", "USD")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["commercial_banks"]["alif"]["USD"]["card_buy"], 10.00)


class TestNormalizeBankName(unittest.TestCase):
    def test_aliases(self):
        self.assertEqual(normalize_bank_name("Alif Bank"), "alif")
        self.assertEqual(normalize_bank_name("Dushanbe City"), "dc")
        self.assertEqual(normalize_bank_name("International Bank of Tajikistan"), "ibt")
        self.assertIsNone(normalize_bank_name("Unknown Bank"))


class TestParseAlifPayload(unittest.TestCase):
    def test_local_rates(self):
        data = {
            "localRates": [
                {
                    "name": "RUB",
                    "currencyCode": "810",
                    "moneyTransferBuyValue": "0.0925",
                    "moneyTransferTradeValue": "0.0940",
                }
            ]
        }
        rates = parse_alif_payload(data)
        self.assertEqual(rates["RUB"]["buy"], 0.0925)
        self.assertEqual(rates["RUB"]["sell"], 0.0940)


if __name__ == "__main__":
    unittest.main()
