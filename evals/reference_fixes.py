"""Reference fixes: one known-good fix per fault type, applied to a sandbox copy of an
incident. Used by the tests to prove every incident is solvable (the fixed replay
reproduces the golden outputs) and to build "good fix" labels for the verify_fix
classifier. Never shown to the agent; lives outside agent/.

    apply(truth, sandbox_pipeline, sandbox_data, project_root)
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / "pipeline_template"
STAGING = {
    "orders": "models/staging/stg_orders.sql",
    "order_items": "models/staging/stg_order_items.sql",
    "shipments": "models/staging/stg_shipments.sql",
}
OFFSET_PATTERN = r"(Z|[+-]00:00| UTC)$"


def _sub(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    if old not in text:
        raise RuntimeError(f"reference fix does not apply to {path.name}: {old!r}")
    path.write_text(text.replace(old, new))


def _renamed(pipeline: Path, p: dict) -> None:
    path = pipeline / STAGING[p["table"]]
    text = path.read_text()
    text = re.sub(rf"(?<!AS )\b{p['old']}\b", f"COALESCE({p['old']}, {p['new']})", text)
    path.write_text(text)


def _currency(pipeline: Path, p: dict) -> None:
    _sub(pipeline / STAGING["order_items"], "CAST(unit_price AS DECIMAL(10, 2))",
         "CAST(regexp_replace(unit_price, '[^0-9.]', '', 'g') AS DECIMAL(10, 2))")


def _backfill(pipeline: Path, p: dict) -> None:
    _sub(pipeline / STAGING["orders"], "    customer_id,\n",
         "    COALESCE(customer_id, (SELECT c.customer_id FROM raw_customers AS c\n"
         "                           WHERE lower(c.email) = lower(raw_orders.customer_email))) AS customer_id,\n")


def _dedupe(pipeline: Path, p: dict) -> None:
    for table in ("orders", "order_items"):
        _sub(pipeline / STAGING[table], f"FROM raw_{table};", f"FROM (SELECT DISTINCT * FROM raw_{table}) AS raw_{table};")


def _timezone(pipeline: Path, p: dict) -> None:
    _sub(pipeline / STAGING["orders"], "CAST(order_ts AS TIMESTAMP)",
         f"(CASE WHEN regexp_matches(order_ts, '{OFFSET_PATTERN}') "
         "THEN CAST(order_ts AS TIMESTAMP) + to_hours({{ store_utc_offset_hours }}) "
         "ELSE CAST(order_ts AS TIMESTAMP) END)")


def _revert(pipeline: Path, p: dict) -> None:
    shutil.copy2(TEMPLATE / p["file"], pipeline / p["file"])


def _config(pipeline: Path, p: dict) -> None:
    shutil.copy2(TEMPLATE / "config.json", pipeline / "config.json")


def _backfill_data(data: Path, root: Path, incident_id: str) -> None:
    late = root / "world" / "late_data" / incident_id
    for source in late.rglob("*.csv"):
        target = data / source.relative_to(late)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


CODE_FIXES = {
    "schema_drift": _renamed,
    "type_change": _currency,
    "null_spike": _backfill,
    "duplicate_keys": _dedupe,
    "timezone_shift": _timezone,
    "join_fanout": _revert,
    "code_regression": _revert,
    "config_error": _config,
}


def apply(truth: dict, pipeline: Path, data: Path, root: Path) -> None:
    """Fix every fault of the incident in the sandbox copy (pipeline/ and data/)."""
    for fault in truth["faults"]:
        if fault["fault_type"] == "late_partial":
            _backfill_data(Path(data), Path(root), truth["incident_id"])
        else:
            CODE_FIXES[fault["fault_type"]](Path(pipeline), fault["params"])
