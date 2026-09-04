# Rate Sync Performance & Database Optimization

This document records the backend and database changes made to optimize rate updates and prevent client performance degradation.

---

## Changes in `publish_rate_calculation_run`

### Problem
The calculation runner was publishing ~46 rate rows every 30 minutes. The original function unconditionally:
1. Marked existing `exchange_rates` records as `is_current = false`.
2. Inserted brand new rows into `exchange_rates`, firing database triggers (`moneytj_prepare_rate_trigger`, `moneytj_audit_rate_trigger`), incrementing `moneytj_revision_seq`, and generating audit logs.
3. Updated `national_bank_rates` with incremented revisions.
4. Rewrote `tajikistan_card_rates` in `app_configuration` with incremented revisions.
5. Fired ~46 Supabase Realtime CDC events every 30 minutes, even when not a single rate value had changed.

This inflated database size, generated thousands of historical rows, and queued up hundreds of Realtime events for mobile clients returning from background states.

### Solution & Value-Change Detection
The `publish_rate_calculation_run` function was updated with change detection logic:
1. **RUB Exchange Rates:** Checks whether `is_current = true` record already exists with matching `effective_rate` and `verification_status`. If unchanged, the row insert and old row inactivation are skipped.
2. **National Bank Rates:** Checks whether `buy_rate` matches `r.final_rate`. If unchanged, the update is skipped.
3. **Card Rates:** Inspects each entry in `tajikistan_card_rates` JSONB array; only writes to `app_configuration` if a value actually changed.

### Impact
- When rate values do not change:
  - **0 rows inserted** into `exchange_rates`
  - **0 revisions incremented** on `moneytj_revision_seq`
  - **0 Realtime broadcasts** emitted to connected clients
  - **0 audit logs** generated
- Paired with the mobile app's revision cursor early-exit, mobile sync completes in < 200 ms.
