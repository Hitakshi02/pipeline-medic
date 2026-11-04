# PAGE: eyewear_nightly failed for 2026-03-07

**Incident:** INC-044 · **Pipeline:** eyewear_nightly (DuckDB, SQL models) · **Night:** 2026-03-07 · **Raised:** 2026-03-08 02:15 UTC

The nightly run for **2026-03-07** finished with status **failed**.

## What fired

- contract `orders_order_ts_nulls` (nulls): null rate 1.000 (128/128), max 0.0
- contract `orders_fresh` (freshness): 128/128 rows of the 2026-03-07 batch are dated another day
- test `assert_target_night_has_revenue`: 1 failing row(s)

## Source warnings

- contract `orders_schema` (schema, warning): missing columns ['order_ts'], unexpected columns ['ordered_at']

## Your job

Find the root cause, fix it in your sandbox, replay the night to prove the fix, and submit an RCA. The full run log is in `logs/`.

Downstream consumers: finance (`daily_revenue`), merchandising (`inventory_levels`), operations (`late_shipments`), CRM (`customer_ltv`).
