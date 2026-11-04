CREATE OR REPLACE TABLE stg_inventory AS
SELECT
    product_id,
    CAST(snapshot_date AS DATE) AS snapshot_date,
    CAST(on_hand AS INTEGER) AS on_hand,
    _batch_night
FROM raw_inventory;
