from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from reference_rates import alif_api

PUBLIC_RESULTS = "https://shohrukh-akhmatov.github.io/tajik-rate-monitor/results.json"
PUBLIC_CALCULATED = "https://shohrukh-akhmatov.github.io/tajik-rate-monitor/calculated_rates.json"
RESULTS = Path("site/results.json")
TZ = ZoneInfo("Asia/Dushanbe")


def fetch_json(url: str) -> dict | None:
    try:
        response = requests.get(url, params={"t": int(datetime.now().timestamp())}, timeout=12, headers={"Cache-Control": "no-cache", "User-Agent": "TajikRateMonitor/1.3"})
        return response.json() if response.ok else None
    except Exception:
        return None


def valid_rub(value: object) -> bool:
    try:
        value = float(value)
        return 0.05 <= value <= 0.20
    except (TypeError, ValueError):
        return False


def main() -> None:
    payload = json.loads(RESULTS.read_text(encoding="utf-8"))
    previous = fetch_json(PUBLIC_RESULTS)
    previous_calculated = fetch_json(PUBLIC_CALCULATED)
    previous_reference = (previous or {}).get("reference_rates") or {}
    previous_rates = (previous_calculated or {}).get("rates") or []

    # Keep the last validated RUB base for temporary source failures. A missing
    # website/API response must not become a false MISSING_BASE anomaly.
    last_valid_base: dict[tuple[str, str], float] = {}
    for row in previous_rates:
        if row.get("currency_code") != "RUB":
            continue
        bank_code = row.get("bank_code")
        service_slug = row.get("service_slug")
        base = row.get("base_rate")
        if bank_code and service_slug and valid_rub(base):
            last_valid_base.setdefault((service_slug, bank_code), float(base))
            last_valid_base.setdefault(("*", bank_code), float(base))

    alif = alif_api() or {"status": "error", "rates": {}}
    alif_rates = alif.setdefault("rates", {})
    alif_rub = alif_rates.get("RUB") or {}

    # Alif is the configured fallback for IBT/Spitamen/Vasl. If its endpoint is
    # temporarily unavailable, reuse the last validated fallback base.
    if not valid_rub(alif_rub.get("buy")):
        candidates = [
            float(row["base_rate"])
            for row in previous_rates
            if row.get("currency_code") == "RUB"
            and row.get("bank_code") in {"ibt", "spitamen", "vasl", "alif"}
            and valid_rub(row.get("base_rate"))
        ]
        if candidates:
            alif_rates["RUB"] = {"currency": "RUB", "buy": candidates[0], "sell": None, "source": "last_valid_calculated_rate", "stale": True, "fetched_at": datetime.now(TZ).isoformat(timespec="seconds")}
            alif["status"] = "stale_fallback"

    payload["reference_rates"] = {
        "collected_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "currencies": ["USD", "EUR", "RUB", "CNY", "KZT"],
        "nbt": previous_reference.get("nbt") or {"status": "stale", "rates": {}, "commercial_banks": {}},
        "alif_api": alif,
        "bank_usd_eur_policy": "NBT commercial-bank Credit Cards Buy is refreshed only by the daily NBT scan.",
        "note": "RUB-only scan: NBT/USD/EUR data are not recollected; the last published NBT snapshot is retained for calculation compatibility.",
    }

    # For any bank whose transfer table temporarily disappears, restore only the
    # missing observation from the last validated calculation. Fresh scraper data
    # always win. The restored value is explicitly marked stale.
    for bank in payload.get("banks", []):
        bank_code = bank.get("id")
        if not bank_code:
            continue
        rates = bank.setdefault("rates", {})
        transfer = rates.get("transfer") or {}
        current = transfer.get("buy_per_1000")
        if current is not None and valid_rub(float(current) / 1000.0):
            continue

        candidate = None
        for service in ("t-bank", "sberbank", "*"):
            candidate = last_valid_base.get((service, bank_code))
            if candidate is not None:
                break
        if candidate is None:
            continue

        rates["transfer"] = {
            **transfer,
            "buy": candidate,
            "sell": transfer.get("sell"),
            "buy_per_1000": round(candidate * 1000, 4),
            "sell_per_1000": transfer.get("sell_per_1000"),
            "selector_found": False,
            "raw": "last_valid_calculated_base",
            "stale": True,
            "fallback_source": "last_valid_calculated_rate",
        }
        bank["rates"] = rates

    RESULTS.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
