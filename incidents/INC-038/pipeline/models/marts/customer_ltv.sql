-- Lifetime value per customer over the loaded window.
CREATE OR REPLACE TABLE customer_ltv AS
WITH order_values AS (
    SELECT
        o.order_id,
        o.customer_id,
        o.order_date,
        o.status,
        SUM(i.quantity * i.unit_price) AS order_value
    FROM stg_orders AS o
    JOIN stg_order_items AS i
        ON i.order_id = o.order_id
    GROUP BY o.order_id, o.customer_id, o.order_date, o.status
)
SELECT
    c.customer_id,
    c.region,
    MIN(v.order_date) AS first_order_date,
    COUNT(*) AS orders,
    CAST(SUM(CASE WHEN v.status = 'placed' THEN v.order_value ELSE 0 END) AS DECIMAL(12, 2)) AS net_revenue
FROM order_values AS v
JOIN stg_customers AS c
    ON c.customer_id = v.customer_id
GROUP BY c.customer_id, c.region;
