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


def commercial_quote(row, currency, stale_keys):
    key = f"{row.get('code', '')}:{currency}"
    quote = row.get(currency, {}) or {}
    value = first_number(quote, "card_buy", "buy")
    if value is None or key in stale_keys:
        return "—", "Not available in current NBT table"
    return fmt_rate(value), "NBT Card Buy"


def main():
    data = load_json(ROOT / "data" / "normalized.json", default={})
    reference = data.get("reference_rates", {}) or {}
    banks = reference.get("commercial_banks", {}) or {}
    nbt_stale_keys = set(reference.get("commercial_banks_stale", []) or [])

    html = ["<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>", "<title>Tajikistan Exchange Rates</title>", "<style>body{font-family:system-ui,sans-serif;max-width:1100px;margin:32px auto;padding:0 16px;color:#222}table{border-collapse:collapse;width:100%;margin:12px 0 28px}th,td{padding:8px;border:1px solid #ddd;text-align:left}th{background:#f5f5f5}.muted{color:#666}.good{color:#087f23;font-weight:600}</style></head><body>"]
    html.append("<h1>Tajikistan Exchange Rates</h1>")
    html.append(f"<p class='muted'>Updated: {datetime.now(timezone.utc).isoformat()}</p>")

    html.append("<h2>USD / EUR — NBT commercial-bank Card Buy</h2>")
    html.append("<p>Only values explicitly published in the current NBT commercial-bank table are shown as bank rates. If USD/EUR is absent there, the bank is treated as not offering that currency and no substitute or Alif fallback is published.</p>")
    html.append("<table><tr><th>Bank</th><th>USD buy</th><th>USD source</th><th>EUR buy</th><th>EUR source</th></tr>")
    for bank_code, row in sorted(banks.items()):
        row = dict(row)
        row["code"] = bank_code
        usd, usd_source = commercial_quote(row, "USD", nbt_stale_keys)
        eur, eur_source = commercial_quote(row, "EUR", nbt_stale_keys)
        name = row.get("name", bank_code)
        html.append(f"<tr><td>{name}</td><td>{usd}</td><td>{usd_source}</td><td>{eur}</td><td>{eur_source}</td></tr>")
    html.append("</table>")

    nbt = data.get("nbt", {}) or {}
    html.append("<h2>Official NBT rates</h2><table><tr><th>Currency</th><th>Rate</th></tr>")
    for currency in ("USD", "EUR", "RUB"):
        row = nbt.get(currency, {}) or {}
        html.append(f"<tr><td>{currency}</td><td>{fmt_rate(first_number(row, 'rate', 'value'))}</td></tr>")
    html.append("</table>")
    html.append("<p class='muted'>Official NBT rates are separate from commercial-bank Card Buy rates. Alif API fallback is used only for RUB transfer base-rate calculation, never for USD/EUR bank publication.</p>")
    html.append("</body></html>")
    (ROOT / "index.html").write_text("\n".join(html), encoding="utf-8")


if __name__ == "__main__":
    main()
