# PAGE: eyewear_nightly failed for 2026-03-07

**Incident:** INC-019 · **Pipeline:** eyewear_nightly (DuckDB, SQL models) · **Night:** 2026-03-07 · **Raised:** 2026-03-08 02:15 UTC

The nightly run for **2026-03-07** finished with status **failed**.

## What fired

- contract `order_items_volume` (volume): 351 rows vs trailing median 195.5 (ratio 1.80)
- contract `order_items_unique` (unique): 117 duplicated key value(s) of (order_item_id)

## Your job

Find the root cause, fix it in your sandbox, replay the night to prove the fix, and submit an RCA. The full run log is in `logs/`.

Downstream consumers: finance (`daily_revenue`), merchandising (`inventory_levels`), operations (`late_shipments`), CRM (`customer_ltv`).
