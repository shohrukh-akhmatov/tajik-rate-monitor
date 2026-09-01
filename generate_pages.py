from __future__ import annotations

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


def main():
    data = load_json(ROOT / "data" / "normalized.json", default={})
    html = ["<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>", "<title>Tajikistan Exchange Rates</title>", "<style>body{font-family:system-ui,sans-serif;max-width:1100px;margin:32px auto;padding:0 16px;color:#222}table{border-collapse:collapse;width:100%;margin:12px 0 28px}th,td{padding:8px;border:1px solid #ddd;text-align:left}th{background:#f5f5f5}.muted{color:#666}</style></head><body>"]
    html.append("<h1>Tajikistan Exchange Rates</h1>")
    html.append(f"<p class='muted'>Updated: {datetime.now(timezone.utc).isoformat()}</p>")
    html.append("<h2>USD / EUR — NBT commercial bank card buy rates</h2>")
    html.append("<table><tr><th>Bank</th><th>USD buy</th><th>EUR buy</th></tr>")
    banks = data.get("reference_rates", {}).get("commercial_banks", {})
    for bank, row in sorted(banks.items()):
        html.append(f"<tr><td>{bank}</td><td>{fmt_rate(row.get('USD', {}).get('card_buy'))}</td><td>{fmt_rate(row.get('EUR', {}).get('card_buy'))}</td></tr>")
    html.append("</table>")
    html.append("<p class='muted'>These USD/EUR values are collected from the NBT commercial-bank table (Card Buy). They are shown for monitoring/quality checking and are not the official NBT rates.</p>")
    html.append("<h2>Official NBT rates</h2><pre>")
    html.append(json.dumps(data.get("nbt", {}), ensure_ascii=False, indent=2))
    html.append("</pre></body></html>")
    (ROOT / "index.html").write_text("\n".join(html), encoding="utf-8")


if __name__ == "__main__":
    main()
