from __future__ import annotations

import asyncio
import json
import math
import re
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
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 TajikRateMonitor/1.0"
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


BANKS = [
    BankConfig(
        "oriyon",
        "Oriyonbank",
        "https://oriyonbonk.tj/ru",
        [("transfer", "Переводы"), ("cash", "В кассе"), ("noncash", "Безналичными"), ("card", "Картой"), ("nbt", "НБТ")],
        "transfer",
        "direct_candidate",
        "Website exposes five rate classes. Use Transfers only after matching it to Oriyon 24.",
    ),
    BankConfig(
        "dc",
        "Dushanbe City",
        "https://dc.tj/",
        [("transfer", "Переводы"), ("cash", "В кассе"), ("legal", "Юр. лица"), ("nbt", "НБТ")],
        "transfer",
        "direct_candidate",
        "Transfer table is explicitly published on the official site.",
    ),
    BankConfig(
        "eskhata",
        "Eskhata",
        "https://www.eskhata.com/",
        [("retail", "Частным лицам"), ("legal", "Юридическим лицам"), ("transfer", "Денежные переводы")],
        "transfer",
        "direct_candidate",
        "Official page says rates apply to bank offices and the mobile app.",
    ),
    BankConfig(
        "humo",
        "Humo",
        "https://humo.tj/ru",
        [("transfer", "Переводы")],
        "transfer",
        "direct_candidate",
        "Official page publishes a transfer RUB buy/sell rate and update timestamp.",
    ),
    BankConfig(
        "activbank",
        "ActivBank",
        "https://activbank.tj/exchange-rates",
        [("retail", "Частным лицам"), ("legal", "Юридическим лицам"), ("card", "По карточкам"), ("transfer", "Денежные переводы"), ("nbt", "НБТ")],
        "transfer",
        "verify_app",
        "Website is machine-readable, but observed app/transfer behavior can differ from the published table.",
    ),
    BankConfig(
        "amonat",
        "Amonatbank",
        "https://www.amonatbonk.tj/ru/",
        [("retail", "Физическое лицо"), ("legal", "Юридическое лицо"), ("transfer", "Денежные переводы")],
        "transfer",
        "experimental",
        "Rates appear to be dynamically loaded; browser collector will try the official page.",
    ),
    BankConfig(
        "spitamen",
        "Spitamen Bank",
        "https://spitamenbank.tj/ru/personal/",
        [],
        "generic",
        "wrong_rate_class",
        "Public RUB table may be ordinary exchange, not incoming transfer/card conversion.",
        True,
    ),
    BankConfig(
        "vasl",
        "Vasl Bank",
        "https://vasl.tj/",
        [],
        "generic",
        "experimental",
        "JavaScript application; collector records a RUB quote only if it can identify one safely.",
        True,
    ),
    BankConfig(
        "alif",
        "Alif Bank",
        "https://alif.tj/",
        [],
        None,
        "unsupported",
        "Public site does not expose a clearly equivalent incoming RUB transfer/card rate.",
        True,
    ),
    BankConfig(
        "ibt",
        "International Bank of Tajikistan",
        "https://ibt.tj/?lang=ru",
        [],
        None,
        "unsupported",
        "Public site may show an accounting/NBT rate while RUB buy/sell transfer values are absent.",
        True,
    ),
]

CATEGORY_LABELS = {
    "transfer": "Transfers",
    "card": "Card",
    "cash": "Cash",
    "noncash": "Non-cash",
    "retail": "Retail",
    "legal": "Legal entities",
    "nbt": "NBT",
    "generic": "Generic website",
}


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def extract_source_timestamp(text: str) -> str | None:
    patterns = [
        r"Актуал(?:ен|ьно|ен на|ьно на)?\s*(?:на)?\s*(\d{1,2}:\d{2}(?::\d{2})?)?\s*,?\s*(\d{1,2}\.\d{1,2}\.\d{4})",
        r"Курс\s+(?:актуален\s+)?на[:\s]+(\d{1,2}:\d{2}(?::\d{2})?)?\s*,?\s*(\d{1,2}\.\d{1,2}\.\d{4})",
        r"Курс\s+на[:\s]+(\d{1,2}\.\d{1,2}\.\d{4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        groups = [g for g in match.groups() if g]
        if len(groups) == 2:
            time_value, date_value = groups
        else:
            time_value, date_value = "", groups[0]
        for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
            raw = f"{date_value} {time_value}".strip()
            try:
                return datetime.strptime(raw, fmt).replace(tzinfo=TZ).isoformat(timespec="seconds")
            except ValueError:
                pass
        return normalize_space(match.group(0))
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


def extract_rub_pair(text: str) -> dict[str, Any] | None:
    """Extract RUB buy/sell from visible text. Refuses ambiguous pages instead of guessing."""
    lines = [normalize_space(line) for line in text.splitlines() if normalize_space(line)]
    candidates: list[tuple[int, list[float], str]] = []
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
            candidates.append((idx, values, segment))

    for _idx, values, segment in candidates:
        if len(values) >= 2:
            return {"buy": values[0], "sell": values[1], "raw": segment[:240]}
    for _idx, values, segment in candidates:
        if len(values) == 1:
            return {"buy": values[0], "sell": None, "raw": segment[:240]}
    return None


async def visible_text(page: Page) -> str:
    return await page.locator("body").inner_text(timeout=12_000)


async def try_select_native(page: Page, label: str) -> bool:
    selects = page.locator("select:visible")
    count = await selects.count()
    for i in range(count):
        select = selects.nth(i)
        options = await select.locator("option").all_inner_texts()
        matching = [opt for opt in options if normalize_space(opt).lower() == label.lower()]
        if not matching:
            matching = [opt for opt in options if label.lower() in normalize_space(opt).lower()]
        if matching:
            await select.select_option(label=matching[0])
            await page.wait_for_timeout(900)
            return True
    return False


async def try_click_category(page: Page, label: str) -> bool:
    for role in ("button", "tab", "option"):
        try:
            locator = page.get_by_role(role, name=label, exact=True)
            if await locator.count():
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
            combo = combos.nth(i)
            await combo.click(timeout=3_000)
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


async def extract_visible_rub(page: Page) -> tuple[dict[str, Any] | None, str, str | None]:
    text = await visible_text(page)
    return extract_rub_pair(text), text, extract_source_timestamp(text)


async def collect_bank(browser: Browser, bank: BankConfig) -> dict[str, Any]:
    fetched_at = now_iso()
    context = await browser.new_context(user_agent=USER_AGENT, locale="ru-RU", timezone_id="Asia/Dushanbe")
    page = await context.new_page()
    page.set_default_timeout(7_000)
    result: dict[str, Any] = {
        "id": bank.id,
        "name": bank.name,
        "source": bank.url,
        "suitability": bank.suitability,
        "note": bank.note,
        "primary_category": bank.primary,
        "rates": {},
        "status": "error",
        "error": None,
        "source_updated_at": None,
        "fetched_at": fetched_at,
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
                pair, _text, stamp = await extract_visible_rub(page)
                if stamp:
                    timestamps.append(stamp)
                if pair:
                    result["rates"][key] = {
                        "label": CATEGORY_LABELS.get(key, label),
                        "buy": pair["buy"],
                        "sell": pair["sell"],
                        "buy_per_1000": round(pair["buy"] * 1000, 4),
                        "sell_per_1000": round(pair["sell"] * 1000, 4) if pair["sell"] is not None else None,
                        "selector_found": selected,
                        "raw": pair["raw"],
                    }
                else:
                    errors.append(f"{label}: RUB row not found")
        elif bank.default_only:
            pair, _text, stamp = await extract_visible_rub(page)
            if stamp:
                timestamps.append(stamp)
            if pair:
                result["rates"]["generic"] = {
                    "label": CATEGORY_LABELS["generic"],
                    "buy": pair["buy"],
                    "sell": pair["sell"],
                    "buy_per_1000": round(pair["buy"] * 1000, 4),
                    "sell_per_1000": round(pair["sell"] * 1000, 4) if pair["sell"] is not None else None,
                    "selector_found": True,
                    "raw": pair["raw"],
                }
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


def get_public_json(name: str) -> Any:
    try:
        response = requests.get(
            f"{PUBLIC_BASE}/{name}?t={int(datetime.now().timestamp())}",
            timeout=8,
            headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"},
        )
        if response.ok:
            return response.json()
    except Exception:
        pass
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
        current["rates"] = previous["rates"]
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
        history.append(
            {
                "at": bank.get("source_updated_at") or bank.get("fetched_at"),
                "collected_at": bank.get("fetched_at"),
                "bank": bank["id"],
                "name": bank["name"],
                "rates": {
                    key: {"buy": value.get("buy"), "sell": value.get("sell")}
                    for key, value in bank.get("rates", {}).items()
                },
            }
        )
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
        "schema_version": 1,
        "generated_at": generated_at,
        "timezone": "Asia/Dushanbe",
        "unit": "TJS per 1 RUB; *_per_1000 fields are TJS per 1,000 RUB",
        "disclaimer": "Raw public bank website data for monitoring. A published website rate is not automatically the effective Russia→Tajikistan transfer rate.",
        "banks": banks,
    }
    history = update_history(banks, previous_results)
    (SITE / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (SITE / "history.json").write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"generated_at": generated_at, "banks": {b['id']: b['status'] for b in banks}}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
