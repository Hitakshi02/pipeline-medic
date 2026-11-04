# TICKET: `daily_revenue` for 2026-03-07 looks off

**Incident:** INC-040 · **Pipeline:** eyewear_nightly (DuckDB, SQL models) · **Night:** 2026-03-07 · **Raised:** 2026-03-08 02:15 UTC

The nightly run for **2026-03-07** finished with status **success** and no contract or test fired. An analyst on the finance team writes: *"Net revenue for Saturday looks different from what I expected. Is the pipeline broken?"*

## Your job

Find out whether anything is actually wrong with the pipeline or the data for this night. If something is broken, find the root cause, fix it in your sandbox, replay the night to prove the fix, and submit an RCA. If nothing is broken, show the evidence and submit an RCA that says so.

Downstream consumers: finance (`daily_revenue`), merchandising (`inventory_levels`), operations (`late_shipments`), CRM (`customer_ltv`).
