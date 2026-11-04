# PAGE: eyewear_nightly failed for 2026-03-07

**Incident:** INC-043 · **Pipeline:** eyewear_nightly (DuckDB, SQL models) · **Night:** 2026-03-07 · **Raised:** 2026-03-08 02:15 UTC

The nightly run for **2026-03-07** finished with status **failed**.

## What fired

- model error: models/staging/stg_order_items.sql: Conversion Error: Could not convert string "$237.00" to DECIMAL(10,2) when casting from source column unit_price
- model error: models/marts/customer_ltv.sql: Catalog Error: Table with name stg_order_items does not exist!
- model error: models/marts/daily_revenue.sql: Catalog Error: Table with name stg_order_items does not exist!
- model error: models/marts/inventory_levels.sql: Catalog Error: Table with name stg_order_items does not exist!
- contract `orders_volume` (volume): 290 rows vs trailing median 104 (ratio 2.79)
- contract `order_items_volume` (volume): could not check: Catalog Error: Table with name stg_order_items does not exist!
- contract `items_unit_price_nulls` (nulls): could not check: Catalog Error: Table with name stg_order_items does not exist!
- contract `orders_unique` (unique): 145 duplicated key value(s) of (order_id)
- contract `order_items_unique` (unique): could not check: Catalog Error: Table with name stg_order_items does not exist!
- contract `inventory_levels_unique` (unique): could not check: Catalog Error: Table with name inventory_levels does not exist!
- contract `customer_ltv_unique` (unique): could not check: Catalog Error: Table with name customer_ltv does not exist!
- contract `revenue_non_negative` (invariant): could not check: Catalog Error: Table with name daily_revenue does not exist!
- contract `revenue_reconciles` (invariant): could not check: Catalog Error: Table with name daily_revenue does not exist!
- test `assert_inventory_complete`: could not run: Catalog Error: Table with name inventory_levels does not exist!
- test `assert_ltv_non_negative`: could not run: Catalog Error: Table with name customer_ltv does not exist!
- test `assert_no_future_dates`: could not run: Catalog Error: Table with name daily_revenue does not exist!
- test `assert_target_night_has_revenue`: could not run: Catalog Error: Table with name daily_revenue does not exist!

## Source warnings

- contract `order_items_price_numeric` (types, warning): 496 value(s) of unit_price are not DECIMAL(10,2)

## Your job

Find the root cause, fix it in your sandbox, replay the night to prove the fix, and submit an RCA. The full run log is in `logs/`.

Downstream consumers: finance (`daily_revenue`), merchandising (`inventory_levels`), operations (`late_shipments`), CRM (`customer_ltv`).
