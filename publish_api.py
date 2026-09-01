from __future__ import annotations

import json
from pathlib import Path

SITE = Path("site")
API = SITE / "api"


def load(name: str, default):
    path = SITE / name
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write(name: str, payload: object) -> None:
    (API / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    API.mkdir(parents=True, exist_ok=True)
    calculated = load("calculated_rates.json", {})
    results = load("results.json", {})

    rates = calculated.get("rates", [])
    rub = [r for r in rates if r.get("currency_code") == "RUB" and r.get("service_slug") in {"t-bank", "sberbank"}]
    nbt = [r for r in rates if r.get("service_slug") == "nbt-reference" and r.get("status") in {"ok", "stale"}]
    cards = [r for r in rates if r.get("service_slug") == "bank-card" and r.get("status") == "ok"]

    envelope = {
        "schema_version": 1,
        "generated_at": calculated.get("generated_at") or results.get("generated_at"),
        "source": "tajik-rate-monitor",
        "status": "ok" if not calculated.get("anomalies") else "needs_review",
    }
    write("rates.json", {**envelope, "rates": rub})
    write("nbt.json", {**envelope, "rates": nbt, "nbt_status": calculated.get("nbt_status"), "nbt_stale": calculated.get("nbt_stale", False)})
    write("banks.json", {**envelope, "rates": cards})
    write("calculated.json", {**envelope, "anomaly_count": calculated.get("anomaly_count", 0), "anomalies": calculated.get("anomalies", []), "rates": rates})
    write("index.json", {"schema_version": 1, "generated_at": envelope["generated_at"], "endpoints": ["rates.json", "nbt.json", "banks.json", "calculated.json"]})


if __name__ == "__main__":
    main()
