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
ALIF_API = "https://alif.tj/api/rates"

CURRENCIES = {
    "USD": (5.0, 20.0),
    "EUR": (7.0, 20.0),
    "RUB": (0.05, 0.20),
}


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def number_tokens(text: str, currency: str) -> list[float]:
    lo, hi = CURRENCIES[currency]
    values: list[float] = []
    for token in re.findall(r"(?<!\d)(\d{1,2}[.,]\d{3,6})(?!\d)", text):
        try:
            value = float(token.replace(",", "."))
        except ValueError:
            continue
        if lo <= value <= hi and value not in values:
            values.append(value)
    return values


def pair_from_text(text: str, currency: str, pair_order: str = "buy_sell") -> dict[str, Any] | None:
    lines = [re.sub(r"\s+", " ", x).strip() for x in text.splitlines() if x.strip()]
    candidates: list[tuple[list[float], str]] = []
    for i, line in enumerate(lines):
        if currency not in line.upper():
            continue
        segment = " ".join(lines[i:i + 5])
        values = number_tokens(segment, currency)
        if values:
            candidates.append((values, segment))
    for values, raw in candidates:
        if len(values) >= 2:
            a, b = values[0], values[1]
            if pair_order == "sell_buy":
                a, b = b, a
            return {"buy": a, "sell": b, "raw": raw[:300]}
    for values, raw in candidates:
        if len(values) == 1:
            return {"buy": values[0], "sell": None, "raw": raw[:300]}
    return None


def nbt_rates() -> dict[str, Any]:
    out: dict[str, Any] = {"source": NBT_API, "status": "error", "updated_at": None, "rates": {}}
    try:
        r = requests.get(NBT_API, timeout=15, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        r.raise_for_status()
        data = r.json()
        out["status"] = "ok"
        out["updated_at"] = data.get("period_info", {}).get("date")
        for row in data.get("data", []):
            code = str(row.get("Code", ""))
            currency = {"840": "USD", "978": "EUR", "810": "RUB"}.get(code)
            if currency:
                out["rates"][currency] = {
                    "rate": float(str(row.get("Rate", "0")).replace(",", ".")),
                    "nominal": row.get("Nominal"),
                    "date": row.get("Date"),
                }
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def find_alif_quotes(value: Any, found: dict[str, dict[str, float | None]] | None = None) -> dict[str, dict[str, float | None]]:
    if found is None:
        found = {}
    if isinstance(value, dict):
        code = None
        for key in ("currency", "currencyCode", "code", "ccy", "symbol", "ticker"):
            raw = value.get(key)
            if isinstance(raw, str) and raw.upper() in CURRENCIES:
                code = raw.upper()
                break
        if code:
            buy = sell = None
            for key, raw in value.items():
                if not isinstance(raw, (int, float, str)):
                    continue
                try:
                    num = float(str(raw).replace(",", "."))
                except ValueError:
                    continue
                if not (CURRENCIES[code][0] <= num <= CURRENCIES[code][1]):
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
    out: dict[str, Any] = {"source": ALIF_API, "status": "error", "rates": {}}
    try:
        r = requests.get(ALIF_API, timeout=15, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        r.raise_for_status()
        data = r.json()
        out["status"] = "ok"
        out["rates"] = find_alif_quotes(data)
        out["raw_shape"] = type(data).__name__
        if not out["rates"]:
            out["note"] = "API responded, but no USD/EUR/RUB buy/sell fields were recognized."
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


async def collect_bank_fx() -> dict[str, Any]:
    result: dict[str, Any] = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        try:
            for bank in BANKS:
                if not bank.categories:
                    continue
                context = await browser.new_context(user_agent=USER_AGENT, locale="ru-RU", timezone_id="Asia/Dushanbe")
                page = await context.new_page()
                page.set_default_timeout(7_000)
                bank_out: dict[str, Any] = {}
                try:
                    response = await page.goto(bank.url, wait_until="domcontentloaded", timeout=35_000)
                    if response and response.status >= 400:
                        raise RuntimeError(f"HTTP {response.status}")
                    try:
                        await page.wait_for_load_state("networkidle", timeout=8_000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(700)
                    for key, label in bank.categories:
                        selected = await try_click_category(page, label)
                        text = await visible_text(page)
                        quotes = {}
                        for currency in ("USD", "EUR"):
                            pair = pair_from_text(text, currency, bank.pair_order)
                            if pair:
                                quotes[currency] = {**pair, "selector_found": selected}
                        if quotes:
                            bank_out[key] = quotes
                except Exception as exc:
                    bank_out["_error"] = f"{type(exc).__name__}: {exc}"
                finally:
                    await context.close()
                if bank_out:
                    result[bank.id] = {"name": bank.name, "rates": bank_out}
        finally:
            await browser.close()
    return result


async def main() -> None:
    with open(RESULTS, encoding="utf-8") as f:
        payload = json.load(f)
    payload["reference_rates"] = {
        "collected_at": now_iso(),
        "currencies": ["USD", "EUR"],
        "nbt": nbt_rates(),
        "alif_api": alif_api(),
        "banks": await collect_bank_fx(),
        "note": "USD/EUR bank quotes are collected for monitoring. They are not automatically treated as Somoni app transfer rates unless the category is verified for that use.",
    }
    with open(RESULTS, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(json.dumps({
        "nbt": payload["reference_rates"]["nbt"]["status"],
        "alif_api": payload["reference_rates"]["alif_api"]["status"],
        "banks": len(payload["reference_rates"]["banks"]),
    }, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
