from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

CALCULATED = Path("site/calculated_rates.json")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
SCAN_MODE = os.getenv("RATE_SCAN_MODE", "daily").strip().lower()


def headers() -> dict[str, str]:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }


def post(table: str, payload: object) -> None:
    response = requests.post(f"{SUPABASE_URL}/rest/v1/{table}", headers=headers(), json=payload, timeout=20)
    if not response.ok:
        raise RuntimeError(f"{table}: HTTP {response.status_code}: {response.text[:500]}")


def rpc(function_name: str, payload: dict) -> dict:
    response = requests.post(f"{SUPABASE_URL}/rest/v1/rpc/{function_name}", headers=headers(), json=payload, timeout=20)
    if not response.ok:
        raise RuntimeError(f"RPC {function_name}: HTTP {response.status_code}: {response.text[:500]}")
    data = response.json()
    return data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else {"result": data})


def main() -> None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("Supabase credentials are required for automated publishing.")

    data = json.loads(CALCULATED.read_text(encoding="utf-8"))
    anomalies = data.get("anomalies", [])
    all_rates = data.get("rates", [])

    if SCAN_MODE == "rub":
        rows_to_sync = [
            row for row in all_rates
            if row.get("currency_code") == "RUB" and row.get("service_slug") in {"t-bank", "sberbank"}
        ]
    elif SCAN_MODE == "daily":
        rows_to_sync = all_rates
    else:
        raise RuntimeError(f"Unsupported RATE_SCAN_MODE: {SCAN_MODE}")

    run_id = str(uuid.uuid4())
    status = "needs_review" if anomalies else "staged"

    post("rate_calculation_runs", {
        "id": run_id,
        "generated_at": data.get("generated_at") or datetime.now(timezone.utc).isoformat(),
        "source_commit": os.getenv("GITHUB_SHA"),
        "status": status,
        "anomaly_count": len(anomalies),
        "warning_sent": bool(anomalies),
        "notes": f"GitHub deterministic calculation pipeline; scan_mode={SCAN_MODE}",
    })

    rows = []
    for row in rows_to_sync:
        item = dict(row)
        item["run_id"] = run_id
        rows.append(item)
    if rows:
        post("rate_calculation_staging", rows)

    published = None
    if not anomalies and rows:
        published = rpc("publish_rate_calculation_run", {"p_run_id": run_id})
    elif not anomalies and not rows:
        raise RuntimeError(f"No publishable rows produced for scan mode '{SCAN_MODE}'.")

    print(json.dumps({
        "run_id": run_id,
        "scan_mode": SCAN_MODE,
        "status": "published" if published is not None else status,
        "rows": len(rows),
        "anomalies": len(anomalies),
        "publish_result": published,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
