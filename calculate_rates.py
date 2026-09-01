from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

TZ = ZoneInfo("Asia/Dushanbe")
RESULTS = Path("site/results.json")
RULES = Path("config/rate_rules.json")
PUBLIC_RESULTS = "https://shohrukh-akhmatov.github.io/tajik-rate-monitor/results.json"
PUBLIC_CALCULATED = "https://shohrukh-akhmatov.github.io/tajik-rate-monitor/calculated_rates.json"


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def pct_change(old: float | None, new: float | None) -> float | None:
    if old in (None, 0) or new is None:
        return None
    return abs(float(new) - float(old)) / abs(float(old)) * 100


def fetch_json(url: str) -> dict | None:
    try:
        response = requests.get(url, params={"t": int(datetime.now().timestamp())}, timeout=12, headers={"Cache-Control": "no-cache", "User-Agent": "TajikRateMonitor/1.4"})
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
    rules = json.loads(RULES.read_text(encoding="utf-8"))
    banks = {bank["id"]: bank for bank in payload.get("banks", [])}
    previous = fetch_json(PUBLIC_RESULTS)
    previous_calculated = fetch_json(PUBLIC_CALCULATED)
    previous_ref = (previous or {}).get("reference_rates", {})
    previous_nbt = previous_ref.get("nbt", {}).get("rates", {})
    previous_rates = (previous_calculated or {}).get("rates", [])

    reference = payload.get("reference_rates") or {}
    alif_api_rub = ((reference.get("alif_api") or {}).get("rates") or {}).get("RUB") or {}
    alif_api_base = alif_api_rub.get("buy")

    # Alif API is the primary fallback. If the API responds without a RUB quote,
    # use the current normalized Alif transfer observation, then the last
    # validated RUB base. This prevents temporary API schema/source gaps from
    # producing false MISSING_BASE warnings for IBT/Spitamen/Vasl.
    alif_bank = banks.get("alif", {})
    alif_transfer = (alif_bank.get("rates") or {}).get("transfer") or {}
    alif_observed = alif_transfer.get("buy_per_1000")
    alif_observed_base = float(alif_observed) / 1000.0 if alif_observed is not None else None

    last_valid_alif = None
    for row in previous_rates:
        if row.get("currency_code") == "RUB" and row.get("bank_code") in {"alif", "ibt", "spitamen", "vasl"} and valid_rub(row.get("base_rate")):
            last_valid_alif = float(row["base_rate"])
            break

    if valid_rub(alif_api_base):
        alif_base = float(alif_api_base)
        alif_source_kind = "alif_api"
    elif valid_rub(alif_observed_base):
        alif_base = float(alif_observed_base)
        alif_source_kind = "alif_transfer_observation"
    elif valid_rub(last_valid_alif):
        alif_base = last_valid_alif
        alif_source_kind = "last_valid_rub_base"
    else:
        alif_base = None
        alif_source_kind = "missing"

    rows: list[dict] = []
    anomalies: list[dict] = []
    generated_at = payload.get("generated_at") or now_iso()
    fallback_banks = set(rules["base_rate_policy"]["fallback_for"])
    anomaly_rules = rules["anomaly_rules"]
    old_banks = {b.get("id"): b for b in (previous or {}).get("banks", [])}

    for service_slug, service in rules["services"].items():
        coefficients = service.get("coefficients", {})
        for bank_code, coefficient in coefficients.items():
            bank = banks.get(bank_code, {})
            transfer = (bank.get("rates") or {}).get("transfer") or {}
            direct_buy_per_1000 = transfer.get("buy_per_1000")
            use_alif = bank_code in fallback_banks

            if use_alif:
                base_rate = alif_base
                base_source_bank_code = "alif"
                base_source_kind = alif_source_kind
            elif direct_buy_per_1000 is not None and valid_rub(float(direct_buy_per_1000) / 1000.0):
                base_rate = float(direct_buy_per_1000) / 1000.0
                base_source_bank_code = bank_code
                base_source_kind = "bank_transfer_observation"
            else:
                # Temporary loss of a bank transfer selector should not destroy
                # a previously valid calculation. Reuse the previous bank base.
                previous_bank = old_banks.get(bank_code, {})
                old_buy = ((previous_bank.get("rates") or {}).get("transfer") or {}).get("buy_per_1000")
                if old_buy is not None and valid_rub(float(old_buy) / 1000.0):
                    base_rate = float(old_buy) / 1000.0
                    base_source_bank_code = bank_code
                    base_source_kind = "last_valid_bank_transfer"
                else:
                    base_rate = None
                    base_source_bank_code = bank_code
                    base_source_kind = "missing"

            if base_rate is None:
                anomalies.append({"service_slug": service_slug, "bank_code": bank_code, "code": "MISSING_BASE", "message": "No usable base rate was found after bank, Alif API, Alif observation, and last-valid fallbacks."})
                continue

            old_base = None
            if use_alif:
                old_base = ((previous_ref.get("alif_api") or {}).get("rates") or {}).get("RUB", {}).get("buy")
                if not valid_rub(old_base):
                    old_base = last_valid_alif
            else:
                previous_bank = old_banks.get(bank_code, {})
                old_buy = ((previous_bank.get("rates") or {}).get("transfer") or {}).get("buy_per_1000")
                old_base = float(old_buy) / 1000.0 if old_buy is not None and valid_rub(float(old_buy) / 1000.0) else None

            change = pct_change(old_base, base_rate)
            anomaly_code = None
            anomaly_message = None
            # Do not create a false jump warning when the value is explicitly
            # marked as a last-valid fallback.
            if change is not None and change > anomaly_rules["max_rub_base_change_pct"] and base_source_kind not in {"last_valid_bank_transfer", "last_valid_rub_base"}:
                anomaly_code = "BASE_JUMP"
                anomaly_message = f"Base rate changed by {change:.2f}%"
                anomalies.append({"service_slug": service_slug, "bank_code": bank_code, "code": anomaly_code, "message": anomaly_message})

            raw = base_rate * float(coefficient)
            stale = base_source_kind in {"last_valid_bank_transfer", "last_valid_rub_base"}
            rows.append({"service_slug": service_slug, "bank_code": bank_code, "bank_name": bank.get("name", bank_code), "currency_code": "RUB", "base_rate": base_rate, "base_source_bank_code": base_source_bank_code, "base_source_kind": base_source_kind, "coefficient": float(coefficient), "raw_calculated_rate": raw, "final_rate": round(raw, rules["rounding"]["published_rate_decimals"]), "sample_source_amount": rules["rounding"]["sample_source_amount"], "sample_target_amount": round(raw * rules["rounding"]["sample_source_amount"], 4), "status": "stale" if stale else ("anomaly" if anomaly_code else "ok"), "anomaly_code": anomaly_code, "anomaly_message": anomaly_message, "source_observed_at": generated_at})

    # Official NBT reference rates remain separate from commercial-bank rates.
    nbt = reference.get("nbt") or {}
    for currency in ("RUB", "USD", "EUR"):
        item = (nbt.get("rates") or {}).get(currency)
        if not item:
            anomalies.append({"service_slug": "nbt-reference", "bank_code": "nbt", "code": "NBT_MISSING", "message": f"{currency} missing from NBT and no valid cached value is available"})
            continue

        value = float(item["rate"])
        if currency == "RUB":
            bounds = (anomaly_rules["min_nbt_rub"], anomaly_rules["max_nbt_rub"])
        elif currency == "USD":
            bounds = (anomaly_rules["min_nbt_usd"], anomaly_rules["max_nbt_usd"])
        else:
            bounds = (anomaly_rules["min_nbt_eur"], anomaly_rules["max_nbt_eur"])

        bad = not bounds[0] <= value <= bounds[1]
        old = (previous_nbt.get(currency) or {}).get("rate")
        change = pct_change(old, value)
        if currency in ("USD", "EUR") and change is not None and change > anomaly_rules["max_usd_eur_change_pct"] and not nbt.get("stale"):
            bad = True
            anomalies.append({"service_slug": "nbt-reference", "bank_code": "nbt", "code": "NBT_FX_JUMP", "message": f"{currency} changed by {change:.2f}%"})
        if not bounds[0] <= value <= bounds[1]:
            anomalies.append({"service_slug": "nbt-reference", "bank_code": "nbt", "code": "NBT_OUTLIER", "message": f"{currency} NBT value {value} is outside configured bounds"})

        rows.append({"service_slug": "nbt-reference", "bank_code": "nbt", "bank_name": "National Bank of Tajikistan", "currency_code": currency, "base_rate": value, "base_source_bank_code": "nbt", "base_source_kind": "last_valid_official_nbt" if nbt.get("stale") else "official_nbt", "coefficient": 1.0, "raw_calculated_rate": value, "final_rate": value, "sample_source_amount": item.get("nominal") or 1, "sample_target_amount": value, "status": "stale" if nbt.get("stale") and not bad else ("anomaly" if bad else "ok"), "anomaly_code": "NBT_OUTLIER" if not bounds[0] <= value <= bounds[1] else None, "anomaly_message": "Using last valid NBT publication because the current scan has no fresh NBT data." if nbt.get("stale") and not bad else None, "source_observed_at": item.get("date")})

    # USD/EUR commercial-bank rates are valid only when NBT explicitly publishes
    # Card Buy for that bank/currency. Missing means do not publish.
    bank_fx = (reference.get("nbt") or {}).get("commercial_banks") or {}
    commercial_stale = set((reference.get("nbt") or {}).get("commercial_banks_stale") or [])
    for bank_code, bank_data in bank_fx.items():
        bank_name = bank_data.get("name", bank_code)
        for currency in ("USD", "EUR"):
            stale_key = f"{bank_code}:{currency}"
            quote = (bank_data.get(currency) or {}).get("card_buy")
            if quote is None or stale_key in commercial_stale:
                continue
            value = float(quote)
            rows.append({"service_slug": "bank-card", "bank_code": bank_code, "bank_name": bank_name, "currency_code": currency, "base_rate": value, "base_source_bank_code": "nbt", "base_source_kind": "nbt_commercial_bank_card_buy", "coefficient": 1.0, "raw_calculated_rate": value, "final_rate": value, "sample_source_amount": 1, "sample_target_amount": value, "status": "ok", "anomaly_code": None, "anomaly_message": None, "source_observed_at": bank_data.get("date") or nbt.get("updated_at")})

    output = {"generated_at": generated_at, "rules_version": rules["version"], "anomaly_count": len(anomalies), "anomalies": anomalies, "rates": rows, "nbt_status": nbt.get("status"), "nbt_stale": bool(nbt.get("stale")), "nbt_stale_age_days": nbt.get("stale_age_days"), "alif_fallback_rub": alif_base, "alif_fallback_source": alif_source_kind}
    Path("site/calculated_rates.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    Path("site/anomalies.json").write_text(json.dumps({"generated_at": generated_at, "anomalies": anomalies}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"rows": len(rows), "anomalies": len(anomalies), "nbt_status": nbt.get("status"), "nbt_stale": bool(nbt.get("stale")), "alif_fallback_rub": alif_base, "alif_fallback_source": alif_source_kind}, ensure_ascii=False))


if __name__ == "__main__":
    main()
