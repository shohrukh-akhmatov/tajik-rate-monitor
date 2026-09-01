from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from monitor import USER_AGENT

TZ = ZoneInfo("Asia/Dushanbe")
RESULTS = "site/results.json"
NBT_API = "https://nbt.tj/en/kurs/rate_export.php"
NBT_HTML = "https://nbt.tj/en/kurs/kurs.php"
ALIF_API = "https://alif.tj/api/rates"
NBT_CACHE = Path("site/nbt_last_valid.json")
CURRENCIES = {"USD": (5.0, 20.0), "EUR": (7.0, 20.0), "RUB": (0.05, 0.20), "CNY": (0.5, 3.0), "KZT": (0.01, 0.20)}
NBT_CODES = {"840": "USD", "978": "EUR", "810": "RUB", "156": "CNY", "398": "KZT"}
MAX_STALE_DAYS = 7


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def parse_nbt_api(data: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "source": NBT_API,
        "source_type": "official_api_primary",
        "status": "error",
        "updated_at": None,
        "currencies": list(NBT_CODES.values()),
        "rates": {},
    }
    if not isinstance(data, dict) or data.get("success") is not True:
        out["error"] = "NBT API response is not a successful payload."
        return out
    records = data.get("data")
    if not isinstance(records, list):
        out["error"] = "NBT API data field is missing or not a list."
        return out
    out["updated_at"] = (data.get("period_info") or {}).get("date")
    for item in records:
        if not isinstance(item, dict):
            continue
        code = str(item.get("Code", "")).zfill(3)
        currency = NBT_CODES.get(code)
        if not currency:
            continue
        try:
            nominal = float(str(item.get("Nominal", "1")).replace(",", "."))
            rate = float(str(item["Rate"]).replace(",", "."))
        except (KeyError, TypeError, ValueError):
            continue
        if nominal <= 0:
            continue
        out["rates"][currency] = {
            "rate": rate,
            "nominal": nominal,
            "per_unit": rate / nominal,
            "date": item.get("Date") or out["updated_at"],
        }
    if all(currency in out["rates"] for currency in NBT_CODES.values()):
        out["status"] = "ok"
    elif out["rates"]:
        out["status"] = "partial"
        out["missing"] = [c for c in NBT_CODES.values() if c not in out["rates"]]
    else:
        out["error"] = "No configured NBT currency rows recognized in API response."
    return out


def parse_nbt_html(html: str) -> dict[str, Any]:
    from html import unescape
    out: dict[str, Any] = {
        "source": NBT_HTML,
        "source_type": "official_html_fallback",
        "status": "error",
        "updated_at": None,
        "currencies": list(NBT_CODES.values()),
        "rates": {},
    }
    try:
        text = unescape(re.sub(r"<[^>]+>", " ", html))
        text = re.sub(r"\s+", " ", text)
        # The HTML fallback is intentionally conservative. The API remains primary.
        for code, currency in NBT_CODES.items():
            m = re.search(rf"\b{code}\b\s+(\d+(?:\.\d+)?)\s+[^0-9]+\s+([0-9]+[.,][0-9]+)", text)
            if m:
                nominal = float(m.group(1))
                rate = float(m.group(2).replace(",", "."))
                out["rates"][currency] = {"rate": rate, "nominal": nominal, "per_unit": rate / nominal}
        if len(out["rates"]) == len(NBT_CODES):
            out["status"] = "ok"
        elif out["rates"]:
            out["status"] = "partial"
            out["missing"] = [c for c in NBT_CODES.values() if c not in out["rates"]]
        else:
            out["error"] = "No NBT currency rows recognized in HTML."
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def valid_nbt(out: dict[str, Any]) -> bool:
    return out.get("status") == "ok" and all(c in (out.get("rates") or {}) for c in ("RUB", "USD", "EUR"))


def cache_nbt(out: dict[str, Any]) -> None:
    cache = dict(out)
    cache["cached_at"] = now_iso()
    cache["cache_source"] = out.get("source")
    NBT_CACHE.parent.mkdir(parents=True, exist_ok=True)
    NBT_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def load_cached_nbt() -> dict[str, Any] | None:
    try:
        cached = json.loads(NBT_CACHE.read_text(encoding="utf-8"))
        cached_at = datetime.fromisoformat(cached["cached_at"])
        age_days = (datetime.now(TZ) - cached_at.astimezone(TZ)).total_seconds() / 86400
        if age_days > MAX_STALE_DAYS:
            return None
        cached["status"] = "stale_fallback"
        cached["source_type"] = "last_valid_official_nbt"
        cached["stale"] = True
        cached["stale_age_days"] = round(age_days, 3)
        cached["current_scan_at"] = now_iso()
        return cached
    except Exception:
        return None


def nbt_rates() -> dict[str, Any]:
    """NBT API primary, official HTML fallback, then last valid NBT snapshot."""
    errors: list[str] = []
    try:
        r = requests.get(NBT_API, timeout=15, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        r.raise_for_status()
        out = parse_nbt_api(r.json())
        out["api_http_status"] = r.status_code
        if valid_nbt(out):
            cache_nbt(out)
            return out
        errors.append(out.get("error") or f"NBT API status: {out['status']}")
    except Exception as exc:
        errors.append(f"API {type(exc).__name__}: {exc}")

    try:
        r = requests.get(NBT_HTML, timeout=20, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        out = parse_nbt_html(r.text)
        if valid_nbt(out):
            cache_nbt(out)
            return out
        errors.append(out.get("error") or f"NBT HTML status: {out['status']}")
    except Exception as exc:
        errors.append(f"HTML {type(exc).__name__}: {exc}")

    cached = load_cached_nbt()
    if cached:
        cached["fallback_errors"] = errors
        return cached

    return {
        "source": NBT_API,
        "source_type": "official_api_primary",
        "status": "error",
        "currencies": list(NBT_CODES.values()),
        "rates": {},
        "error": "; ".join(errors),
    }


def alif_api() -> dict[str, Any]:
    """Keep Alif only as the RUB fallback source for T-Bank/Sber calculations."""
    out = {"source": ALIF_API, "status": "error", "rates": {}}
    try:
        r = requests.get(ALIF_API, timeout=15, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        r.raise_for_status()
        data = r.json()
        out["status"] = "ok"
        out["rates"] = find_alif_quotes(data)
        out["raw_shape"] = type(data).__name__
        if not out["rates"]:
            out["note"] = "API responded, but no recognized buy/sell fields were found."
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def find_alif_quotes(value: Any, found: dict[str, dict[str, float | None]] | None = None):
    if found is None:
        found = {}
    if isinstance(value, dict):
        code = next((str(value.get(k)).upper() for k in ("currency", "currencyCode", "code", "ccy", "symbol", "ticker") if isinstance(value.get(k), str) and str(value.get(k)).upper() in CURRENCIES), None)
        if code:
            buy = sell = None
            for key, raw in value.items():
                if not isinstance(raw, (int, float, str)):
                    continue
                try:
                    num = float(str(raw).replace(",", "."))
                except ValueError:
                    continue
                if not CURRENCIES[code][0] <= num <= CURRENCIES[code][1]:
                    continue
                k = str(key).lower()
                if any(x in k for x in ("buy", "purchase", "bid", "pokup")):
                    buy = num
                elif any(x in k for x in ("sell", "sale", "ask", "prodaj")):
                    sell = num
            if buy is not None or sell is not None:
                found[code] = {"buy": buy, "sell": sell}
        for child in value.values():
            find_alif_quotes(child, found)
    elif isinstance(value, list):
        for child in value:
            find_alif_quotes(child, found)
    return found


async def main() -> None:
    with open(RESULTS, encoding="utf-8") as f:
        payload = json.load(f)
    payload["reference_rates"] = {
        "collected_at": now_iso(),
        "currencies": list(NBT_CODES.values()),
        "nbt": nbt_rates(),
        "alif_api": alif_api(),
        "bank_usd_eur_policy": "NBT commercial-bank data is the sole source for USD/EUR; use the last valid NBT snapshot when NBT does not publish fresh data.",
        "note": "Separate bank USD/EUR scrapers are disabled. NBT official JSON API is primary, official HTML is fallback, and the last valid NBT snapshot is used for temporary gaps.",
    }
    with open(RESULTS, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    nbt = payload["reference_rates"]["nbt"]
    print(json.dumps({
        "nbt_status": nbt["status"],
        "nbt_currencies_found": sorted((nbt.get("rates") or {}).keys()),
        "nbt_source_type": nbt.get("source_type"),
        "nbt_stale": nbt.get("stale", False),
        "alif_api_status": payload["reference_rates"]["alif_api"]["status"],
        "bank_usd_eur_source": "NBT only",
    }, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
