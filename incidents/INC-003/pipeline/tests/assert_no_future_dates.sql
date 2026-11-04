-- No mart row may be dated after the target night.
SELECT order_date FROM daily_revenue WHERE order_date > DATE '{{ night }}';
