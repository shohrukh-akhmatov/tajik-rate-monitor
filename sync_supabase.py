from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

CALCULATED = Path("site/calculated_rates.json")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
SCAN_MODE = os.getenv("RATE_SCAN_MODE", "daily").strip().lower()
MAX_ATTEMPTS = 3


def headers() -> dict[str, str]:
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json", "Prefer": "return=minimal"}


def request_with_retry(method: str, url: str, **kwargs) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.request(method, url, **kwargs)
            if response.ok:
                return response
            if response.status_code < 500 and response.status_code not in {408, 429}:
                return response
            last_error = RuntimeError(f"HTTP {response.status_code}: {response.text[:500]}")
        except requests.RequestException as exc:
            last_error = exc
        if attempt < MAX_ATTEMPTS:
            time.sleep(2 ** (attempt - 1))
    assert last_error is not None
    raise last_error


def post(table: str, payload: object) -> None:
    response = request_with_retry("POST", f"{SUPABASE_URL}/rest/v1/{table}", headers=headers(), json=payload, timeout=20)
    if not response.ok:
        raise RuntimeError(f"{table}: HTTP {response.status_code}: {response.text[:500]}")


def rpc(function_name: str, payload: dict) -> dict:
    response = request_with_retry("POST", f"{SUPABASE_URL}/rest/v1/rpc/{function_name}", headers=headers(), json=payload, timeout=20)
    if not response.ok:
        raise RuntimeError(f"RPC {function_name}: HTTP {response.status_code}: {response.text[:500]}")
    data = response.json()
    return data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else {"result": data})


def final_row(row: dict, run_id: str) -> dict:
    """Only the ready-to-publish result crosses into Supabase staging."""
    return {
        "run_id": run_id,
        "service_slug": row.get("service_slug"),
        "bank_code": row.get("bank_code"),
        "bank_name": row.get("bank_name", row.get("bank_code")),
        "currency_code": row.get("currency_code"),
        "final_rate": row.get("final_rate"),
        "sample_source_amount": row.get("sample_source_amount", 1000),
        "sample_target_amount": row.get("sample_target_amount"),
        "status": row.get("status", "ok"),
        "anomaly_code": row.get("anomaly_code"),
        "anomaly_message": row.get("anomaly_message"),
        "is_manual_override": False,
        "source_observed_at": row.get("source_observed_at"),
    }


def main() -> None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("Supabase credentials are required for automated publishing.")

    data = json.loads(CALCULATED.read_text(encoding="utf-8"))
    anomalies = data.get("anomalies", [])
    all_rates = data.get("rates", [])

    if SCAN_MODE == "rub":
        publishable = [r for r in all_rates if r.get("currency_code") == "RUB" and r.get("service_slug") in {"t-bank", "sberbank"}]
    elif SCAN_MODE == "daily":
        publishable = all_rates
    else:
        raise RuntimeError(f"Unsupported RATE_SCAN_MODE: {SCAN_MODE}")

    run_id = str(uuid.uuid4())
    status = "needs_review" if anomalies else "staged"
    post("rate_calculation_runs", {"id": run_id, "generated_at": data.get("generated_at") or datetime.now(timezone.utc).isoformat(), "source_commit": os.getenv("GITHUB_SHA"), "status": status, "anomaly_count": len(anomalies), "warning_sent": bool(anomalies), "notes": f"Validated final-rate pipeline; scan_mode={SCAN_MODE}. Calculation details remain on GitHub Pages."})

    rows = [final_row(row, run_id) for row in publishable]
    if rows:
        post("rate_calculation_staging", rows)

    published = None
    if not anomalies and rows:
        published = rpc("publish_rate_calculation_run", {"p_run_id": run_id})
    elif not anomalies and not rows:
        raise RuntimeError(f"No publishable final rates produced for scan mode '{SCAN_MODE}'.")

    print(json.dumps({"run_id": run_id, "scan_mode": SCAN_MODE, "status": "published" if published is not None else status, "rows": len(rows), "anomalies": len(anomalies), "publish_result": published}, ensure_ascii=False))


if __name__ == "__main__":
    main()
