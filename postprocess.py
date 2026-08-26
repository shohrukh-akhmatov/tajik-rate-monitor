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
ALIF_URL = "https://alif.tj/en"
ALIF_API = "https://alif.tj/api/rates"
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


def numeric(value) -> float | None:
    try:
        result = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return result if 0.01 <= result <= 20 else None


async def click_label(page, label: str) -> bool:
    pattern = re.compile(rf"^\s*{re.escape(label)}\s*$", re.IGNORECASE)
    candidates = page.get_by_text(pattern)
    for i in range(await candidates.count()):
        item = candidates.nth(i)
        try:
            if await item.is_visible():
                await item.click(timeout=3500)
                await page.wait_for_timeout(1400)
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


async def visible_rub_block(page) -> tuple[list[float], str] | None:
    try:
        body_text = await page.locator("body").inner_text(timeout=5000)
    except Exception:
        return None

    lines = [" ".join(line.split()) for line in body_text.splitlines() if line.strip()]
    matches: list[tuple[list[float], str]] = []
    for i, line in enumerate(lines):
        if not re.search(r"(^|\s)(RUB|РУБ)(\s|$)", line, re.IGNORECASE):
            continue
        window = lines[max(0, i - 2) : min(len(lines), i + 9)]
        raw = " ".join(window)
        vals = decimals(raw)
        if len(vals) >= 2:
            matches.append((vals[:2], raw))

    return matches[0] if matches else None


async def visible_rub_rate(page) -> tuple[list[float], str] | None:
    row = await visible_rub_row(page)
    if row:
        return row
    return await visible_rub_block(page)


async def open_page(context, url: str):
    page = await context.new_page()
    page.set_default_timeout(8000)
    response = await page.goto(url, wait_until="domcontentloaded", timeout=35000)
    if response and response.status >= 400:
        raise RuntimeError(f"HTTP {response.status}")
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeoutError:
        pass
    await page.wait_for_timeout(2500)
    return page


async def collect_amonat(browser) -> dict[str, dict]:
    context = await browser.new_context(
        user_agent=USER_AGENT,
        locale="en-US",
        timezone_id="Asia/Dushanbe",
    )
    try:
        page = await open_page(context, AMONAT_URL)
        result: dict[str, dict] = {}

        row = await visible_rub_row(page)
        if row:
            vals, raw = row
            result["cash"] = rate_record("Cash", vals[-2], vals[-1], raw)

        if await click_label(page, "Individual"):
            row = await visible_rub_row(page)
            if row:
                vals, raw = row
                result["cash"] = rate_record("Cash", vals[-2], vals[-1], raw)

        if await click_label(page, "Remittances"):
            row = await visible_rub_row(page)
            if row:
                vals, raw = row
                result["transfer"] = rate_record("Transfers", vals[-2], vals[-1], raw)

        return result
    finally:
        await context.close()


async def collect_alif(browser) -> dict[str, dict]:
    """Collect the same official JSON data used by Alif's public exchange widget."""
    context = await browser.new_context(
        user_agent=USER_AGENT,
        locale="en-US",
        timezone_id="Asia/Dushanbe",
    )
    try:
        result: dict[str, dict] = {}

        try:
            response = await context.request.get(ALIF_API, timeout=15000)
            if response.ok:
                payload = await response.json()
                rub = next(
                    (
                        item
                        for item in payload.get("localRates", [])
                        if str(item.get("name", "")).upper() == "RUB"
                        or str(item.get("currencyCode", "")) == "810"
                    ),
                    None,
                )
                if rub:
                    raw = json.dumps(rub, ensure_ascii=False, separators=(",", ":"))
                    transfer_buy = numeric(rub.get("moneyTransferBuyValue"))
                    transfer_sell = numeric(rub.get("moneyTransferTradeValue"))
                    cash_buy = numeric(rub.get("buyValue"))
                    cash_sell = numeric(rub.get("sellValue"))

                    if transfer_buy is not None and transfer_sell is not None:
                        result["transfer"] = rate_record(
                            "Transfers", transfer_buy, transfer_sell, raw
                        )
                    if cash_buy is not None and cash_sell is not None:
                        result["cash"] = rate_record("Cash", cash_buy, cash_sell, raw)
        except Exception as exc:
            print(f"Alif API collection failed; trying rendered page fallback: {exc}")

        # Defensive fallback in case Alif changes or temporarily blocks the JSON endpoint.
        if "transfer" not in result or "cash" not in result:
            try:
                page = await open_page(context, ALIF_URL)
                if "transfer" not in result and await click_label(page, "Transfers"):
                    row = await visible_rub_rate(page)
                    if row:
                        vals, raw = row
                        result["transfer"] = rate_record(
                            "Transfers", vals[-2], vals[-1], raw
                        )
                if "cash" not in result and await click_label(page, "Cash desks"):
                    row = await visible_rub_rate(page)
                    if row:
                        vals, raw = row
                        result["cash"] = rate_record("Cash", vals[-2], vals[-1], raw)
            except Exception as exc:
                print(f"Alif rendered-page fallback failed: {exc}")

        return result
    finally:
        await context.close()


def normalize_existing(payload: dict) -> None:
    for bank in payload.get("banks", []):
        rates = bank.setdefault("rates", {})
        bank_id = bank.get("id")

        if bank_id in {"eskhata", "activbank"} and "retail" in rates and "cash" not in rates:
            rates["cash"] = dict(rates["retail"])
            rates["cash"]["label"] = "Cash"

        if bank_id == "vasl" and "generic" in rates:
            rates["cash"] = dict(rates["generic"])
            rates["cash"]["label"] = "Cash"
            bank["primary_category"] = None
            bank["note"] = (
                "Vasl publishes one general Exchange Rates table. It is shown as Cash/standard rate; "
                "no separate transfer rate has been verified."
            )


def apply_special_rates(payload: dict, bank_id: str, source: str, rates: dict[str, dict], note: str) -> None:
    for bank in payload.get("banks", []):
        if bank.get("id") != bank_id:
            continue
        bank["source"] = source
        bank["note"] = note
        if rates:
            bank["rates"].update(rates)
            bank["status"] = "ok" if "transfer" in rates else "partial"
            bank["error"] = None
            bank["last_success_at"] = datetime.now(TZ).isoformat(timespec="seconds")
        return


async def main() -> None:
    payload = json.loads(RESULTS.read_text(encoding="utf-8"))
    normalize_existing(payload)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        try:
            amonat_rates = await collect_amonat(browser)
            alif_rates = await collect_alif(browser)
        finally:
            await browser.close()

    apply_special_rates(
        payload,
        "amonat",
        AMONAT_URL,
        amonat_rates,
        "Amonatbank publishes Individual, Legal entity and Remittances. Remittances is the Somoni transfer-rate source.",
    )
    for bank in payload.get("banks", []):
        if bank.get("id") == "amonat":
            bank["primary_category"] = "transfer"
            if amonat_rates and "transfer" not in amonat_rates:
                bank["status"] = "partial"
                bank["error"] = "Amonat Remittances RUB row not extracted"
            break

    apply_special_rates(
        payload,
        "alif",
        ALIF_API,
        alif_rates,
        "Alif's public website loads rates from its official /api/rates endpoint. moneyTransferBuyValue/moneyTransferTradeValue are used for Transfers; buyValue/sellValue for Cash desks.",
    )
    for bank in payload.get("banks", []):
        if bank.get("id") == "alif":
            bank["primary_category"] = "transfer"
            bank["suitability"] = "direct_candidate"
            if "transfer" not in alif_rates:
                bank["status"] = "partial"
                bank["error"] = "Alif Transfers RUB rate not extracted"
            break

    RESULTS.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "amonat": {k: v["buy_per_1000"] for k, v in amonat_rates.items()},
                "alif": {k: v["buy_per_1000"] for k, v in alif_rates.items()},
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
