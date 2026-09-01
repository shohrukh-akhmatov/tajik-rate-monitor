from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from reference_rates import alif_api

PUBLIC_RESULTS = "https://shohrukh-akhmatov.github.io/tajik-rate-monitor/results.json"
RESULTS = Path("site/results.json")
TZ = ZoneInfo("Asia/Dushanbe")


def main() -> None:
    payload = json.loads(RESULTS.read_text(encoding="utf-8"))
    previous = None
    try:
        response = requests.get(
            PUBLIC_RESULTS,
            params={"t": int(datetime.now().timestamp())},
            timeout=12,
            headers={"Cache-Control": "no-cache", "User-Agent": "TajikRateMonitor/1.2"},
        )
        if response.ok:
            previous = response.json()
    except Exception:
        pass

    previous_reference = (previous or {}).get("reference_rates") or {}
    payload["reference_rates"] = {
        "collected_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "currencies": ["USD", "EUR", "RUB", "CNY", "KZT"],
        "nbt": previous_reference.get("nbt") or {"status": "stale", "rates": {}, "commercial_banks": {}},
        "alif_api": alif_api(),
        "bank_usd_eur_policy": "NBT commercial-bank Credit Cards Buy is refreshed only by the daily NBT scan.",
        "note": "RUB-only scan: NBT/USD/EUR data are not recollected; the last published NBT snapshot is retained for calculation compatibility.",
    }
    RESULTS.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
