CREATE OR REPLACE TABLE stg_order_items AS
SELECT
    order_item_id,
    order_id,
    product_id,
    CAST(quantity AS INTEGER) AS quantity,
    CAST(unit_price AS DECIMAL(10, 2)) AS unit_price,
    _batch_night
FROM raw_order_items;
