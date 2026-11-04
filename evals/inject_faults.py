#!/usr/bin/env python3
"""Build labeled incidents from the pipeline template.

    python evals/inject_faults.py              # all incidents into incidents/, ground_truth/, world/
    python evals/inject_faults.py --only INC-001 INC-010

For each incident it generates clean data (seeded), saves the golden outputs of a clean
run, injects the fault(s) into a snapshot of the pipeline and/or the failed night's
batch, runs the broken night to produce the logs and the page (alert.md), and writes the
ground-truth label. 4 incidents per fault type, 6 clean-night controls, 4 two-fault
incidents. Split: by fault seed (variants 0-1 dev, 2-3 test), never random.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import importlib.util
import json
import random
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline_template.generator import generate  # noqa: E402
from pipeline_template.golden import TEMPLATE, build_golden, copy_template, run_on  # noqa: E402

START, NIGHTS = "2026-03-01", 7
NIGHT = generate.nights(START, NIGHTS)[-1]
FAULT_TYPES = [
    "schema_drift", "type_change", "null_spike", "late_partial", "duplicate_keys",
    "join_fanout", "timezone_shift", "code_regression", "config_error",
]
MARTS = ["daily_revenue", "inventory_levels", "late_shipments", "customer_ltv"]
MART_KEYS = {
    "daily_revenue": ["order_date", "category"],
    "inventory_levels": ["product_id"],
    "late_shipments": ["shipment_id"],
    "customer_ltv": ["customer_id"],
}
SUMS = [("daily_revenue", "gross_revenue"), ("daily_revenue", "net_revenue"),
        ("customer_ltv", "net_revenue"), ("inventory_levels", "units_sold_7d"),
        ("late_shipments", "hours_late")]
TOLERANCE = 0.005

# What the upstream data team says when asked (world/upstream_team.py serves these).
NOTHING_UPSTREAM = ("Nothing changed on our side for that night: the export ran on schedule, "
                    "with the usual schema and volumes.")


@dataclass
class State:
    batches: dict
    headers: dict
    pipeline: Path
    world: Path
    incident_id: str
    rng: random.Random
    night: str = NIGHT
    late_tables: dict = field(default_factory=dict)


def _source(table, column=None):
    return {"kind": "source", "table": table, "column": column}


def _edit(path: Path, old: str, new: str) -> int:
    """Replace `old` (once) with `new` in a model file; return the 1-based line of the change."""
    text = path.read_text()
    if text.count(old) != 1:
        raise RuntimeError(f"fault edit does not apply to {path.name}: {old!r}")
    index = text.index(old)
    path.write_text(text.replace(old, new))
    prefix = text[:index]
    # the first line that differs from the original
    common = 0
    while common < min(len(old), len(new)) and old[common] == new[common]:
        common += 1
    return prefix.count("\n") + old[:common].count("\n") + 1


# ---- faults -------------------------------------------------------------------------
# Each takes (state, variant) and returns the label for that fault.


def schema_drift(s: State, v: int) -> dict:
    table, old, new = [("orders", "order_ts", "ordered_at"), ("orders", "order_ts", "order_timestamp"),
                       ("shipments", "shipped_ts", "shipped_at"), ("order_items", "unit_price", "price")][v]
    for row in s.batches[s.night][table]:
        row[new] = row.pop(old)
    s.headers[(s.night, table)] = [new if c == old else c for c in generate.COLUMNS[table]]
    return {"root_location": _source(table, old), "expected_fix_type": "map_renamed_column",
            "params": {"table": table, "old": old, "new": new},
            "upstream": f"Yes: with our v2 export (shipped this week) the {table} column {old} is now "
                        f"called {new}. Values and formats are unchanged."}


PRICE_FORMATS = [lambda x: "$" + x, lambda x: "USD " + x, lambda x: x + " USD", lambda x: "US$" + x]


def type_change(s: State, v: int) -> dict:
    for row in s.batches[s.night]["order_items"]:
        row["unit_price"] = PRICE_FORMATS[v](row["unit_price"])
    return {"root_location": _source("order_items", "unit_price"), "expected_fix_type": "parse_currency",
            "params": {"table": "order_items", "column": "unit_price"},
            "upstream": "We moved order_items to the new pricing service; unit_price now arrives formatted "
                        "with a currency marker. Amounts are still US dollars, two decimals."}


def null_spike(s: State, v: int) -> dict:
    rate = [0.35, 0.4, 0.45, 0.5][v]
    orders = s.batches[s.night]["orders"]
    for row in s.rng.sample(orders, int(len(orders) * rate)):
        row["customer_id"] = ""
    return {"root_location": _source("orders", "customer_id"), "expected_fix_type": "backfill_from_email",
            "params": {"table": "orders", "column": "customer_id", "rate": rate},
            "upstream": "A guest-checkout bug dropped customer_id from some orders that night. "
                        "customer_email is still populated on every order and matches the customer's email."}


def late_partial(s: State, v: int) -> dict:
    keep = [0.45, 0.4, 0.35, 0.3][v]
    night = s.batches[s.night]
    full = {t: copy.deepcopy(night[t]) for t in ("orders", "order_items", "shipments")}
    kept = night["orders"][: int(len(night["orders"]) * keep)]
    ids = {o["order_id"] for o in kept}
    night["orders"] = kept
    night["order_items"] = [i for i in night["order_items"] if i["order_id"] in ids]
    dropped = {o["order_id"] for o in full["orders"]} - ids
    night["shipments"] = [x for x in night["shipments"] if x["order_id"] not in dropped]
    s.late_tables = full
    cutoff = kept[-1]["order_ts"][11:16] if kept else "00:00"
    return {"root_location": _source("orders"), "expected_fix_type": "rerun_after_backfill",
            "params": {"keep_fraction": keep},
            "upstream": f"Our export job for {s.night} timed out at {cutoff} and only delivered part of "
                        "the batch. We have re-delivered complete orders, order_items and shipments files "
                        "for that night; re-run once they land."}


def duplicate_keys(s: State, v: int) -> dict:
    tables, fraction = [("orders+items", 1.0), ("orders", 1.0), ("items", 0.5), ("orders+items", 0.3)][v]
    night = s.batches[s.night]
    if "orders" in tables:
        chosen = s.rng.sample(night["orders"], int(len(night["orders"]) * fraction))
        ids = {o["order_id"] for o in chosen}
        night["orders"] = night["orders"] + [dict(o) for o in chosen]
        if "items" in tables:
            night["order_items"] = night["order_items"] + [dict(i) for i in night["order_items"]
                                                           if i["order_id"] in ids]
        table = "orders"
    else:
        chosen = s.rng.sample(night["order_items"], int(len(night["order_items"]) * fraction))
        night["order_items"] = night["order_items"] + [dict(i) for i in chosen]
        table = "order_items"
    return {"root_location": _source(table), "expected_fix_type": "dedupe_source",
            "params": {"tables": tables, "fraction": fraction},
            "upstream": "Our retry logic re-sent part of that night's batch after a timeout. The re-sent rows "
                        "are exact copies; order_id and order_item_id remain reliable keys."}


FANOUTS = [
    ("models/marts/daily_revenue.sql", "    ON p.product_id = i.product_id\n",
     "    ON p.product_id = i.product_id\nJOIN stg_inventory AS v\n    ON v.product_id = i.product_id\n"),
    ("models/marts/daily_revenue.sql", "JOIN stg_products AS p\n", "JOIN raw_products AS p\n"),
    ("models/marts/inventory_levels.sql", "JOIN stg_products AS p\n", "JOIN raw_products AS p\n"),
    ("models/marts/daily_revenue.sql", "    ON i.order_id = o.order_id\n",
     "    ON i.order_id = o.order_id\nJOIN stg_order_items AS i2\n    ON i2.order_id = o.order_id\n"),
]


def join_fanout(s: State, v: int) -> dict:
    file, old, new = FANOUTS[v]
    line = _edit(s.pipeline / file, old, new)
    return {"root_location": {"kind": "model", "file": file, "line": line}, "expected_fix_type": "fix_join",
            "params": {"file": file}, "upstream": NOTHING_UPSTREAM}


TZ_FORMATS = [lambda d, t: f"{d}T{t}Z", lambda d, t: f"{d} {t}+00:00",
              lambda d, t: f"{d}T{t}.000Z", lambda d, t: f"{d} {t} UTC"]


def timezone_shift(s: State, v: int) -> dict:
    for row in s.batches[s.night]["orders"]:
        local = dt.datetime.strptime(row["order_ts"], "%Y-%m-%d %H:%M:%S")
        utc = local + dt.timedelta(hours=5)  # store time is UTC-05:00
        row["order_ts"] = TZ_FORMATS[v](utc.strftime("%Y-%m-%d"), utc.strftime("%H:%M:%S"))
    return {"root_location": _source("orders", "order_ts"), "expected_fix_type": "convert_timezone",
            "params": {"table": "orders", "column": "order_ts"},
            "upstream": "We moved the orders service to new hosts that night; order_ts is now written in UTC "
                        "(with an explicit UTC marker) instead of store-local time."}


REGRESSIONS = [
    ("models/marts/daily_revenue.sql", "WHERE o.status IN ('placed', 'returned')",
     "WHERE o.status IN ('shipped', 'returned')"),
    ("models/staging/stg_orders.sql", "CAST(CAST(order_ts AS TIMESTAMP) AS DATE) AS order_date",
     "CAST(CAST(order_ts AS TIMESTAMP) - INTERVAL 1 DAY AS DATE) AS order_date"),
    ("models/marts/late_shipments.sql", "WHERE hours_to_ship > promised_hours;",
     "WHERE hours_to_ship > promised_hours / 2;"),
    ("models/marts/inventory_levels.sql", "WHERE v.snapshot_date = DATE '{{ night }}';",
     "WHERE v.snapshot_date >= DATE '{{ night }}' - INTERVAL 1 DAY;"),
]


def code_regression(s: State, v: int) -> dict:
    file, old, new = REGRESSIONS[v]
    line = _edit(s.pipeline / file, old, new)
    return {"root_location": {"kind": "model", "file": file, "line": line},
            "expected_fix_type": "revert_model_change", "params": {"file": file}, "upstream": NOTHING_UPSTREAM}


CONFIGS = [("source_path", "../data/raw"), ("source_path", "../data_v2"),
           ("lookback_nights", 1), ("lookback_nights", 2)]


def config_error(s: State, v: int) -> dict:
    key, value = CONFIGS[v]
    path = s.pipeline / "config.json"
    config = json.loads(path.read_text())
    config[key] = value
    path.write_text(json.dumps(config, indent=2) + "\n")
    line = next(i for i, text in enumerate(path.read_text().splitlines(), 1) if f'"{key}"' in text)
    return {"root_location": {"kind": "config", "file": "config.json", "key": key, "line": line},
            "expected_fix_type": "fix_config", "params": {"key": key}, "upstream": NOTHING_UPSTREAM}


FAULTS = {f.__name__: f for f in (schema_drift, type_change, null_spike, late_partial, duplicate_keys,
                                   join_fanout, timezone_shift, code_regression, config_error)}
TWO_FAULT = [  # applied in this order (duplicates before rewrites of the duplicated rows)
    [("duplicate_keys", 0), ("type_change", 0)],
    [("schema_drift", 0), ("code_regression", 0)],
    [("null_spike", 1), ("join_fanout", 0)],
    [("duplicate_keys", 1), ("timezone_shift", 0)],
]


def plan() -> list:
    """Every incident to build: [{"id", "faults": [(type, variant)], "variant", "group"}]."""
    specs = []
    for faults, group in ([[(f, v)] for f in FAULT_TYPES for v in range(4)], "single"), \
                         ([[] for _ in range(6)], "control"), (TWO_FAULT, "two_fault"):
        for faults_ in faults:
            specs.append({"faults": faults_, "group": group})
    variant: dict = {}
    for n, spec in enumerate(specs, 1):
        spec["id"] = f"INC-{n:03d}"
        kind = spec["faults"][0][0] if spec["group"] == "single" else spec["group"]
        spec["variant"] = spec["faults"][0][1] if spec["group"] == "single" else variant.get(kind, 0)
        variant[kind] = spec["variant"] + 1
        half = 3 if spec["group"] == "control" else 2
        spec["split"] = "dev" if spec["variant"] < half else "test"
        kind_index = (FAULT_TYPES.index(kind) + 1) if kind in FAULT_TYPES else {"control": 20, "two_fault": 30}[kind]
        spec["fault_seed"] = kind_index * 100 + spec["variant"]
        spec["data_seed"] = 1000 + n
    return specs


def hidden_invariants() -> list:
    checks = [{"check": "row_count_equals_golden", "mart": m, "description": f"row count of {m} equals golden"}
              for m in MARTS]
    checks += [{"check": "keys_equal_golden", "mart": m, "key": MART_KEYS[m],
                "description": f"the keys ({', '.join(MART_KEYS[m])}) of {m} equal golden"} for m in MARTS]
    checks += [{"check": "sum_within_golden", "mart": m, "column": c, "tolerance": TOLERANCE,
                "description": f"sum({c}) of {m} within {TOLERANCE:.1%} of golden"} for m, c in SUMS]
    return checks


def _restamp_log(text: str, night: str) -> str:
    """Deterministic log timestamps: the morning after the night, one second per line."""
    day = (dt.date.fromisoformat(night) + dt.timedelta(days=1)).isoformat()
    lines = text.splitlines()
    out = []
    for i, line in enumerate(lines):
        stamp = f"{day}T02:{(14 + i // 60) % 60:02d}:{i % 60:02d}Z"
        out.append(re.sub(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", stamp, line))
    return "\n".join(out) + "\n"


def _load_runner(pipeline: Path):
    spec = importlib.util.spec_from_file_location(f"_runner_{id(pipeline)}", pipeline / "run_pipeline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _alert(incident_id: str, result: dict, night: str, control: bool) -> str:
    paged = (dt.date.fromisoformat(night) + dt.timedelta(days=1)).isoformat()
    head = (f"**Incident:** {incident_id} · **Pipeline:** eyewear_nightly (DuckDB, SQL models) · "
            f"**Night:** {night} · **Raised:** {paged} 02:15 UTC\n\n")
    consumers = ("Downstream consumers: finance (`daily_revenue`), merchandising (`inventory_levels`), "
                 "operations (`late_shipments`), CRM (`customer_ltv`).\n")
    if control:
        return (f"# TICKET: `daily_revenue` for {night} looks off\n\n" + head +
                f"The nightly run for **{night}** finished with status **{result['status']}** and no contract "
                "or test fired. An analyst on the finance team writes: *\"Net revenue for Saturday looks "
                "different from what I expected. Is the pipeline broken?\"*\n\n"
                "## Your job\n\nFind out whether anything is actually wrong with the pipeline or the data "
                "for this night. If something is broken, find the root cause, fix it in your sandbox, replay "
                "the night to prove the fix, and submit an RCA. If nothing is broken, show the evidence and "
                "submit an RCA that says so.\n\n" + consumers)
    fired = [f"- model error: {e.get('model') or e.get('table') or e.get('stage')}: "
             f"{str(e['error']).splitlines()[0][:220]}" for e in result["errors"]]
    fired += [f"- contract `{c['id']}` ({c['kind']}): {c['detail']}" for c in result["contracts"]
              if not c["passed"] and c.get("severity", "error") == "error"]
    fired += [f"- test `{t['id']}`: {t['detail']}" for t in result["tests"] if not t["passed"]]
    warned = [f"- contract `{c['id']}` ({c['kind']}, warning): {c['detail']}" for c in result["contracts"]
              if not c["passed"] and c.get("severity") == "warn"]
    return (f"# PAGE: eyewear_nightly failed for {night}\n\n" + head +
            f"The nightly run for **{night}** finished with status **{result['status']}**.\n\n"
            "## What fired\n\n" + "\n".join(fired) + "\n\n"
            + ("## Source warnings\n\n" + "\n".join(warned) + "\n\n" if warned else "")
            + "## Your job\n\nFind the root cause, fix it in your sandbox, replay the night to prove the fix, "
            "and submit an RCA. The full run log is in `logs/`.\n\n" + consumers)


def build_incident(spec: dict, root: Path = ROOT) -> dict:
    """Build one incident under root/incidents, root/ground_truth, root/world. Returns its label."""
    iid = spec["id"]
    incident = root / "incidents" / iid
    golden = root / "ground_truth" / "golden" / iid
    for folder in (incident, golden, root / "world" / "late_data" / iid):
        shutil.rmtree(folder, ignore_errors=True)
    incident.mkdir(parents=True)

    clean = generate.generate(spec["data_seed"], START, NIGHTS)
    with tempfile.TemporaryDirectory() as tmp:
        clean_dir = Path(tmp) / "clean"
        generate.write(clean, clean_dir)
        build_golden(clean_dir, golden, night=NIGHT)

    pipeline = copy_template(incident / "pipeline")
    for extra in ("__init__.py", "generator"):
        target = pipeline / extra
        shutil.rmtree(target) if target.is_dir() else target.unlink(missing_ok=True)
    state = State(batches=copy.deepcopy(clean), headers={}, pipeline=pipeline,
                  world=root / "world", incident_id=iid, rng=random.Random(spec["fault_seed"]))
    faults = []
    for fault_type, variant in spec["faults"]:
        label = FAULTS[fault_type](state, variant)
        faults.append(dict(label, fault_type=fault_type, variant=variant))
    generate.write(state.batches, incident / "data", state.headers)
    if state.late_tables:
        late = root / "world" / "late_data" / iid / f"night={NIGHT}"
        late.mkdir(parents=True)
        for table, rows in state.late_tables.items():
            (late / f"{table}.csv").write_text(generate.to_csv(rows, generate.COLUMNS[table]))

    logs = incident / "logs"
    logs.mkdir()
    previous = generate.nights(START, NIGHTS)[-2]
    with tempfile.TemporaryDirectory() as tmp:
        clean_dir = Path(tmp) / "clean"
        generate.write(clean, clean_dir)
        run_on(clean_dir, Path(tmp) / "prev", night=previous)
        prev_log = (Path(tmp) / "prev" / "run.log").read_text().replace(str(clean_dir.resolve()), "../data")
        (logs / f"run_{previous}.log").write_text(_restamp_log(prev_log, previous))
        result = _load_runner(pipeline).run(NIGHT, pipeline, Path(tmp) / "failed", quiet=True)
        (logs / f"run_{NIGHT}.log").write_text(_restamp_log((Path(tmp) / "failed" / "run.log").read_text(), NIGHT))

    control = not faults
    (incident / "alert.md").write_text(_alert(iid, result, NIGHT, control))
    (incident / "incident.json").write_text(json.dumps(
        {"incident_id": iid, "pipeline": "eyewear_nightly", "night": NIGHT,
         "nights": generate.nights(START, NIGHTS)}, indent=2) + "\n")

    first = faults[0] if faults else None
    truth = {
        "incident_id": iid,
        "group": spec["group"],
        "fault_type": first["fault_type"] if first else "none",
        "root_location": first["root_location"] if first else {"kind": "none"},
        "expected_fix_type": first["expected_fix_type"] if first else "no_change",
        "faults": [{k: f[k] for k in ("fault_type", "variant", "root_location", "expected_fix_type", "params")}
                   for f in faults],
        "hidden_invariants": hidden_invariants(),
        "golden_outputs": f"ground_truth/golden/{iid}/",
        "night": NIGHT,
        "split": spec["split"],
        "fault_seed": spec["fault_seed"],
        "data_seed": spec["data_seed"],
        "detected_by_contracts": result["status"] != "success",
    }
    (root / "ground_truth").mkdir(exist_ok=True)
    (root / "ground_truth" / f"{iid}.json").write_text(json.dumps(truth, indent=2) + "\n")

    upstream = root / "world" / "upstream"
    upstream.mkdir(parents=True, exist_ok=True)
    replies = [f["upstream"] for f in faults if f["upstream"] != NOTHING_UPSTREAM] or [NOTHING_UPSTREAM]
    (upstream / f"{iid}.json").write_text(json.dumps(
        {"incident_id": iid, "reply": " ".join(replies), "late_data": bool(state.late_tables)}, indent=2) + "\n")
    return truth


def main() -> None:
    parser = argparse.ArgumentParser(description="Build incidents and ground truth from the template.")
    parser.add_argument("--only", nargs="*", help="incident ids to (re)build")
    parser.add_argument("--root", default=str(ROOT), help="project root to write into")
    args = parser.parse_args()
    root = Path(args.root)
    specs = [s for s in plan() if not args.only or s["id"] in args.only]
    for spec in specs:
        truth = build_incident(spec, root)
        kinds = "+".join(f["fault_type"] for f in truth["faults"]) or "control"
        print(f"{spec['id']} {kinds:<32} {spec['split']:<4} detected={truth['detected_by_contracts']}")
    assert TEMPLATE.is_dir()


if __name__ == "__main__":
    main()
