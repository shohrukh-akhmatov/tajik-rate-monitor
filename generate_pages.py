from __future__ import annotations

import html
import json
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
    calculated = load_json(ROOT / "calculated_rates.json", default={})
    reference = data.get("reference_rates", {}) or {}
    nbt = reference.get("nbt", {}) or {}
    banks = nbt.get("commercial_banks", {}) or reference.get("commercial_banks", {}) or {}
    stale_keys = set(nbt.get("commercial_banks_stale", []) or reference.get("commercial_banks_stale", []) or [])

    out = [
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>Tajikistan Exchange Rates Monitor</title>",
        "<style>body{font-family:system-ui,sans-serif;max-width:1200px;margin:32px auto;padding:0 16px;color:#222}table{border-collapse:collapse;width:100%;margin:12px 0 28px}th,td{padding:8px;border:1px solid #ddd;text-align:left;vertical-align:top}th{background:#f5f5f5}.muted{color:#666}.stale{font-weight:600}.ok{font-weight:600}.warning{background:#fff7df}.num{text-align:right;font-variant-numeric:tabular-nums}</style></head><body>",
        "<h1>Tajikistan Exchange Rates Monitor</h1>",
        f"<p class='muted'>Generated: {datetime.now(timezone.utc).isoformat()}</p>",
    ]

    out.append("<h2>USD / EUR — NBT commercial-bank Card Buy</h2>")
    out.append("<p>USD/EUR bank values are taken only from the NBT commercial-bank Card Buy table. Missing current NBT values are not substituted with another source.</p>")
    out.append("<table><tr><th>Bank</th><th>USD buy</th><th>USD source/status</th><th>EUR buy</th><th>EUR source/status</th></tr>")
    for bank_code, raw_row in sorted(banks.items()):
        row = dict(raw_row or {})
        row["code"] = bank_code
        usd, usd_source = commercial_quote(row, "USD", stale_keys)
        eur, eur_source = commercial_quote(row, "EUR", stale_keys)
        name = html.escape(str(row.get("name", bank_code)))
        out.append(f"<tr><td>{name}</td><td class='num'>{usd}</td><td>{usd_source}</td><td class='num'>{eur}</td><td>{eur_source}</td></tr>")
    out.append("</table>")

    out.append("<h2>RUB transfer calculations — final rates and coefficients</h2>")
    out.append("<p>These are the complete deterministic calculations used for transfer services. The calculation details remain on this monitoring site; production publication receives only the final rate.</p>")
    out.append("<table><tr><th>Service</th><th>Bank</th><th>Base source</th><th>Base RUB</th><th>Coefficient</th><th>Raw result</th><th>Final rate</th><th>Status</th></tr>")
    calc_rows = [r for r in calculated.get("rates", []) if r.get("currency_code") == "RUB"]
    for row in calc_rows:
        status = html.escape(str(row.get("status", "")))
        cls = "warning" if status in {"anomaly", "stale"} else ""
        out.append(
            f"<tr class='{cls}'><td>{html.escape(str(row.get('service_slug','')))}</td>"
            f"<td>{html.escape(str(row.get('bank_name') or row.get('bank_code','')))}</td>"
            f"<td>{html.escape(str(row.get('base_source_kind','')))}</td>"
            f"<td class='num'>{fmt_rate(row.get('base_rate'))}</td>"
            f"<td class='num'>{fmt_rate(row.get('coefficient'))}</td>"
            f"<td class='num'>{fmt_rate(row.get('raw_calculated_rate'))}</td>"
            f"<td class='num'><b>{fmt_rate(row.get('final_rate'))}</b></td>"
            f"<td>{status}</td></tr>"
        )
    out.append("</table>")

    out.append("<h2>Official NBT rates</h2><table><tr><th>Currency</th><th>Rate</th></tr>")
    for currency in ("USD", "EUR", "RUB"):
        row = nbt.get("rates", {}).get(currency, {}) or {}
        out.append(f"<tr><td>{currency}</td><td class='num'>{fmt_rate(first_number(row, 'per_unit', 'rate', 'value'))}</td></tr>")
    out.append("</table>")
    out.append("<p class='muted'>Official NBT rates are separate from commercial-bank Card Buy rates. Alif API fallback is used only for RUB transfer-base calculation and never for USD/EUR bank publication.</p>")

    out.append("<h2>Run status</h2>")
    out.append(f"<p>Calculated rows: <b>{len(calculated.get('rates', []))}</b> · Anomalies: <b>{int(calculated.get('anomaly_count', 0) or 0)}</b> · NBT status: <b>{html.escape(str(calculated.get('nbt_status', 'unknown')))}</b></p>")
    out.append("</body></html>")
    (ROOT / "index.html").write_text("\n".join(out), encoding="utf-8")


if __name__ == "__main__":
    main()
