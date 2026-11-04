-- Orders in store-local time.
CREATE OR REPLACE TABLE stg_orders AS
SELECT
    order_id,
    customer_id,
    lower(customer_email) AS customer_email,
    CAST(order_ts AS TIMESTAMP) AS order_ts,
    CAST(CAST(order_ts AS TIMESTAMP) - INTERVAL 1 DAY AS DATE) AS order_date,
    status,
    _batch_night
FROM raw_orders;
