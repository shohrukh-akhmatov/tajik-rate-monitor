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
ORIYON_URL = "https://oriyonbonk.tj/ru"
ACTIV_URL = "https://activbank.tj/exchange-rates"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 TajikRateMonitor/1.1"
)
FX_CURRENCIES = ("USD", "EUR")


def decimals(text: str) -> list[float]:
    out: list[float] = []
    for token in re.findall(r"(?<!\d)(0[.,]\d{3,6})(?!\d)", text):
        value = float(token.replace(",", "."))
        if 0.05 <= value <= 0.20:
            out.append(value)
    return out


def fx_decimals(text: str) -> list[float]:
    out: list[float] = []
    for token in re.findall(r"(?<!\d)(\d{1,2}[.,]\d{2,6})(?!\d)", text):
        try:
            value = float(token.replace(",", "."))
        except ValueError:
            continue
        if 5.0 <= value <= 20.0:
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


def card_buy_record(currency: str, buy: float, raw: str, source_category: str) -> dict:
    return {
        "currency": currency,
        "buy": round(buy, 6),
        "source_category": source_category,
        "raw": raw[:240],
        "fetched_at": datetime.now(TZ).isoformat(timespec="seconds"),
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

    # Some bank widgets use native selects instead of buttons/tabs.
    selects = page.locator("select:visible")
    for i in range(await selects.count()):
        select = selects.nth(i)
        try:
            options = await select.locator("option").all_inner_texts()
        except Exception:
            continue
        match = next(
            (option for option in options if " ".join(option.split()).lower() == label.lower()),
            None,
        )
        if match is None:
            match = next(
                (option for option in options if label.lower() in " ".join(option.split()).lower()),
                None,
            )
        if match:
            try:
                await select.select_option(label=match)
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


async def visible_currency_buy(page, currency: str) -> tuple[float, str] | None:
    """Read the first Buy value from the currently selected visible FX category."""
    rows = page.locator("tr:visible")
    for i in range(await rows.count()):
        row = rows.nth(i)
        try:
            text = " ".join((await row.inner_text(timeout=1500)).split())
        except Exception:
            continue
        if not re.search(rf"(^|\s)(?:1\s*)?{re.escape(currency)}(\s|$)", text, re.I):
            continue
        vals = fx_decimals(text)
        if vals:
            return vals[0], text

    # Responsive widgets often use divs instead of table rows.
    try:
        body_text = await page.locator("body").inner_text(timeout=5000)
    except Exception:
        return None
    lines = [" ".join(line.split()) for line in body_text.splitlines() if line.strip()]
    for i, line in enumerate(lines):
        if not re.search(rf"(^|\s)(?:1\s*)?{re.escape(currency)}(\s|$)", line, re.I):
            continue
        window = lines[i : min(len(lines), i + 7)]
        raw = " ".join(window)
        vals = fx_decimals(raw)
        if vals:
            return vals[0], raw
    return None


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
        try:
            page = await open_page(context, AMONAT_URL)
        except Exception as exc:
            print(f"Amonat page load failed: {exc}")
            return {}

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
    except Exception as exc:
        print(f"Amonat collection failed: {exc}")
        return {}
    finally:
        await context.close()


async def collect_alif(browser) -> tuple[dict[str, dict], dict[str, dict]]:
    """Collect the same official JSON data used by Alif's public exchange widget."""
    context = await browser.new_context(
        user_agent=USER_AGENT,
        locale="en-US",
        timezone_id="Asia/Dushanbe",
    )
    try:
        result: dict[str, dict] = {}
        card_buy: dict[str, dict] = {}

        try:
            response = await context.request.get(ALIF_API, timeout=15000)
            if response.ok:
                payload = await response.json()
                local_rates = payload.get("localRates", [])
                rub = next(
                    (
                        item
                        for item in local_rates
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

                for currency in FX_CURRENCIES:
                    item = next(
                        (r for r in local_rates if str(r.get("name", "")).upper() == currency),
                        None,
                    )
                    if not item:
                        continue
                    buy = numeric(item.get("visaBuyValue"))
                    if buy is None:
                        continue
                    raw = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
                    card_buy[currency] = card_buy_record(
                        currency, buy, raw, "Cards / Visa"
                    )
        except Exception as exc:
            print(f"Alif API collection failed; trying rendered page fallback: {exc}")

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

        return result, card_buy
    finally:
        await context.close()


async def collect_card_buy_from_page(
    browser,
    url: str,
    category_label: str,
    source_category: str,
) -> dict[str, dict]:
    context = await browser.new_context(
        user_agent=USER_AGENT,
        locale="ru-RU",
        timezone_id="Asia/Dushanbe",
    )
    try:
        try:
            page = await open_page(context, url)
        except Exception as exc:
            print(f"Card buy page load failed for {url} ({category_label}): {exc}")
            return {}

        try:
            if not await click_label(page, category_label):
                print(f"Card category not selected at {url}: {category_label}")
                return {}

            result: dict[str, dict] = {}
            for currency in FX_CURRENCIES:
                found = await visible_currency_buy(page, currency)
                if not found:
                    continue
                buy, raw = found
                result[currency] = card_buy_record(currency, buy, raw, source_category)
            return result
        except Exception as exc:
            print(f"Card buy extraction failed for {url}: {exc}")
            return {}
    finally:
        await context.close()


def normalize_existing(payload: dict) -> None:
    for bank in payload.get("banks", []):
        rates = bank.setdefault("rates", {})
        bank_id = bank.get("id")

        if bank_id in {"eskhata", "activbank"} and "retail" in rates and "cash" not in rates:
            rates["cash"] = dict(rates["retail"])
            rates["cash"]["label"] = "Cash"
            del rates["retail"]

        if bank_id == "vasl" and "generic" in rates:
            rates["cash"] = dict(rates["generic"])
            rates["cash"]["label"] = "Cash"
            del rates["generic"]
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


def apply_card_buy(payload: dict, bank_id: str, card_buy: dict[str, dict], source: str) -> None:
    for bank in payload.get("banks", []):
        if bank.get("id") != bank_id:
            continue
        bank["card_buy_source"] = source
        bank["card_buy"] = card_buy
        bank["card_buy_status"] = "ok" if all(c in card_buy for c in FX_CURRENCIES) else (
            "partial" if card_buy else "no_rate"
        )
        return


async def main() -> None:
    payload = json.loads(RESULTS.read_text(encoding="utf-8"))
    normalize_existing(payload)

    amonat_rates = {}
    alif_rates, alif_card_buy = {}, {}
    oriyon_card_buy = {}
    activ_card_buy = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        try:
            try:
                amonat_rates = await collect_amonat(browser)
            except Exception as exc:
                print(f"Amonat collection error: {exc}")

            try:
                alif_rates, alif_card_buy = await collect_alif(browser)
            except Exception as exc:
                print(f"Alif collection error: {exc}")

            try:
                oriyon_card_buy = await collect_card_buy_from_page(
                    browser, ORIYON_URL, "Картой", "Картой"
                )
            except Exception as exc:
                print(f"Oriyon card collection error: {exc}")

            try:
                activ_card_buy = await collect_card_buy_from_page(
                    browser, ACTIV_URL, "По карточкам", "По карточкам"
                )
            except Exception as exc:
                print(f"ActivBank card collection error: {exc}")
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

    # Additive monitoring only: these fields do not change the existing Somoni app contract.
    apply_card_buy(payload, "alif", alif_card_buy, ALIF_API)
    apply_card_buy(payload, "oriyon", oriyon_card_buy, ORIYON_URL)
    apply_card_buy(payload, "activbank", activ_card_buy, ACTIV_URL)

    RESULTS.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "amonat": {k: v["buy_per_1000"] for k, v in amonat_rates.items()},
                "alif_rub": {k: v["buy_per_1000"] for k, v in alif_rates.items()},
                "card_buy": {
                    "alif": {k: v["buy"] for k, v in alif_card_buy.items()},
                    "oriyon": {k: v["buy"] for k, v in oriyon_card_buy.items()},
                    "activbank": {k: v["buy"] for k, v in activ_card_buy.items()},
                },
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
