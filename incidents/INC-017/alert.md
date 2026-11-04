# PAGE: eyewear_nightly failed for 2026-03-07

**Incident:** INC-017 · **Pipeline:** eyewear_nightly (DuckDB, SQL models) · **Night:** 2026-03-07 · **Raised:** 2026-03-08 02:15 UTC

The nightly run for **2026-03-07** finished with status **failed**.

## What fired

- contract `orders_volume` (volume): 294 rows vs trailing median 110 (ratio 2.67)
- contract `order_items_volume` (volume): 494 rows vs trailing median 182 (ratio 2.71)
- contract `orders_unique` (unique): 147 duplicated key value(s) of (order_id)
- contract `order_items_unique` (unique): 247 duplicated key value(s) of (order_item_id)

## Your job

Find the root cause, fix it in your sandbox, replay the night to prove the fix, and submit an RCA. The full run log is in `logs/`.

Downstream consumers: finance (`daily_revenue`), merchandising (`inventory_levels`), operations (`late_shipments`), CRM (`customer_ltv`).
