# PAGE: eyewear_nightly failed for 2026-03-07

**Incident:** INC-004 · **Pipeline:** eyewear_nightly (DuckDB, SQL models) · **Night:** 2026-03-07 · **Raised:** 2026-03-08 02:15 UTC

The nightly run for **2026-03-07** finished with status **failed**.

## What fired

- contract `items_unit_price_nulls` (nulls): null rate 1.000 (233/233), max 0.0

## Source warnings

- contract `order_items_schema` (schema, warning): missing columns ['unit_price'], unexpected columns ['price']

## Your job

Find the root cause, fix it in your sandbox, replay the night to prove the fix, and submit an RCA. The full run log is in `logs/`.

Downstream consumers: finance (`daily_revenue`), merchandising (`inventory_levels`), operations (`late_shipments`), CRM (`customer_ltv`).
