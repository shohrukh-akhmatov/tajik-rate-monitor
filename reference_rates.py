from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests
from playwright.async_api import async_playwright

from monitor import BANKS, USER_AGENT, try_click_category, visible_text

TZ = ZoneInfo("Asia/Dushanbe")
RESULTS = "site/results.json"
NBT_API = "https://nbt.tj/en/kurs/rate_export.php"
NBT_HTML = "https://nbt.tj/tj/kurs/kurs.php"
ALIF_API = "https://alif.tj/api/rates"
CURRENCIES = {"USD": (5.0, 20.0), "EUR": (7.0, 20.0), "RUB": (0.05, 0.20), "CNY": (0.5, 3.0), "KZT": (0.01, 0.20)}
NBT_CODES = {"840": "USD", "978": "EUR", "810": "RUB", "156": "CNY", "398": "KZT"}


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def number_tokens(text: str, currency: str) -> list[float]:
    lo, hi = CURRENCIES[currency]; values: list[float] = []
    for token in re.findall(r"(?<!\d)(\d{1,2}[.,]\d{3,6})(?!\d)", text):
        try: value = float(token.replace(",", "."))
        except ValueError: continue
        if lo <= value <= hi and value not in values: values.append(value)
    return values


def pair_from_text(text: str, currency: str, pair_order: str = "buy_sell") -> dict[str, Any] | None:
    lines = [re.sub(r"\s+", " ", x).strip() for x in text.splitlines() if x.strip()]
    for i, line in enumerate(lines):
        if currency not in line.upper(): continue
        raw = " ".join(lines[i:i + 5]); values = number_tokens(raw, currency)
        if len(values) >= 2:
            a, b = values[:2]
            if pair_order == "sell_buy": a, b = b, a
            return {"buy": a, "sell": b, "raw": raw[:300]}
        if len(values) == 1: return {"buy": values[0], "sell": None, "raw": raw[:300]}
    return None


def parse_nbt_html(html: str) -> dict[str, Any]:
    from html import unescape
    out: dict[str, Any] = {"source": NBT_HTML, "status": "error", "updated_at": None, "currencies": list(NBT_CODES.values()), "rates": {}}
    try:
        text = unescape(re.sub(r"<[^>]+>", " ", html)); text = re.sub(r"\s+", " ", text)
        for code, currency in NBT_CODES.items():
            m = re.search(rf"\b{code}\b\s+(\d+)\s+([0-9]+[.,][0-9]+)", text)
            if m:
                nominal = int(m.group(1)); rate = float(m.group(2).replace(",", "."))
                out["rates"][currency] = {"rate": rate, "nominal": nominal, "per_unit": rate / nominal}
        if len(out["rates"]) == len(NBT_CODES): out["status"] = "ok"
        elif out["rates"]:
            out["status"] = "partial"; out["missing"] = [c for c in NBT_CODES.values() if c not in out["rates"]]
        else: out["error"] = "No NBT currency rows recognized in HTML."
    except Exception as exc: out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def nbt_rates() -> dict[str, Any]:
    try:
        r = requests.get(NBT_HTML, timeout=20, headers={"User-Agent": USER_AGENT}); r.raise_for_status()
        out = parse_nbt_html(r.text)
        try:
            api = requests.get(NBT_API, timeout=8, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            out["api_status"] = "ok" if api.ok else f"http_{api.status_code}"
        except Exception as exc: out["api_status"] = f"error:{type(exc).__name__}"
        return out
    except Exception as exc:
        return {"source": NBT_HTML, "status": "error", "currencies": list(NBT_CODES.values()), "rates": {}, "error": f"{type(exc).__name__}: {exc}"}


def find_alif_quotes(value: Any, found: dict[str, dict[str, float | None]] | None = None):
    if found is None: found = {}
    if isinstance(value, dict):
        code = next((str(value.get(k)).upper() for k in ("currency", "currencyCode", "code", "ccy", "symbol", "ticker") if isinstance(value.get(k), str) and str(value.get(k)).upper() in CURRENCIES), None)
        if code:
            buy = sell = None
            for key, raw in value.items():
                if not isinstance(raw, (int, float, str)): continue
                try: num = float(str(raw).replace(",", "."))
                except ValueError: continue
                if not CURRENCIES[code][0] <= num <= CURRENCIES[code][1]: continue
                k = str(key).lower()
                if any(x in k for x in ("buy", "purchase", "bid", "pokup")): buy = num
                elif any(x in k for x in ("sell", "sale", "ask", "prodaj")): sell = num
            if buy is not None or sell is not None: found[code] = {"buy": buy, "sell": sell}
        for child in value.values(): find_alif_quotes(child, found)
    elif isinstance(value, list):
        for child in value: find_alif_quotes(child, found)
    return found


def alif_api() -> dict[str, Any]:
    out = {"source": ALIF_API, "status": "error", "rates": {}}
    try:
        r = requests.get(ALIF_API, timeout=15, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}); r.raise_for_status()
        data = r.json(); out["status"] = "ok"; out["rates"] = find_alif_quotes(data); out["raw_shape"] = type(data).__name__
        if not out["rates"]: out["note"] = "API responded, but no recognized buy/sell fields were found."
    except Exception as exc: out["error"] = f"{type(exc).__name__}: {exc}"
    return out


async def collect_bank_fx() -> dict[str, Any]:
    result = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        try:
            for bank in BANKS:
                if not bank.categories: continue
                context = await browser.new_context(user_agent=USER_AGENT, locale="ru-RU", timezone_id="Asia/Dushanbe")
                page = await context.new_page(); page.set_default_timeout(7000); bank_out = {}
                try:
                    response = await page.goto(bank.url, wait_until="domcontentloaded", timeout=35000)
                    if response and response.status >= 400: raise RuntimeError(f"HTTP {response.status}")
                    try: await page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception: pass
                    await page.wait_for_timeout(700)
                    # USD/EUR: Cards first. Cash is the only fallback. Never use Transfers.
                    eligible = [(key, label) for key, label in bank.categories if key in ("card", "cash")]
                    ordered = sorted(eligible, key=lambda item: 0 if item[0] == "card" else 1)
                    for key, label in ordered:
                        selected = await try_click_category(page, label); text = await visible_text(page); quotes = {}
                        for currency in ("USD", "EUR"):
                            pair = pair_from_text(text, currency, bank.pair_order)
                            if pair: quotes[currency] = {**pair, "selector_found": selected}
                        if quotes:
                            bank_out[key] = quotes
                            if key == "card": break
                    if not bank_out:
                        bank_out["_note"] = "No usable USD/EUR quote found in Card or Cash categories; Transfers are intentionally excluded."
                except Exception as exc: bank_out["_error"] = f"{type(exc).__name__}: {exc}"
                finally: await context.close()
                if bank_out: result[bank.id] = {"name": bank.name, "rates": bank_out}
        finally: await browser.close()
    return result


async def main() -> None:
    with open(RESULTS, encoding="utf-8") as f: payload = json.load(f)
    payload["reference_rates"] = {
        "collected_at": now_iso(), "currencies": list(NBT_CODES.values()), "nbt": nbt_rates(), "alif_api": alif_api(),
        "banks": await collect_bank_fx(), "bank_usd_eur_policy": "Card first; Cash fallback; never Transfers.",
        "note": "NBT is an official reference rate. USD/EUR bank quotes are monitoring data only until verified for the intended product.",
    }
    with open(RESULTS, "w", encoding="utf-8") as f: json.dump(payload, f, ensure_ascii=False, indent=2)
    print(json.dumps({"nbt_status": payload["reference_rates"]["nbt"]["status"], "nbt_currencies_found": sorted(payload["reference_rates"]["nbt"]["rates"]), "alif_api_status": payload["reference_rates"]["alif_api"]["status"], "bank_sources": len(payload["reference_rates"]["banks"])}, ensure_ascii=False))


if __name__ == "__main__": asyncio.run(main())
