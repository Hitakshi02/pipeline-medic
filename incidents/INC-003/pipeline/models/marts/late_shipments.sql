-- Shipments that left the warehouse later than the service promised.
CREATE OR REPLACE TABLE late_shipments AS
WITH timed AS (
    SELECT
        s.shipment_id,
        s.order_id,
        s.service,
        s.promised_hours,
        o.order_ts,
        s.shipped_ts,
        CAST(date_diff('minute', o.order_ts, s.shipped_ts) / 60.0 AS DECIMAL(8, 1)) AS hours_to_ship
    FROM stg_shipments AS s
    JOIN stg_orders AS o
        ON o.order_id = s.order_id
)
SELECT
    shipment_id,
    order_id,
    service,
    promised_hours,
    order_ts,
    shipped_ts,
    hours_to_ship,
    CAST(hours_to_ship - promised_hours AS DECIMAL(8, 1)) AS hours_late
FROM timed
WHERE hours_to_ship > promised_hours;
