-- One row per customer. Raw customers arrive as nightly sign-up deltas.
CREATE OR REPLACE TABLE stg_customers AS
SELECT
    customer_id,
    lower(email) AS email,
    region,
    CAST(signup_date AS DATE) AS signup_date,
    _batch_night
FROM raw_customers;
