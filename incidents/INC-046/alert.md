# PAGE: eyewear_nightly failed for 2026-03-07

**Incident:** INC-046 · **Pipeline:** eyewear_nightly (DuckDB, SQL models) · **Night:** 2026-03-07 · **Raised:** 2026-03-08 02:15 UTC

The nightly run for **2026-03-07** finished with status **failed**.

## What fired

- contract `orders_volume` (volume): 292 rows vs trailing median 105 (ratio 2.78)
- contract `orders_fresh` (freshness): 100/292 rows of the 2026-03-07 batch are dated another day
- contract `orders_unique` (unique): 146 duplicated key value(s) of (order_id)
- test `assert_no_future_dates`: 4 failing row(s)

## Your job

Find the root cause, fix it in your sandbox, replay the night to prove the fix, and submit an RCA. The full run log is in `logs/`.

Downstream consumers: finance (`daily_revenue`), merchandising (`inventory_levels`), operations (`late_shipments`), CRM (`customer_ltv`).
