from __future__ import annotations

import html
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
    if value is None:
        return "—", "Not available in current NBT table"
    if key in stale_keys:
        return fmt_rate(value), "Last valid NBT Card Buy (stale)"
    return fmt_rate(value), "NBT Card Buy"


def main():
    data = load_json(ROOT / "data" / "normalized.json", default={})
    reference = data.get("reference_rates", {}) or {}
    nbt = reference.get("nbt", {}) or {}
    banks = nbt.get("commercial_banks", {}) or reference.get("commercial_banks", {}) or {}
    stale_keys = set(nbt.get("commercial_banks_stale", []) or reference.get("commercial_banks_stale", []) or [])

    html_out = [
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>Tajikistan Exchange Rates</title>",
        "<style>body{font-family:system-ui,sans-serif;max-width:1100px;margin:32px auto;padding:0 16px;color:#222}table{border-collapse:collapse;width:100%;margin:12px 0 28px}th,td{padding:8px;border:1px solid #ddd;text-align:left}th{background:#f5f5f5}.muted{color:#666}</style></head><body>",
        "<h1>Tajikistan Exchange Rates</h1>",
        f"<p class='muted'>Generated: {datetime.now(timezone.utc).isoformat()}</p>",
    ]

    html_out.append("<h2>USD / EUR — NBT commercial-bank Card Buy</h2>")
    html_out.append("<p>Current NBT Card Buy values are shown directly. If USD/EUR is absent from the current NBT commercial-bank table, it is shown as unavailable and is not replaced by another source.</p>")
    html_out.append("<table><tr><th>Bank</th><th>USD buy</th><th>USD source/status</th><th>EUR buy</th><th>EUR source/status</th></tr>")
    for bank_code, raw_row in sorted(banks.items()):
        row = dict(raw_row or {})
        row["code"] = bank_code
        usd, usd_source = commercial_quote(row, "USD", stale_keys)
        eur, eur_source = commercial_quote(row, "EUR", stale_keys)
        name = html.escape(str(row.get("name", bank_code)))
        html_out.append(f"<tr><td>{name}</td><td>{usd}</td><td>{usd_source}</td><td>{eur}</td><td>{eur_source}</td></tr>")
    html_out.append("</table>")

    html_out.append("<h2>Official NBT rates</h2><table><tr><th>Currency</th><th>Rate</th></tr>")
    for currency in ("USD", "EUR", "RUB"):
        row = nbt.get("rates", {}).get(currency, {}) or {}
        html_out.append(f"<tr><td>{currency}</td><td>{fmt_rate(first_number(row, 'per_unit', 'rate', 'value'))}</td></tr>")
    html_out.append("</table>")
    html_out.append("<p class='muted'>Official NBT rates are separate from commercial-bank Card Buy rates. Alif API fallback is used only for RUB transfer-base calculation and never for USD/EUR bank publication.</p>")
    html_out.append("</body></html>")
    (ROOT / "index.html").write_text("\n".join(html_out), encoding="utf-8")


if __name__ == "__main__":
    main()
