from __future__ import annotations

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


def fetch_previous() -> dict | None:
    try:
        response = requests.get(
            PUBLIC_RESULTS,
            params={"t": int(datetime.now().timestamp())},
            timeout=12,
            headers={"Cache-Control": "no-cache", "User-Agent": "TajikRateMonitor/1.0"},
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


def changed(old: float | None, new: float | None) -> bool:
    if old is None or new is None:
        return False
    return abs(float(old) - float(new)) >= 0.005


def fmt(value: float | None) -> str:
    return "—" if value is None else f"{float(value):.2f}"


def collect_changes(previous: dict, current: dict) -> list[dict]:
    old_banks = bank_map(previous)
    changes: list[dict] = []

    for bank in current.get("banks", []):
        old_bank = old_banks.get(bank.get("id"))
        if not old_bank:
            continue

        bank_changes: list[dict] = []
        for category_key, category_label in CATEGORIES:
            old_rate = (old_bank.get("rates") or {}).get(category_key) or {}
            new_rate = (bank.get("rates") or {}).get(category_key) or {}
            field_changes = []

            for field_key, field_label in FIELDS:
                old_value = old_rate.get(field_key)
                new_value = new_rate.get(field_key)
                if changed(old_value, new_value):
                    field_changes.append(
                        {
                            "field": field_label,
                            "old": old_value,
                            "new": new_value,
                        }
                    )

            if field_changes:
                bank_changes.append(
                    {
                        "category": category_label,
                        "changes": field_changes,
                        "current_buy": new_rate.get("buy_per_1000"),
                        "current_sell": new_rate.get("sell_per_1000"),
                    }
                )

        if bank_changes:
            changes.append({"name": bank.get("name", bank.get("id", "Bank")), "changes": bank_changes})

    return changes


def build_message(changes: list[dict], generated_at: str | None) -> str:
    if generated_at:
        try:
            dt = datetime.fromisoformat(generated_at).astimezone(TZ)
        except ValueError:
            dt = datetime.now(TZ)
    else:
        dt = datetime.now(TZ)

    lines = [
        "🔔 RUB→TJS rate change",
        dt.strftime("%d.%m.%Y %H:%M (Dushanbe)"),
        "",
    ]

    for bank in changes:
        lines.append(f"🏦 {bank['name']}")
        for category in bank["changes"]:
            parts = [
                f"{item['field']} {fmt(item['old'])} → {fmt(item['new'])}"
                for item in category["changes"]
            ]
            lines.append(f"{category['category']}: " + " | ".join(parts))
            lines.append(
                f"Current: Buy {fmt(category['current_buy'])} · Sell {fmt(category['current_sell'])} / 1,000 RUB"
            )
        lines.append("")

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
    previous = fetch_previous()
    if not previous:
        print("No previous deployed results available; skipping notification baseline run.")
        return

    changes = collect_changes(previous, current)
    if not changes:
        print("No Cash/Transfer rate changes detected; no Telegram message sent.")
        return

    message = build_message(changes, current.get("generated_at"))
    print(message)
    send_telegram(message)


if __name__ == "__main__":
    main()
