from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from utils import load_json

ROOT = Path(__file__).resolve().parent


def fmt_rate(value):
    if value is None:
        return "—"
    try:
        return f"{float(value):.6f}"
    except (TypeError, ValueError):
        return str(value)


def first_number(mapping, *keys):
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


def main():
    data = load_json(ROOT / "data" / "normalized.json", default={})
    reference = data.get("reference_rates", {}) or {}
    banks = reference.get("commercial_banks", {}) or {}

    html = ["<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>", "<title>Tajikistan Exchange Rates</title>", "<style>body{font-family:system-ui,sans-serif;max-width:1100px;margin:32px auto;padding:0 16px;color:#222}table{border-collapse:collapse;width:100%;margin:12px 0 28px}th,td{padding:8px;border:1px solid #ddd;text-align:left}th{background:#f5f5f5}.muted{color:#666}.good{color:#087f23;font-weight:600}</style></head><body>"]
    html.append("<h1>Tajikistan Exchange Rates</h1>")
    html.append(f"<p class='muted'>Updated: {datetime.now(timezone.utc).isoformat()}</p>")

    html.append("<h2>USD / EUR — bank rates from NBT commercial-bank table</h2>")
    html.append("<p>These are the <b>Card Buy</b> values used as the USD/EUR bank reference data. They are displayed here so you can visually verify scraping quality.</p>")
    html.append("<table><tr><th>Bank</th><th>USD buy</th><th>EUR buy</th></tr>")
    for bank, row in sorted(banks.items()):
        usd = row.get("USD", {}) or {}
        eur = row.get("EUR", {}) or {}
        html.append(f"<tr><td>{bank}</td><td>{fmt_rate(first_number(usd, 'card_buy', 'buy'))}</td><td>{fmt_rate(first_number(eur, 'card_buy', 'buy'))}</td></tr>")
    html.append("</table>")

    nbt = data.get("nbt", {}) or {}
    html.append("<h2>Official NBT rates</h2><table><tr><th>Currency</th><th>Rate</th></tr>")
    for currency in ("USD", "EUR", "RUB"):
        row = nbt.get(currency, {}) or {}
        html.append(f"<tr><td>{currency}</td><td>{fmt_rate(first_number(row, 'rate', 'value'))}</td></tr>")
    html.append("</table>")
    html.append("<p class='muted'>The official NBT values above are separate from the commercial-bank Card Buy values.</p>")
    html.append("</body></html>")
    (ROOT / "index.html").write_text("\n".join(html), encoding="utf-8")


if __name__ == "__main__":
    main()
