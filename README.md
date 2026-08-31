# Tajik Rate Monitor

Developer dashboard for monitoring **RUB → TJS** rates published on official Tajik bank websites.

## What it does

- Runs automatically every **10 minutes** with GitHub Actions.
- Uses a real headless browser so JavaScript-driven bank rate widgets can be read.
- Keeps rate classes separate: **Transfers, Card, Cash, Non-cash, Retail, Legal entities, NBT**.
- Never silently substitutes one rate class for another.
- Publishes normalized `results.json` plus a change-only rolling `history.json` to GitHub Pages.
- Retains the last good rate if a bank website temporarily fails, but clearly marks it **STALE**.
- Uses canonical bank IDs from `config/banks.json`, so display-name changes cannot move a coefficient to another bank.
- Uses version-controlled coefficients from `config/rate_rules.json`.
- Uses **Alif as the explicit fallback base source only for IBT, Spitamen and Vasl** when their own base rate is unavailable.
- Calculates ready-to-use **T-Bank and Sberbank** rates without putting the calculation logic in the mobile app.
- Collects official **NBT RUB/USD/EUR** reference rates and checks them for missing/outlier/jump conditions before they are eligible for publication.
- Collects available **USD/EUR bank card-buy** observations for staging.
- Sends a separate **Telegram warning** when an anomaly requires manual review. In that case normal rate-change notifications are suppressed and calculated data stays staged.
- Sends calculated/staged data to Supabase when the GitHub Actions secrets are configured.

## Calculation pipeline

```text
Official bank/NBT sources
        ↓
GitHub Actions collectors
        ↓
Canonical bank IDs + versioned coefficients
        ↓
Deterministic calculation
        ↓
Anomaly checks
        ↓
Telegram warning if abnormal
        ↓
Supabase rate_calculation_staging
        ↓
Admin correction if required
        ↓
Admin publish RPC
        ↓
Existing production tables
        ↓
Somoni
```

The mobile app continues to read the existing production tables. The calculation/staging layer does not replace the existing public data model.

## Supabase integration

The pipeline writes calculation runs to `rate_calculation_runs` and ready-to-use rows to `rate_calculation_staging`.

Required GitHub Actions secrets:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

The service-role key is used only by GitHub Actions and must never be put in the GitHub Pages frontend.

After an anomalous run, an administrator can correct the staged `final_rate` and mark the row as a manual override in the admin panel. The Supabase `publish_rate_calculation_run(uuid)` function refuses to publish unresolved anomalies.

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
