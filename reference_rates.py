from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from monitor import USER_AGENT

TZ = ZoneInfo("Asia/Dushanbe")
RESULTS = "site/results.json"
NBT_API = "https://nbt.tj/en/kurs/rate_export.php"
NBT_HTML = "https://nbt.tj/en/kurs/kurs.php"
NBT_COMMERCIAL_HTML = "https://nbt.tj/en/kurs/kurs_kommer_bank.php"
ALIF_API = "https://alif.tj/api/rates"
NBT_CACHE = Path("site/nbt_last_valid.json")
CURRENCIES = {"USD": (5.0, 20.0), "EUR": (7.0, 20.0), "RUB": (0.05, 0.20), "CNY": (0.5, 3.0), "KZT": (0.01, 0.20)}
NBT_CODES = {"840": "USD", "978": "EUR", "810": "RUB", "156": "CNY", "398": "KZT"}
MAX_STALE_DAYS = 7
BANK_NAME_MAP = {
    "alif": "alif",
    "amonat": "amonatbank",
    "eskhata": "eskhata",
    "activ": "activbank",
    "humo": "humo",
    "international bank of tajikistan": "ibt",
    "ibt": "ibt",
    "oriyon": "oriyonbank",
    "spitamen": "spitamen",
    "vasl": "vasl",
    "dushanbe city": "dcity",
    "duşanbe city": "dcity",
    "d city": "dcity",
}


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def parse_nbt_api(data: Any) -> dict[str, Any]:
    out = {"source": NBT_API, "source_type": "official_api_primary", "status": "error", "updated_at": None, "currencies": list(NBT_CODES.values()), "rates": {}}
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
        currency = NBT_CODES.get(str(item.get("Code", "")).zfill(3))
        if not currency:
            continue
        try:
            nominal = float(str(item.get("Nominal", "1")).replace(",", "."))
            rate = float(str(item["Rate"]).replace(",", "."))
        except (KeyError, TypeError, ValueError):
            continue
        if nominal <= 0:
            continue
        out["rates"][currency] = {"rate": rate, "nominal": nominal, "per_unit": rate / nominal, "date": item.get("Date") or out["updated_at"]}
    if all(c in out["rates"] for c in NBT_CODES.values()):
        out["status"] = "ok"
    elif out["rates"]:
        out["status"] = "partial"
        out["missing"] = [c for c in NBT_CODES.values() if c not in out["rates"]]
    else:
        out["error"] = "No configured NBT currency rows recognized in API response."
    return out


def parse_nbt_html(html: str) -> dict[str, Any]:
    from html import unescape
    out = {"source": NBT_HTML, "source_type": "official_html_fallback", "status": "error", "updated_at": None, "currencies": list(NBT_CODES.values()), "rates": {}}
    try:
        text = unescape(re.sub(r"<[^>]+>", " ", html))
        text = re.sub(r"\s+", " ", text)
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


def normalize_bank_name(name: str) -> str | None:
    low = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    for key, code in BANK_NAME_MAP.items():
        key_low = re.sub(r"[^a-z0-9]+", " ", key.lower()).strip()
        if key_low in low:
            return code
    return None


def parse_nbt_commercial_html(html: str) -> dict[str, Any]:
    """Extract Credit Cards Buy for USD/EUR from NBT's commercial-bank table."""
    from html import unescape
    out: dict[str, Any] = {"source": NBT_COMMERCIAL_HTML, "source_type": "official_nbt_commercial_cards", "status": "error", "commercial_banks": {}}
    try:
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.I | re.S)
        for row in rows:
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.I | re.S)
            if len(cells) < 13:
                continue
            clean = [re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", c))).strip() for c in cells]
            bank_code = normalize_bank_name(clean[0])
            if not bank_code:
                continue
            try:
                # Table order: name, Interbank B/S, Cash B/S, Non-cash B/S,
                # E-wallet B/S, Credit Cards B/S, NPCR B/S, Date.
                card_buy = float(clean[9].replace(",", "."))
                card_sell = float(clean[10].replace(",", "."))
            except (ValueError, IndexError):
                continue
            date = clean[13] if len(clean) > 13 else None
            if date and not re.search(r"\d", date):
                date = None
            out["commercial_banks"][bank_code] = {
                "name": clean[0],
                "USD": {"card_buy": card_buy, "card_sell": card_sell},
                "EUR": {},
                "date": date,
            }
        if out["commercial_banks"]:
            out["status"] = "ok"
        else:
            out["error"] = "No supported bank rows recognized in NBT commercial-bank HTML."
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


def merge_commercial_cache(current: dict[str, Any], cached: dict[str, Any] | None) -> tuple[dict[str, Any], bool]:
    current_banks = dict(current.get("commercial_banks") or {})
    cached_banks = dict((cached or {}).get("commercial_banks") or {})
    merged = dict(cached_banks)
    merged.update(current_banks)
    used_cache = set(cached_banks) - set(current_banks)
    for bank_code in current_banks:
        merged[bank_code] = dict(cached_banks.get(bank_code, {}), **current_banks[bank_code])
    current["commercial_banks"] = merged
    current["commercial_banks_stale"] = sorted(used_cache)
    current["commercial_banks_source"] = "NBT current publication + last valid NBT publication per missing bank"
    return current, bool(used_cache)


def nbt_rates() -> dict[str, Any]:
    """NBT API/HTML for official FX; NBT commercial page for bank card-buy; cache temporary gaps."""
    errors: list[str] = []
    fx: dict[str, Any] | None = None
    try:
        r = requests.get(NBT_API, timeout=15, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        r.raise_for_status()
        out = parse_nbt_api(r.json())
        if valid_nbt(out):
            fx = out
        else:
            errors.append(out.get("error") or f"NBT API status: {out['status']}")
    except Exception as exc:
        errors.append(f"API {type(exc).__name__}: {exc}")

    if fx is None:
        try:
            r = requests.get(NBT_HTML, timeout=20, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            out = parse_nbt_html(r.text)
            if valid_nbt(out):
                fx = out
            else:
                errors.append(out.get("error") or f"NBT HTML status: {out['status']}")
        except Exception as exc:
            errors.append(f"HTML {type(exc).__name__}: {exc}")

    cached = None
    if fx is None:
        cached = load_cached_nbt()
        if cached:
            fx = cached
        else:
            fx = {"source": NBT_API, "source_type": "official_api_primary", "status": "error", "currencies": list(NBT_CODES.values()), "rates": {}, "error": "; ".join(errors)}
    elif valid_nbt(fx):
        cache_nbt(fx)

    try:
        r = requests.get(NBT_COMMERCIAL_HTML, timeout=20, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        commercial = parse_nbt_commercial_html(r.text)
        if commercial.get("status") != "ok":
            errors.append(commercial.get("error", "NBT commercial table unavailable"))
            commercial = {"commercial_banks": {}}
    except Exception as exc:
        errors.append(f"Commercial HTML {type(exc).__name__}: {exc}")
        commercial = {"commercial_banks": {}}

    if cached is None:
        cached = load_cached_nbt()
    commercial, commercial_stale = merge_commercial_cache(commercial, cached)
    fx["commercial_banks"] = commercial.get("commercial_banks", {})
    fx["commercial_banks_stale"] = commercial.get("commercial_banks_stale", [])
    fx["commercial_banks_source"] = commercial.get("commercial_banks_source")
    if errors:
        fx["collection_warnings"] = errors
    return fx


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


def alif_api() -> dict[str, Any]:
    """Keep Alif only as the RUB fallback source for T-Bank/Sber calculations."""
    out = {"source": ALIF_API, "status": "error", "rates": {}}
    try:
        r = requests.get(ALIF_API, timeout=15, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        r.raise_for_status()
        data = r.json()
        out["status"] = "ok"
        out["rates"] = find_alif_quotes(data)
        if not out["rates"]:
            out["note"] = "API responded, but no recognized buy/sell fields were found."
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


async def main() -> None:
    with open(RESULTS, encoding="utf-8") as f:
        payload = json.load(f)
    payload["reference_rates"] = {
        "collected_at": now_iso(),
        "currencies": list(NBT_CODES.values()),
        "nbt": nbt_rates(),
        "alif_api": alif_api(),
        "bank_usd_eur_policy": "NBT commercial-bank Credit Cards Buy is the sole source for USD/EUR bank rates; last valid NBT publication is used for temporary gaps.",
        "note": "Separate bank USD/EUR scrapers are disabled. NBT official FX API/HTML and NBT commercial-bank table are the only USD/EUR sources.",
    }
    with open(RESULTS, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    nbt = payload["reference_rates"]["nbt"]
    print(json.dumps({"nbt_status": nbt["status"], "nbt_currencies_found": sorted((nbt.get("rates") or {}).keys()), "nbt_source_type": nbt.get("source_type"), "nbt_stale": nbt.get("stale", False), "commercial_banks": len(nbt.get("commercial_banks") or {}), "commercial_banks_stale": nbt.get("commercial_banks_stale", []), "alif_api_status": payload["reference_rates"]["alif_api"]["status"], "bank_usd_eur_source": "NBT only"}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
