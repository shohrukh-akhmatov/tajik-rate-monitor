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


def headers() -> dict[str, str]:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }


def post(table: str, payload: object) -> None:
    response = requests.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=headers(),
        json=payload,
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"{table}: HTTP {response.status_code}: {response.text[:500]}")


def main() -> None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Supabase staging skipped: add SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY to GitHub Actions secrets.")
        return

    data = json.loads(CALCULATED.read_text(encoding="utf-8"))
    anomalies = data.get("anomalies", [])
    run_id = str(uuid.uuid4())
    status = "needs_review" if anomalies else "staged"

    post(
        "rate_calculation_runs",
        {
            "id": run_id,
            "generated_at": data.get("generated_at") or datetime.now(timezone.utc).isoformat(),
            "source_commit": os.getenv("GITHUB_SHA"),
            "status": status,
            "anomaly_count": len(anomalies),
            "warning_sent": bool(anomalies),
            "notes": "GitHub deterministic calculation pipeline",
        },
    )

    rows = []
    for row in data.get("rates", []):
        item = dict(row)
        item["run_id"] = run_id
        rows.append(item)
    if rows:
        post("rate_calculation_staging", rows)

    print(json.dumps({"run_id": run_id, "status": status, "rows": len(rows), "anomalies": len(anomalies)}))


if __name__ == "__main__":
    main()
