-- Revenue per order date and product category.
CREATE OR REPLACE TABLE daily_revenue AS
SELECT
    o.order_date,
    p.category,
    COUNT(DISTINCT o.order_id) AS orders,
    SUM(i.quantity) AS units,
    CAST(SUM(i.quantity * i.unit_price) AS DECIMAL(12, 2)) AS gross_revenue,
    CAST(SUM(CASE WHEN o.status = 'returned' THEN i.quantity * i.unit_price ELSE 0 END) AS DECIMAL(12, 2)) AS returned_revenue,
    CAST(SUM(CASE WHEN o.status = 'placed' THEN i.quantity * i.unit_price ELSE 0 END) AS DECIMAL(12, 2)) AS net_revenue
FROM stg_orders AS o
JOIN stg_order_items AS i
    ON i.order_id = o.order_id
JOIN stg_products AS p
    ON p.product_id = i.product_id
JOIN stg_inventory AS v
    ON v.product_id = i.product_id
WHERE o.status IN ('placed', 'returned')
GROUP BY o.order_date, p.category;
