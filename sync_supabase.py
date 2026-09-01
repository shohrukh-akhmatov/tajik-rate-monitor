from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

def _load_env() -> None:
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip("'\"")
            if k and k not in os.environ:
                os.environ[k] = v

_load_env()

CALCULATED = Path("site/calculated_rates.json")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
SCAN_MODE = os.getenv("RATE_SCAN_MODE", "daily").strip().lower()
MAX_ATTEMPTS = 3


def headers() -> dict[str, str]:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }


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
    response = request_with_retry(
        "POST",
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=headers(),
        json=payload,
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"{table}: HTTP {response.status_code}: {response.text[:500]}")


def rpc(function_name: str, payload: dict) -> dict:
    response = request_with_retry(
        "POST",
        f"{SUPABASE_URL}/rest/v1/rpc/{function_name}",
        headers=headers(),
        json=payload,
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"RPC {function_name}: HTTP {response.status_code}: {response.text[:500]}")
    data = response.json()
    return data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else {"result": data})


def get_active_services(slugs: set[str]) -> dict[str, dict]:
    """Return active transfer services required by the RUB publisher."""
    if not slugs:
        return {}
    params = {"slug": f"in.({','.join(sorted(slugs))})", "select": "slug,name,is_active"}
    response = request_with_retry(
        "GET",
        f"{SUPABASE_URL}/rest/v1/transfer_services",
        headers=headers(),
        params=params,
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(
            f"transfer_services preflight: HTTP {response.status_code}: {response.text[:500]}"
        )
    rows = response.json()
    return {row["slug"]: row for row in rows if row.get("is_active")}


def validate_rub_services(publishable: list[dict]) -> None:
    required = {
        row.get("service_slug")
        for row in publishable
        if row.get("currency_code") == "RUB"
        and row.get("service_slug") in {"t-bank", "sberbank"}
    }
    if not required:
        return

    active = get_active_services(required)
    missing_or_inactive = sorted(required - set(active))
    if missing_or_inactive:
        raise RuntimeError(
            "Required RUB transfer services are missing or inactive in Supabase: "
            + ", ".join(missing_or_inactive)
        )


def final_row(row: dict, run_id: str) -> dict:
    """Copy the complete validated calculation into Supabase staging for auditability."""
    return {
        "run_id": run_id,
        "service_slug": row.get("service_slug"),
        "bank_code": row.get("bank_code"),
        "bank_name": row.get("bank_name", row.get("bank_code")),
        "currency_code": row.get("currency_code"),
        "base_rate": row.get("base_rate"),
        "base_source_bank_code": row.get("base_source_bank_code"),
        "base_source_kind": row.get("base_source_kind"),
        "coefficient": row.get("coefficient"),
        "raw_calculated_rate": row.get("raw_calculated_rate"),
        "final_rate": row.get("final_rate"),
        "sample_source_amount": row.get("sample_source_amount", 1000),
        "sample_target_amount": row.get("sample_target_amount"),
        "status": row.get("status", "ok"),
        "anomaly_code": row.get("anomaly_code"),
        "anomaly_message": row.get("anomaly_message"),
        "is_manual_override": bool(row.get("is_manual_override", False)),
        "manual_note": row.get("manual_note"),
        "source_observed_at": row.get("source_observed_at"),
    }


def main() -> None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("Supabase credentials are required for automated publishing.")

    data = json.loads(CALCULATED.read_text(encoding="utf-8"))
    anomalies = data.get("anomalies", [])
    all_rates = data.get("rates", [])

    # In all scan modes, publish all validated calculated rates so NBT and Card rates
    # in Supabase remain fresh and synchronized across every run.
    publishable = all_rates

    # Fail before creating a run if the two production RUB services required by the
    # publisher are absent/inactive. This prevents a misleading partial automation run.
    validate_rub_services(publishable)

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
            "notes": f"Validated final-rate pipeline; scan_mode={SCAN_MODE}. Calculation details are also preserved in Supabase staging.",
        },
    )

    rows = [final_row(row, run_id) for row in publishable]
    if rows:
        post("rate_calculation_staging", rows)

    published = None
    if not anomalies and rows:
        published = rpc("publish_rate_calculation_run", {"p_run_id": run_id})
    elif not anomalies and not rows:
        raise RuntimeError(f"No publishable final rates produced for scan mode '{SCAN_MODE}'.")

    print(
        json.dumps(
            {
                "run_id": run_id,
                "scan_mode": SCAN_MODE,
                "status": "published" if published is not None else status,
                "rows": len(rows),
                "anomalies": len(anomalies),
                "publish_result": published,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
