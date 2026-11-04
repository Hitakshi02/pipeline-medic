-- Every product has a stock position for the target night.
SELECT p.product_id
FROM stg_products AS p
LEFT JOIN inventory_levels AS v ON v.product_id = p.product_id
WHERE v.product_id IS NULL;
