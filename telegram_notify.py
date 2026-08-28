from __future__ import annotations

import html
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

TZ = ZoneInfo("Asia/Dushanbe")
RESULTS = Path("site/results.json")
PUBLIC_RESULTS = "https://shohrukh-akhmatov.github.io/tajik-rate-monitor/results.json"
DASHBOARD = "https://shohrukh-akhmatov.github.io/tajik-rate-monitor/"
CATEGORIES = (("transfer", "Transfer"), ("cash", "Cash"))
FIELDS = (("buy_per_1000", "Buy"), ("sell_per_1000", "Sell"))
CARD_CURRENCIES = ("USD", "EUR")


def fetch_previous() -> dict | None:
    try:
        response = requests.get(
            PUBLIC_RESULTS,
            params={"t": int(datetime.now().timestamp())},
            timeout=12,
            headers={"Cache-Control": "no-cache", "User-Agent": "TajikRateMonitor/1.1"},
        )
        if response.ok:
            return response.json()
    except Exception as exc:
        print(f"Could not fetch previous deployed results: {exc}")
    return None


def bank_map(payload: dict | None) -> dict[str, dict]:
    if not payload:
        return {}
    return {bank.get("id"): bank for bank in payload.get("banks", []) if bank.get("id")}


def changed(old: float | None, new: float | None, threshold: float = 0.005) -> bool:
    if old is None or new is None:
        return False
    return abs(float(old) - float(new)) >= threshold


def fmt(value: float | None) -> str:
    return "—" if value is None else f"{float(value):.2f}"


def fmt_fx(value: float | None) -> str:
    return "—" if value is None else f"{float(value):.4f}"


def bold_if_transfer(text: str, category: str) -> str:
    return f"<b>{text}</b>" if category == "Transfer" else text


def collect_changes(previous: dict, current: dict) -> list[dict]:
    old_banks = bank_map(previous)
    changes: list[dict] = []

    for bank in current.get("banks", []):
        old_bank = old_banks.get(bank.get("id"))
        if not old_bank:
            continue

        rub_changes: list[dict] = []
        for category_key, category_label in CATEGORIES:
            old_rate = (old_bank.get("rates") or {}).get(category_key) or {}
            new_rate = (bank.get("rates") or {}).get(category_key) or {}
            field_changes = []

            for field_key, field_label in FIELDS:
                old_value = old_rate.get(field_key)
                new_value = new_rate.get(field_key)
                if changed(old_value, new_value):
                    field_changes.append({"field": field_label, "old": old_value, "new": new_value})

            if field_changes:
                rub_changes.append(
                    {
                        "category": category_label,
                        "changes": field_changes,
                        "current_buy": new_rate.get("buy_per_1000"),
                        "current_sell": new_rate.get("sell_per_1000"),
                    }
                )

        card_changes: list[dict] = []
        old_card = old_bank.get("card_buy") or {}
        new_card = bank.get("card_buy") or {}
        for currency in CARD_CURRENCIES:
            old_value = (old_card.get(currency) or {}).get("buy")
            new_value = (new_card.get(currency) or {}).get("buy")
            if changed(old_value, new_value, threshold=0.00005):
                card_changes.append(
                    {"currency": currency, "old": old_value, "new": new_value}
                )

        if rub_changes or card_changes:
            changes.append(
                {
                    "name": bank.get("name", bank.get("id", "Bank")),
                    "rub_changes": rub_changes,
                    "card_changes": card_changes,
                }
            )

    return changes


def parsed_time(generated_at: str | None) -> datetime:
    if generated_at:
        try:
            return datetime.fromisoformat(generated_at).astimezone(TZ)
        except ValueError:
            pass
    return datetime.now(TZ)


def build_change_message(changes: list[dict], generated_at: str | None) -> str:
    dt = parsed_time(generated_at)
    lines = ["🔔 Tajik bank rate change", dt.strftime("%d.%m.%Y %H:%M (Dushanbe)"), ""]

    for bank in changes:
        lines.append(f"🏦 {html.escape(str(bank['name']))}")
        for category in bank["rub_changes"]:
            parts = [
                f"{html.escape(str(item['field']))} {fmt(item['old'])} → {fmt(item['new'])}"
                for item in category["changes"]
            ]
            change_line = f"RUB {category['category']}: " + " | ".join(parts)
            current_line = (
                f"Current: Buy {fmt(category['current_buy'])} · Sell {fmt(category['current_sell'])} / 1,000 RUB"
            )
            lines.append(bold_if_transfer(change_line, category["category"]))
            lines.append(bold_if_transfer(current_line, category["category"]))
        for item in bank["card_changes"]:
            lines.append(
                f"{html.escape(str(item['currency']))} Card Buy: {fmt_fx(item['old'])} → {fmt_fx(item['new'])} TJS"
            )
        lines.append("")

    lines.append(f"Monitor: {DASHBOARD}")
    return "\n".join(lines).strip()


def build_test_message(current: dict) -> str:
    dt = parsed_time(current.get("generated_at"))
    lines = ["✅ Tajik Rate Monitor test", dt.strftime("%d.%m.%Y %H:%M (Dushanbe)"), ""]
    for bank in current.get("banks", []):
        rate_lines = []
        for key, label in CATEGORIES:
            rate = (bank.get("rates") or {}).get(key) or {}
            if rate.get("buy_per_1000") is None and rate.get("sell_per_1000") is None:
                continue
            line = (
                f"RUB {label}: Buy {fmt(rate.get('buy_per_1000'))} · Sell {fmt(rate.get('sell_per_1000'))}"
            )
            rate_lines.append(bold_if_transfer(line, label))

        card = bank.get("card_buy") or {}
        card_parts = []
        for currency in CARD_CURRENCIES:
            buy = (card.get(currency) or {}).get("buy")
            if buy is not None:
                card_parts.append(f"{currency} {fmt_fx(buy)}")
        if card_parts:
            rate_lines.append("Card Buy: " + " · ".join(card_parts))

        if rate_lines:
            lines.append(f"🏦 {html.escape(str(bank.get('name', bank.get('id', 'Bank'))))}")
            lines.extend(rate_lines)
            lines.append("")
    lines.append("RUB values: TJS per 1,000 RUB")
    lines.append("USD/EUR Card Buy values: TJS per 1 unit")
    lines.append(f"Monitor: {DASHBOARD}")
    return "\n".join(lines).strip()


def send_telegram(text: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("Telegram notification skipped: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not configured.")
        return

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Telegram sendMessage failed: HTTP {response.status_code} {response.text[:300]}")
    print("Telegram notification sent.")


def main() -> None:
    if not RESULTS.exists():
        raise SystemExit("site/results.json does not exist")

    current = json.loads(RESULTS.read_text(encoding="utf-8"))
    test_mode = os.getenv("TELEGRAM_TEST", "").strip().lower() in {"1", "true", "yes", "on"}
    if test_mode:
        message = build_test_message(current)
        print(message)
        send_telegram(message)
        return

    previous = fetch_previous()
    if not previous:
        print("No previous deployed results available; skipping notification baseline run.")
        return

    changes = collect_changes(previous, current)
    if not changes:
        print("No RUB Cash/Transfer or USD/EUR Card Buy changes detected; no Telegram message sent.")
        return

    message = build_change_message(changes, current.get("generated_at"))
    print(message)
    send_telegram(message)


if __name__ == "__main__":
    main()
