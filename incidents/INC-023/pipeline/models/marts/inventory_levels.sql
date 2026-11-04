-- Stock position at the close of the target night, with the last 7 days of sales.
CREATE OR REPLACE TABLE inventory_levels AS
WITH sales AS (
    SELECT
        i.product_id,
        SUM(i.quantity) AS units_sold_7d
    FROM stg_order_items AS i
    JOIN stg_orders AS o
        ON o.order_id = i.order_id
    WHERE o.status = 'placed'
      AND o.order_date > DATE '{{ night }}' - INTERVAL 7 DAY
      AND o.order_date <= DATE '{{ night }}'
    GROUP BY i.product_id
)
SELECT
    v.product_id,
    p.sku,
    p.category,
    v.on_hand,
    COALESCE(s.units_sold_7d, 0) AS units_sold_7d,
    CASE
        WHEN COALESCE(s.units_sold_7d, 0) = 0 THEN NULL
        ELSE CAST(round(v.on_hand / (s.units_sold_7d / 7.0), 1) AS DECIMAL(8, 1))
    END AS days_of_cover
FROM stg_inventory AS v
JOIN raw_products AS p
    ON p.product_id = v.product_id
LEFT JOIN sales AS s
    ON s.product_id = v.product_id
WHERE v.snapshot_date = DATE '{{ night }}';
