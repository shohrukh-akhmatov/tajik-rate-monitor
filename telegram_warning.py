from __future__ import annotations

import html
import json
import os
from pathlib import Path

import requests

ANOMALIES = Path("site/anomalies.json")


def send(text: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("Telegram warning skipped: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not configured.")
        return
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=20,
    )
    response.raise_for_status()


def main() -> None:
    data = json.loads(ANOMALIES.read_text(encoding="utf-8"))
    anomalies = data.get("anomalies", [])
    if not anomalies:
        print("No anomalies; no warning sent.")
        return

    lines = ["🚨 <b>RATE WARNING — MANUAL CHECK REQUIRED</b>", str(data.get("generated_at", "")), ""]
    for item in anomalies:
        service = html.escape(str(item.get("service_slug", "")))
        bank = html.escape(str(item.get("bank_code", "")))
        code = html.escape(str(item.get("code", "")))
        message = html.escape(str(item.get("message", "")))
        lines.append(f"🏦 <b>{service} → {bank}</b>")
        lines.append(f"{code}: {message}")
        lines.append("")
    lines.append("⛔ Calculated data is staged only. Do not publish until checked in the admin panel.")
    send("\n".join(lines).strip())
    print("Telegram anomaly warning sent.")


if __name__ == "__main__":
    main()
