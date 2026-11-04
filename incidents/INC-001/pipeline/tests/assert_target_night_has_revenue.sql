-- The target night must appear in daily_revenue. Returns failing rows.
SELECT DATE '{{ night }}' AS missing_night
WHERE NOT EXISTS (SELECT 1 FROM daily_revenue WHERE order_date = DATE '{{ night }}');
