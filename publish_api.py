from __future__ import annotations

import json
from pathlib import Path

SITE = Path("site")
API = SITE / "api"


def load(name: str, default):
    path = SITE / name
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write(name: str, payload: object) -> None:
    (API / name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> None:
    API.mkdir(parents=True, exist_ok=True)
    calculated = load("calculated_rates.json", {})
    results = load("results.json", {})

    rates = calculated.get("rates", []) if isinstance(calculated, dict) else []
    if not isinstance(rates, list):
        rates = []

    rub = [
        r for r in rates
        if r.get("currency_code") == "RUB"
        and r.get("service_slug") in {"t-bank", "sberbank"}
        and r.get("final_rate") is not None
        and r.get("status") in {"ok", "stale"}
    ]
    nbt = [
        r for r in rates
        if r.get("service_slug") == "nbt-reference"
        and r.get("currency_code") in {"RUB", "USD", "EUR"}
        and r.get("final_rate") is not None
        and r.get("status") in {"ok", "stale"}
    ]
    cards = [
        r for r in rates
        if r.get("service_slug") == "bank-card"
        and r.get("currency_code") in {"USD", "EUR"}
        and r.get("final_rate") is not None
        and r.get("status") == "ok"
    ]

    # A missing NBT USD/EUR quote means that currency is not supported by
    # the NBT source for this run. Never manufacture or substitute it here.
    nbt_currencies = {r.get("currency_code") for r in nbt}
    cards = [r for r in cards if r.get("currency_code") in nbt_currencies]

    anomalies = calculated.get("anomalies", []) if isinstance(calculated, dict) else []
    if not isinstance(anomalies, list):
        anomalies = []

    generated_at = (
        calculated.get("generated_at") if isinstance(calculated, dict) else None
    ) or (results.get("generated_at") if isinstance(results, dict) else None)
    envelope = {
        "schema_version": 2,
        "generated_at": generated_at,
        "source": "tajik-rate-monitor",
        "status": "ok" if not anomalies else "needs_review",
    }

    write("rates.json", {**envelope, "rates": rub})
    write(
        "nbt.json",
        {
            **envelope,
            "rates": nbt,
            "nbt_status": calculated.get("nbt_status"),
            "nbt_stale": calculated.get("nbt_stale", False),
        },
    )
    write("banks.json", {**envelope, "rates": cards})
    write(
        "calculated.json",
        {
            **envelope,
            "anomaly_count": len(anomalies),
            "anomalies": anomalies,
            "rates": rates,
        },
    )
    write(
        "index.json",
        {
            "schema_version": 2,
            "generated_at": generated_at,
            "endpoints": [
                "rates.json",
                "nbt.json",
                "banks.json",
                "calculated.json",
            ],
        },
    )


if __name__ == "__main__":
    main()
