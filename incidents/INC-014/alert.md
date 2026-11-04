# PAGE: eyewear_nightly failed for 2026-03-07

**Incident:** INC-014 · **Pipeline:** eyewear_nightly (DuckDB, SQL models) · **Night:** 2026-03-07 · **Raised:** 2026-03-08 02:15 UTC

The nightly run for **2026-03-07** finished with status **failed**.

## What fired

- contract `orders_volume` (volume): 55 rows vs trailing median 104 (ratio 0.53)
- contract `order_items_volume` (volume): 97 rows vs trailing median 174.5 (ratio 0.56)

## Your job

Find the root cause, fix it in your sandbox, replay the night to prove the fix, and submit an RCA. The full run log is in `logs/`.

Downstream consumers: finance (`daily_revenue`), merchandising (`inventory_levels`), operations (`late_shipments`), CRM (`customer_ltv`).
