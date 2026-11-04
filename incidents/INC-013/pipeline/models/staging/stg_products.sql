-- Products arrive as a full snapshot every night: keep the latest snapshot per product.
CREATE OR REPLACE TABLE stg_products AS
SELECT
    product_id,
    sku,
    name,
    category,
    CAST(list_price AS DECIMAL(10, 2)) AS list_price
FROM raw_products
QUALIFY row_number() OVER (PARTITION BY product_id ORDER BY _batch_night DESC) = 1;
