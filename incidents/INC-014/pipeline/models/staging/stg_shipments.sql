CREATE OR REPLACE TABLE stg_shipments AS
SELECT
    shipment_id,
    order_id,
    service,
    CAST(promised_hours AS INTEGER) AS promised_hours,
    CAST(shipped_ts AS TIMESTAMP) AS shipped_ts,
    _batch_night
FROM raw_shipments;
