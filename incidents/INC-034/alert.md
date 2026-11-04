# PAGE: eyewear_nightly failed for 2026-03-07

**Incident:** INC-034 · **Pipeline:** eyewear_nightly (DuckDB, SQL models) · **Night:** 2026-03-07 · **Raised:** 2026-03-08 02:15 UTC

The nightly run for **2026-03-07** finished with status **failed**.

## What fired

- model error: customers: no input files for table customers under /Users/hitakshi/Documents/devhack/pipeline-medic/incidents/INC-034/data_v2
- model error: products: no input files for table products under /Users/hitakshi/Documents/devhack/pipeline-medic/incidents/INC-034/data_v2
- model error: orders: no input files for table orders under /Users/hitakshi/Documents/devhack/pipeline-medic/incidents/INC-034/data_v2
- model error: order_items: no input files for table order_items under /Users/hitakshi/Documents/devhack/pipeline-medic/incidents/INC-034/data_v2
- model error: inventory: no input files for table inventory under /Users/hitakshi/Documents/devhack/pipeline-medic/incidents/INC-034/data_v2
- model error: shipments: no input files for table shipments under /Users/hitakshi/Documents/devhack/pipeline-medic/incidents/INC-034/data_v2
- model error: models/staging/stg_customers.sql: Catalog Error: Table with name raw_customers does not exist!
- model error: models/staging/stg_inventory.sql: Catalog Error: Table with name raw_inventory does not exist!
- model error: models/staging/stg_order_items.sql: Catalog Error: Table with name raw_order_items does not exist!
- model error: models/staging/stg_orders.sql: Catalog Error: Table with name raw_orders does not exist!
- model error: models/staging/stg_products.sql: Catalog Error: Table with name raw_products does not exist!
- model error: models/staging/stg_shipments.sql: Catalog Error: Table with name raw_shipments does not exist!
- model error: models/marts/customer_ltv.sql: Catalog Error: Table with name stg_orders does not exist!
- model error: models/marts/daily_revenue.sql: Catalog Error: Table with name stg_orders does not exist!
- model error: models/marts/inventory_levels.sql: Catalog Error: Table with name stg_inventory does not exist!
- model error: models/marts/late_shipments.sql: Catalog Error: Table with name stg_shipments does not exist!
- contract `orders_volume` (volume): could not check: Catalog Error: Table with name stg_orders does not exist!
- contract `order_items_volume` (volume): could not check: Catalog Error: Table with name stg_order_items does not exist!
- contract `orders_customer_id_nulls` (nulls): could not check: Catalog Error: Table with name stg_orders does not exist!
- contract `orders_order_ts_nulls` (nulls): could not check: Catalog Error: Table with name stg_orders does not exist!
- contract `items_unit_price_nulls` (nulls): could not check: Catalog Error: Table with name stg_order_items does not exist!
- contract `shipments_shipped_ts_nulls` (nulls): could not check: Catalog Error: Table with name stg_shipments does not exist!
- contract `orders_fresh` (freshness): could not check: Catalog Error: Table with name stg_orders does not exist!
- contract `orders_unique` (unique): could not check: Catalog Error: Table with name stg_orders does not exist!
- contract `order_items_unique` (unique): could not check: Catalog Error: Table with name stg_order_items does not exist!
- contract `customers_unique` (unique): could not check: Catalog Error: Table with name stg_customers does not exist!
- contract `inventory_levels_unique` (unique): could not check: Catalog Error: Table with name inventory_levels does not exist!
- contract `customer_ltv_unique` (unique): could not check: Catalog Error: Table with name customer_ltv does not exist!
- contract `revenue_non_negative` (invariant): could not check: Catalog Error: Table with name daily_revenue does not exist!
- contract `orders_have_known_customers` (invariant): could not check: Catalog Error: Table with name stg_orders does not exist!
- contract `revenue_reconciles` (invariant): could not check: Catalog Error: Table with name daily_revenue does not exist!
- test `assert_inventory_complete`: could not run: Catalog Error: Table with name stg_products does not exist!
- test `assert_late_shipments_positive`: could not run: Catalog Error: Table with name late_shipments does not exist!
- test `assert_ltv_non_negative`: could not run: Catalog Error: Table with name customer_ltv does not exist!
- test `assert_no_future_dates`: could not run: Catalog Error: Table with name daily_revenue does not exist!
- test `assert_target_night_has_revenue`: could not run: Catalog Error: Table with name daily_revenue does not exist!

## Source warnings

- contract `orders_schema` (schema, warning): no orders batch for 2026-03-07
- contract `order_items_schema` (schema, warning): no order_items batch for 2026-03-07
- contract `shipments_schema` (schema, warning): no shipments batch for 2026-03-07
- contract `order_items_price_numeric` (types, warning): could not check: Catalog Error: Table with name raw_order_items does not exist!
- contract `order_items_quantity_integer` (types, warning): could not check: Catalog Error: Table with name raw_order_items does not exist!

## Your job

Find the root cause, fix it in your sandbox, replay the night to prove the fix, and submit an RCA. The full run log is in `logs/`.

Downstream consumers: finance (`daily_revenue`), merchandising (`inventory_levels`), operations (`late_shipments`), CRM (`customer_ltv`).
