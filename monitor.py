from __future__ import annotations

import asyncio
import json
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from playwright.async_api import Browser, Page, TimeoutError as PlaywrightTimeoutError, async_playwright

TZ = ZoneInfo("Asia/Dushanbe")
SITE = Path("site")
SITE.mkdir(exist_ok=True)
PUBLIC_BASE = "https://shohrukh-akhmatov.github.io/tajik-rate-monitor"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 TajikRateMonitor/1.1"
)


@dataclass
class BankConfig:
    id: str
    name: str
    url: str
    categories: list[tuple[str, str]] = field(default_factory=list)
    primary: str | None = None
    suitability: str = "experimental"
    note: str = ""
    default_only: bool = False
    pair_order: str = "buy_sell"  # Most banks publish Покупка → Продажа.
    special: str | None = None


BANKS = [
    BankConfig(
        "oriyon", "Oriyonbank", "https://oriyonbonk.tj/ru",
        [("transfer", "Переводы"), ("cash", "В кассе"), ("noncash", "Безналичными"), ("card", "Картой"), ("nbt", "НБТ")],
        "transfer", "direct_candidate",
        "Website exposes five rate classes. Use Transfers only after matching it to Oriyon 24.",
    ),
    BankConfig(
        "dc", "Dushanbe City", "https://dc.tj/",
        [("transfer", "Переводы"), ("cash", "В кассе"), ("legal", "Юр. лица"), ("nbt", "НБТ")],
        "transfer", "direct_candidate",
        "Transfer table is explicitly published on the official site.",
    ),
    BankConfig(
        "eskhata", "Eskhata", "https://www.eskhata.com/",
        [("retail", "Частным лицам"), ("legal", "Юридическим лицам"), ("transfer", "Денежные переводы")],
        "transfer", "direct_candidate",
        "Official page says rates apply to bank offices and the mobile app.",
    ),
    BankConfig(
        "humo", "Humo", "https://humo.tj/ru",
        [("transfer", "Переводы")],
        "transfer", "direct_candidate",
        "Humo publishes the columns as Продажа → Покупка, so the collector reverses them into normalized Buy/Sell fields.",
        pair_order="sell_buy",
    ),
    BankConfig(
        "activbank", "ActivBank", "https://activbank.tj/exchange-rates",
        [("retail", "Частным лицам"), ("legal", "Юридическим лицам"), ("card", "По карточкам"), ("transfer", "Денежные переводы"), ("nbt", "НБТ")],
        "transfer", "verify_app",
        "Website is machine-readable, but observed app/transfer behavior can differ from the published table.",
    ),
    BankConfig(
        "amonat", "Amonatbank", "https://www.amonatbonk.tj/ru/",
        [("retail", "Физическое лицо"), ("legal", "Юридическое лицо"), ("transfer", "Денежные переводы")],
        "transfer", "experimental",
        "Rate categories exist, but current numbers are loaded in a way the generic browser collector may not expose.",
    ),
    BankConfig(
        "spitamen", "Spitamen Bank", "https://spitamenbank.tj/ru/personal/",
        [], "generic", "wrong_rate_class",
        "This is the bank's ordinary website Buy/Sell table, not verified as an incoming transfer/card rate.",
        True, special="spitamen",
    ),
    BankConfig(
        "vasl", "Vasl Bank", "https://vasl.tj/",
        [], "generic", "experimental",
        "JavaScript application; the collector records a RUB quote only if it can identify one safely.",
        True,
    ),
    BankConfig(
        "alif", "Alif Bank", "https://alif.tj/",
        [], None, "unsupported",
        "A public RUB quote may exist, but it is not verified as the incoming transfer/card rate required by Somoni.",
        True,
    ),
    BankConfig(
        "ibt", "International Bank of Tajikistan", "https://ibt.tj/?lang=ru",
        [], None, "unsupported",
        "The public page can expose an accounting/NBT RUB value even when the relevant transfer Buy/Sell rate is absent.",
        True,
    ),
]

CATEGORY_LABELS = {
    "transfer": "Transfers", "card": "Card", "cash": "Cash", "noncash": "Non-cash",
    "retail": "Retail", "legal": "Legal entities", "nbt": "NBT", "generic": "Generic website",
}
MONTHS_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def extract_source_timestamp(text: str) -> str | None:
    clean = normalize_space(text)

    # 25.08.2026, 14:06:47  /  25.08.2026 14:06
    m = re.search(r"(\d{1,2}\.\d{1,2}\.\d{4})\s*,?\s*(\d{1,2}:\d{2}(?::\d{2})?)", clean)
    if m:
        for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
            try:
                return datetime.strptime(f"{m.group(1)} {m.group(2)}", fmt).replace(tzinfo=TZ).isoformat(timespec="seconds")
            except ValueError:
                pass

    # 21:04, 25 августа 2026 (Oriyonbank)
    m = re.search(
        r"(\d{1,2}:\d{2}(?::\d{2})?)\s*,?\s*(\d{1,2})\s+"
        r"(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+(\d{4})",
        clean, flags=re.I,
    )
    if m:
        hh, mm, *rest = [int(x) for x in m.group(1).split(":")]
        ss = rest[0] if rest else 0
        return datetime(int(m.group(4)), MONTHS_RU[m.group(3).lower()], int(m.group(2)), hh, mm, ss, tzinfo=TZ).isoformat(timespec="seconds")

    # Date only.
    m = re.search(r"(?:актуал\w*|курс(?:\s+актуален)?\s+на|курс\s+на)\D{0,15}(\d{1,2}\.\d{1,2}\.\d{4})", clean, flags=re.I)
    if m:
        try:
            return datetime.strptime(m.group(1), "%d.%m.%Y").replace(tzinfo=TZ).isoformat(timespec="seconds")
        except ValueError:
            pass
    return None


def valid_rub_rate(value: float) -> bool:
    return math.isfinite(value) and 0.05 <= value <= 0.20


def parse_decimal_tokens(text: str) -> list[float]:
    values: list[float] = []
    for token in re.findall(r"(?<!\d)(0[.,]\d{3,6})(?!\d)", text):
        try:
            value = float(token.replace(",", "."))
        except ValueError:
            continue
        if valid_rub_rate(value) and value not in values:
            values.append(value)
    return values


def normalize_pair(values: list[float], raw: str, pair_order: str) -> dict[str, Any] | None:
    if not values:
        return None
    if len(values) == 1:
        return {"buy": values[0], "sell": None, "raw": raw[:240]}
    first, second = values[0], values[1]
    if pair_order == "sell_buy":
        return {"buy": second, "sell": first, "raw": raw[:240]}
    return {"buy": first, "sell": second, "raw": raw[:240]}


def extract_spitamen_pair(text: str) -> dict[str, Any] | None:
    """Spitamen shows NBT first, then a separate Покупка/Продажа table. Target only the latter."""
    clean = normalize_space(text)
    # Anchor to the explicit Buy/Sell headings to avoid accidentally reading the NBT RUB value.
    m = re.search(
        r"Покупка\s+Продажа.*?RUB\s+(0[.,]\d{3,6})\s+(0[.,]\d{3,6})",
        clean, flags=re.I | re.S,
    )
    if not m:
        return None
    buy = float(m.group(1).replace(",", "."))
    sell = float(m.group(2).replace(",", "."))
    if not (valid_rub_rate(buy) and valid_rub_rate(sell)):
        return None
    return {"buy": buy, "sell": sell, "raw": f"RUB {m.group(1)} {m.group(2)}"}


def extract_rub_pair(text: str, pair_order: str = "buy_sell", special: str | None = None) -> dict[str, Any] | None:
    if special == "spitamen":
        return extract_spitamen_pair(text)

    lines = [normalize_space(line) for line in text.splitlines() if normalize_space(line)]
    candidates: list[tuple[list[float], str]] = []
    for idx, line in enumerate(lines):
        upper = line.upper()
        if "RUB" not in upper and "РУБЛ" not in upper:
            continue
        segment_lines = [line]
        for next_line in lines[idx + 1 : idx + 8]:
            if re.search(r"\b(?:USD|EUR|CNY|KZT|AED|TRY)\b", next_line.upper()):
                break
            segment_lines.append(next_line)
        segment = " ".join(segment_lines)
        values = parse_decimal_tokens(segment)
        if values:
            candidates.append((values, segment))

    # Prefer an actual two-column quote over a one-value NBT/accounting row.
    for values, segment in candidates:
        if len(values) >= 2:
            return normalize_pair(values, segment, pair_order)
    for values, segment in candidates:
        if len(values) == 1:
            return normalize_pair(values, segment, pair_order)
    return None


async def visible_text(page: Page) -> str:
    return await page.locator("body").inner_text(timeout=12_000)


async def try_select_native(page: Page, label: str) -> bool:
    selects = page.locator("select:visible")
    for i in range(await selects.count()):
        select = selects.nth(i)
        options = await select.locator("option").all_inner_texts()
        match = next((o for o in options if normalize_space(o).lower() == label.lower()), None)
        if match is None:
            match = next((o for o in options if label.lower() in normalize_space(o).lower()), None)
        if match:
            await select.select_option(label=match)
            await page.wait_for_timeout(900)
            return True
    return False


async def try_click_category(page: Page, label: str) -> bool:
    for role in ("button", "tab", "option"):
        try:
            locator = page.get_by_role(role, name=label, exact=True)
            for i in range(await locator.count()):
                item = locator.nth(i)
                if await item.is_visible():
                    await item.click(timeout=4_000)
                    await page.wait_for_timeout(900)
                    return True
        except Exception:
            pass

    if await try_select_native(page, label):
        return True

    try:
        combos = page.locator('[role="combobox"]:visible')
        for i in range(await combos.count()):
            await combos.nth(i).click(timeout=3_000)
            await page.wait_for_timeout(250)
            option = page.get_by_text(label, exact=True)
            for j in range(await option.count()):
                candidate = option.nth(j)
                if await candidate.is_visible():
                    await candidate.click(timeout=3_000)
                    await page.wait_for_timeout(900)
                    return True
    except Exception:
        pass
    return False


async def extract_visible_rub(page: Page, bank: BankConfig) -> tuple[dict[str, Any] | None, str | None]:
    text = await visible_text(page)
    return extract_rub_pair(text, bank.pair_order, bank.special), extract_source_timestamp(text)


def rate_record(pair: dict[str, Any], key: str, label: str, selector_found: bool) -> dict[str, Any]:
    return {
        "label": CATEGORY_LABELS.get(key, label),
        "buy": pair["buy"],
        "sell": pair["sell"],
        "buy_per_1000": round(pair["buy"] * 1000, 4),
        "sell_per_1000": round(pair["sell"] * 1000, 4) if pair["sell"] is not None else None,
        "selector_found": selector_found,
        "raw": pair["raw"],
    }


async def collect_bank(browser: Browser, bank: BankConfig) -> dict[str, Any]:
    fetched_at = now_iso()
    context = await browser.new_context(user_agent=USER_AGENT, locale="ru-RU", timezone_id="Asia/Dushanbe")
    page = await context.new_page()
    page.set_default_timeout(7_000)
    result: dict[str, Any] = {
        "id": bank.id, "name": bank.name, "source": bank.url, "suitability": bank.suitability,
        "note": bank.note, "primary_category": bank.primary, "rates": {}, "status": "error",
        "error": None, "source_updated_at": None, "fetched_at": fetched_at,
    }
    errors: list[str] = []
    timestamps: list[str] = []

    try:
        response = await page.goto(bank.url, wait_until="domcontentloaded", timeout=35_000)
        if response and response.status >= 400:
            raise RuntimeError(f"HTTP {response.status}")
        try:
            await page.wait_for_load_state("networkidle", timeout=8_000)
        except PlaywrightTimeoutError:
            pass
        await page.wait_for_timeout(800)

        if bank.categories:
            for key, label in bank.categories:
                selected = await try_click_category(page, label)
                pair, stamp = await extract_visible_rub(page, bank)
                if stamp:
                    timestamps.append(stamp)
                if pair:
                    result["rates"][key] = rate_record(pair, key, label, selected)
                else:
                    errors.append(f"{label}: RUB row not found")
        elif bank.default_only:
            pair, stamp = await extract_visible_rub(page, bank)
            if stamp:
                timestamps.append(stamp)
            if pair:
                result["rates"]["generic"] = rate_record(pair, "generic", CATEGORY_LABELS["generic"], True)
            else:
                errors.append("No unambiguous RUB buy/sell row found")

        if result["rates"]:
            result["status"] = "ok" if not errors else "partial"
        else:
            result["status"] = "no_rate"
        if errors:
            result["error"] = "; ".join(errors)
        result["source_updated_at"] = timestamps[0] if timestamps else None
    except Exception as exc:
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        await context.close()
    return result


def get_public_json(name: str, attempts: int = 3) -> Any:
    url = f"{PUBLIC_BASE}/{name}?t={int(datetime.now().timestamp())}"
    for attempt in range(attempts):
        try:
            r = requests.get(
                url, timeout=8,
                headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"},
            )
            if r.ok:
                return r.json()
        except Exception:
            pass
        if attempt < attempts - 1:
            time.sleep(min(2 ** attempt, 4))
    return None


def signature(bank: dict[str, Any]) -> str:
    compact = {
        key: {"buy": value.get("buy"), "sell": value.get("sell")}
        for key, value in sorted(bank.get("rates", {}).items())
    }
    return json.dumps(compact, sort_keys=True, ensure_ascii=False)


def merge_last_good(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    if current.get("rates"):
        current["last_success_at"] = current["fetched_at"]
        return current
    if previous and previous.get("rates"):
        # Retain the last good values, but stamp them STALE so downstream stages
        # (calculate_rates.is_valid_direct_transfer) never treat a retained value
        # as a fresh website observation. Without this stamp the daily full scan
        # published a failed scrape's last-good rate as if it were current.
        current["rates"] = {
            key: {**record, "stale": True, "stale_from": previous.get("last_success_at") or previous.get("fetched_at")}
            for key, record in previous.get("rates", {}).items()
        }
        current["last_success_at"] = previous.get("last_success_at") or previous.get("fetched_at")
        current["status"] = "stale"
        current["error"] = current.get("error") or "Collector failed; showing last known successful values"
    return current


def update_history(banks: list[dict[str, Any]], previous_results: dict[str, Any] | None) -> list[dict[str, Any]]:
    old_history = get_public_json("history.json")
    history = old_history if isinstance(old_history, list) else []
    previous_map = {b["id"]: b for b in (previous_results or {}).get("banks", [])}
    for bank in banks:
        if not bank.get("rates"):
            continue
        old = previous_map.get(bank["id"])
        if old and signature(old) == signature(bank):
            continue
        history.append({
            "at": bank.get("source_updated_at") or bank.get("fetched_at"),
            "collected_at": bank.get("fetched_at"), "bank": bank["id"], "name": bank["name"],
            "rates": {k: {"buy": v.get("buy"), "sell": v.get("sell")} for k, v in bank.get("rates", {}).items()},
        })
    return history[-5000:]


async def main() -> None:
    previous_results = get_public_json("results.json")
    previous_map = {b["id"]: b for b in (previous_results or {}).get("banks", [])}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        try:
            banks = []
            for config in BANKS:
                current = await collect_bank(browser, config)
                banks.append(merge_last_good(current, previous_map.get(config.id)))
        finally:
            await browser.close()

    generated_at = now_iso()
    payload = {
        "schema_version": 1, "generated_at": generated_at, "timezone": "Asia/Dushanbe",
        "unit": "TJS per 1 RUB; *_per_1000 fields are TJS per 1,000 RUB",
        "disclaimer": "Raw public bank website data for monitoring. A published website rate is not automatically the effective Russia→Tajikistan transfer rate.",
        "banks": banks,
    }
    history = update_history(banks, previous_results)
    (SITE / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (SITE / "history.json").write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"generated_at": generated_at, "banks": {b["id"]: b["status"] for b in banks}}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
