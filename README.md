# Tajik Rate Monitor

Developer dashboard for monitoring **RUB → TJS** rates published on official Tajik bank websites.

## What it does

- Runs automatically every **10 minutes** with GitHub Actions.
- Uses a real headless browser so JavaScript-driven bank rate widgets can be read.
- Keeps rate classes separate: **Transfers, Card, Cash, Non-cash, Retail, Legal entities, NBT**.
- Never silently substitutes one rate class for another.
- Publishes normalized `results.json` plus a change-only rolling `history.json` to GitHub Pages.
- Retains the last good rate if a bank website temporarily fails, but clearly marks it **STALE**.
- Dashboard lets you enter the rate seen in the bank's own app. That value stays only in your browser (`localStorage`).
- Dashboard compares website vs app and calculates an optional **Sber −1.5%** estimate separately from raw bank data.

## Banks monitored

Oriyonbank, Dushanbe City, Eskhata, Humo, ActivBank, Amonatbank, Spitamen Bank, Vasl Bank, Alif Bank and International Bank of Tajikistan.

A bank appearing in the dashboard does **not** mean its website rate is suitable for Somoni. `suitability` explicitly distinguishes direct candidates, app-verification cases, experimental sources, wrong rate classes and unsupported sites.

## GitHub Pages

The workflow deploys the `site/` directory using GitHub's official Pages actions. If the first workflow says Pages is not enabled, make the one-time change:

**Repository → Settings → Pages → Build and deployment → Source → GitHub Actions**

Then run **Actions → Update Tajik bank rates → Run workflow** once.

Expected dashboard URL:

`https://shohrukh-akhmatov.github.io/tajik-rate-monitor/`

Raw JSON:

`https://shohrukh-akhmatov.github.io/tajik-rate-monitor/results.json`

## Data rule

Values in `results.json` are raw public bank website observations. They are research/monitoring inputs and are not automatically the actual Russia → Tajikistan transfer rate. Production use should happen only after the corresponding website category is verified against the bank app / real transfer result.
