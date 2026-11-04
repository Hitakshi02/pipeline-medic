-- A late shipment is late by a positive number of hours.
SELECT shipment_id FROM late_shipments WHERE hours_late <= 0;
