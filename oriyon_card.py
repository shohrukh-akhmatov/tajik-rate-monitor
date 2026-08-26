from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright

RESULTS = Path("site/results.json")
ORIYON_URL = "https://oriyonbonk.tj/ru"
TZ = ZoneInfo("Asia/Dushanbe")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 TajikRateMonitor/1.1"
)
CURRENCIES = ("USD", "EUR")


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def fx_numbers(text: str) -> list[float]:
    values: list[float] = []
    for token in re.findall(r"(?<!\d)(\d{1,2}[.,]\d{2,6})(?!\d)", text):
        try:
            value = float(token.replace(",", "."))
        except ValueError:
            continue
        if 5.0 <= value <= 20.0:
            values.append(value)
    return values


async def select_category(page, label: str) -> bool:
    for role in ("button", "tab", "option"):
        try:
            locator = page.get_by_role(role, name=label, exact=True)
            for i in range(await locator.count()):
                item = locator.nth(i)
                if await item.is_visible():
                    await item.click(timeout=4000)
                    await page.wait_for_timeout(1200)
                    return True
        except Exception:
            pass

    selects = page.locator("select:visible")
    for i in range(await selects.count()):
        select = selects.nth(i)
        try:
            options = await select.locator("option").all_inner_texts()
        except Exception:
            continue
        match = next((o for o in options if clean(o).lower() == label.lower()), None)
        if match is None:
            match = next((o for o in options if label.lower() in clean(o).lower()), None)
        if match:
            try:
                await select.select_option(label=match)
                await page.wait_for_timeout(1200)
                return True
            except Exception:
                pass

    try:
        combos = page.locator('[role="combobox"]:visible')
        for i in range(await combos.count()):
            await combos.nth(i).click(timeout=3000)
            await page.wait_for_timeout(250)
            options = page.get_by_text(label, exact=True)
            for j in range(await options.count()):
                item = options.nth(j)
                if await item.is_visible():
                    await item.click(timeout=3000)
                    await page.wait_for_timeout(1200)
                    return True
    except Exception:
        pass

    return False


async def currency_buy(page, currency: str) -> tuple[float, str] | None:
    rows = page.locator("tr:visible")
    for i in range(await rows.count()):
        row = rows.nth(i)
        try:
            text = clean(await row.inner_text(timeout=1500))
        except Exception:
            continue
        if not re.search(rf"(^|\s)(?:1\s*)?{currency}(\s|$)", text, re.I):
            continue
        values = fx_numbers(text)
        if values:
            return values[0], text

    try:
        body = await page.locator("body").inner_text(timeout=5000)
    except Exception:
        return None
    lines = [clean(line) for line in body.splitlines() if clean(line)]
    for i, line in enumerate(lines):
        if not re.search(rf"(^|\s)(?:1\s*)?{currency}(\s|$)", line, re.I):
            continue
        raw = " ".join(lines[i : min(len(lines), i + 7)])
        values = fx_numbers(raw)
        if values:
            return values[0], raw
    return None


async def collect() -> dict[str, dict]:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        context = await browser.new_context(
            user_agent=USER_AGENT, locale="ru-RU", timezone_id="Asia/Dushanbe"
        )
        try:
            page = await context.new_page()
            page.set_default_timeout(8000)
            response = await page.goto(ORIYON_URL, wait_until="domcontentloaded", timeout=35000)
            if response and response.status >= 400:
                raise RuntimeError(f"HTTP {response.status}")
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except PlaywrightTimeoutError:
                pass
            await page.wait_for_timeout(2000)

            if not await select_category(page, "Картой"):
                return {}

            result: dict[str, dict] = {}
            for currency in CURRENCIES:
                found = await currency_buy(page, currency)
                if not found:
                    continue
                buy, raw = found
                result[currency] = {
                    "currency": currency,
                    "buy": round(buy, 6),
                    "source_category": "Картой",
                    "raw": raw[:240],
                    "fetched_at": datetime.now(TZ).isoformat(timespec="seconds"),
                }
            return result
        finally:
            await context.close()
            await browser.close()


async def main() -> None:
    payload = json.loads(RESULTS.read_text(encoding="utf-8"))
    rates = await collect()
    for bank in payload.get("banks", []):
        if bank.get("id") != "oriyon":
            continue
        if rates:
            bank["card_buy"] = rates
            bank["card_buy_source"] = ORIYON_URL
            bank["card_buy_status"] = "ok" if all(c in rates for c in CURRENCIES) else "partial"
        else:
            bank["card_buy_status"] = "no_rate"
        break
    RESULTS.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("ORIYON_CARD_BUY=" + json.dumps({k: v["buy"] for k, v in rates.items()}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
