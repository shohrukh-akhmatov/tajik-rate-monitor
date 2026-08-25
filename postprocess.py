from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright

SITE = Path("site")
RESULTS = SITE / "results.json"
TZ = ZoneInfo("Asia/Dushanbe")
AMONAT_URL = "https://www.amonatbonk.tj/en/#3"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 TajikRateMonitor/1.0"
)


def decimals(text: str) -> list[float]:
    out: list[float] = []
    for token in re.findall(r"(?<!\d)(0[.,]\d{3,6})(?!\d)", text):
        value = float(token.replace(",", "."))
        if 0.05 <= value <= 0.20:
            out.append(value)
    return out


def rate_record(label: str, buy: float, sell: float, raw: str) -> dict:
    return {
        "label": label,
        "buy": buy,
        "sell": sell,
        "buy_per_1000": round(buy * 1000, 4),
        "sell_per_1000": round(sell * 1000, 4),
        "selector_found": True,
        "raw": raw[:240],
    }


async def click_label(page, label: str) -> bool:
    candidates = page.get_by_text(label, exact=True)
    for i in range(await candidates.count()):
        item = candidates.nth(i)
        try:
            if await item.is_visible():
                await item.click(timeout=3500)
                await page.wait_for_timeout(1000)
                return True
        except Exception:
            continue
    return False


async def visible_rub_row(page) -> tuple[list[float], str] | None:
    rows = page.locator("tr:visible")
    matches: list[tuple[list[float], str]] = []
    for i in range(await rows.count()):
        row = rows.nth(i)
        try:
            text = " ".join((await row.inner_text(timeout=1500)).split())
        except Exception:
            continue
        if "RUB" not in text.upper() and "РУБ" not in text.upper():
            continue
        vals = decimals(text)
        if len(vals) >= 2:
            matches.append((vals, text))
    return matches[-1] if matches else None


async def collect_amonat() -> dict[str, dict]:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        context = await browser.new_context(
            user_agent=USER_AGENT,
            locale="en-US",
            timezone_id="Asia/Dushanbe",
        )
        page = await context.new_page()
        page.set_default_timeout(8000)
        try:
            response = await page.goto(AMONAT_URL, wait_until="domcontentloaded", timeout=35000)
            if response and response.status >= 400:
                raise RuntimeError(f"HTTP {response.status}")
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except PlaywrightTimeoutError:
                pass
            await page.wait_for_timeout(2500)

            result: dict[str, dict] = {}

            # Amonat opens on the Individual table by default. Capture it first as the cash/retail rate.
            row = await visible_rub_row(page)
            if row:
                vals, raw = row
                buy, sell = vals[-2], vals[-1]
                result["cash"] = rate_record("Cash", buy, sell, raw)

            # If the Individual tab is clickable, re-select it and prefer the refreshed value.
            if await click_label(page, "Individual"):
                row = await visible_rub_row(page)
                if row:
                    vals, raw = row
                    buy, sell = vals[-2], vals[-1]
                    result["cash"] = rate_record("Cash", buy, sell, raw)

            # The incoming-transfer rate we need is explicitly called "Remittances" by Amonatbank.
            if await click_label(page, "Remittances"):
                row = await visible_rub_row(page)
                if row:
                    vals, raw = row
                    buy, sell = vals[-2], vals[-1]
                    result["transfer"] = rate_record("Transfers", buy, sell, raw)

            return result
        finally:
            await context.close()
            await browser.close()


def normalize_existing(payload: dict) -> None:
    for bank in payload.get("banks", []):
        rates = bank.setdefault("rates", {})
        # Eskhata and ActivBank call their ordinary counter rate "Private individuals".
        if bank.get("id") in {"eskhata", "activbank"} and "retail" in rates and "cash" not in rates:
            rates["cash"] = dict(rates["retail"])
            rates["cash"]["label"] = "Cash"


async def main() -> None:
    payload = json.loads(RESULTS.read_text(encoding="utf-8"))
    normalize_existing(payload)

    amonat_rates = await collect_amonat()
    for bank in payload.get("banks", []):
        if bank.get("id") != "amonat":
            continue
        bank["source"] = AMONAT_URL
        bank["note"] = "Amonatbank publishes Individual, Legal entity and Remittances. Remittances is the Somoni transfer-rate source."
        bank["primary_category"] = "transfer"
        if amonat_rates:
            bank["rates"].update(amonat_rates)
            bank["status"] = "ok" if "transfer" in amonat_rates else "partial"
            bank["error"] = None if "transfer" in amonat_rates else "Amonat Remittances RUB row not extracted"
            bank["last_success_at"] = datetime.now(TZ).isoformat(timespec="seconds")
        break

    RESULTS.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"amonat": {k: v["buy_per_1000"] for k, v in amonat_rates.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
