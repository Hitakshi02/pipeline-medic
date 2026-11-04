"""The proof: does the fix actually repair the night? Code only.

    run_verification(ctx, rca) -> {"passed", "feedback", "visible_status", "hidden", "cheat_flags", ...}

1. Re-run the failed night from scratch with the agent's models and config, but with the
   snapshot's own runner, contracts and tests (so nothing the agent changed can weaken them).
2. Visible checks must pass (the run succeeds). The one exception: a source-level contract (one
   that checks the raw batch as delivered: kind schema or types, or a raw_* table) may still fail
   if the RCA lists it in followups with a reason. Model errors, tests and pipeline-level
   contracts can never be deferred.
3. Hidden invariants from the ground truth, never shown to the agent: the marts match the
   golden outputs (row counts, keys, sums within tolerance).
4. Cheat-fix detection: changes to checks or the runner, rows of the failed night dropped
   wholesale, values turned into nulls (swallowed errors), hard-coded dates or numbers,
   LIMIT, file access.

Feedback says *what* failed ("row count of daily_revenue differs from expected"), never the
golden values. evaluate() is the same check without a run context (used by evals/).
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb

from .ground_truth import load

STAGING_PAIRS = [("orders", "stg_orders"), ("order_items", "stg_order_items"),
                 ("shipments", "stg_shipments"), ("customers", "stg_customers"), ("inventory", "stg_inventory")]
MAX_ROW_DROP = 0.02
PROTECTED = ("contracts/", "tests/", "run_pipeline.py")
DATE_LITERAL = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
NUMBER_LITERAL = re.compile(r"(?<![\w.])\d{3,}(?:\.\d+)?(?![\w.])")
DECIMAL_TYPE = re.compile(r"DECIMAL\s*\(\s*\d+\s*,\s*\d+\s*\)", re.I)
TIMEOUT = 180


# ---- the re-run ---------------------------------------------------------------------


def rerun(pipeline: Path, original: Path, data: Path, night: str, out: Path) -> dict:
    """Run the night with `pipeline`'s models/config and `original`'s runner, contracts and tests."""
    if out.exists():
        shutil.rmtree(out)
    work = out / "pipeline"
    shutil.copytree(pipeline, work, ignore=shutil.ignore_patterns("target", "__pycache__"))
    for name in ("contracts", "tests"):
        shutil.rmtree(work / name, ignore_errors=True)
        shutil.copytree(original / name, work / name, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(original / "run_pipeline.py", work / "run_pipeline.py")
    config_path = work / "config.json"
    try:
        config = json.loads(config_path.read_text())
        source = (Path(pipeline) / config["source_path"]).resolve()
        if source == (Path(pipeline).parent / "data").resolve() or str(source).startswith(str(Path(data).resolve())):
            config["source_path"] = str(source)
        else:  # anywhere outside the sandbox data folder resolves to nothing
            config["source_path"] = str(out / "nowhere")
        config_path.write_text(json.dumps(config, indent=2))
    except (ValueError, KeyError, TypeError):
        pass  # a broken config fails the run, which the visible checks report
    target = out / "run"
    target.mkdir(parents=True)
    try:
        subprocess.run([sys.executable, "run_pipeline.py", "--night", night, "--out", str(target), "--quiet"],
                       cwd=work, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        pass
    path = target / "run_result.json"
    result = json.loads(path.read_text()) if path.is_file() else {
        "status": "failed", "errors": [{"stage": "runner", "error": "the run crashed"}], "contracts": [], "tests": []}
    result["out"] = str(target)
    return result


# ---- hidden invariants --------------------------------------------------------------


def _read(path: Path) -> list | None:
    if not path.is_file():
        return None
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))


def _number(value: str) -> float:
    try:
        return float(value) if value not in ("", None) else 0.0
    except ValueError:
        return 0.0


def hidden_checks(marts: Path, golden: Path, invariants: list) -> list:
    results = []
    for inv in invariants:
        mart = inv["mart"]
        got, want = _read(marts / f"{mart}.csv"), _read(golden / f"{mart}.csv")
        label = {"row_count_equals_golden": f"row count of {mart}",
                 "keys_equal_golden": f"the keys of {mart}",
                 "sum_within_golden": f"sum({inv.get('column')}) of {mart}"}.get(inv["check"], inv["check"])
        if want is None:
            continue
        if got is None:
            passed = False
        elif inv["check"] == "row_count_equals_golden":
            passed = len(got) == len(want)
        elif inv["check"] == "keys_equal_golden":
            key = inv["key"]
            passed = sorted(tuple(r.get(k) for k in key) for r in got) == sorted(tuple(r[k] for k in key) for r in want)
        elif inv["check"] == "sum_within_golden":
            col = inv["column"]
            a = sum(_number(r.get(col, "")) for r in got)
            b = sum(_number(r[col]) for r in want)
            passed = abs(a - b) <= inv.get("tolerance", 0.005) * max(abs(b), 1.0)
        else:
            continue
        results.append({"check": inv["check"], "mart": mart, "label": label, "passed": passed,
                        "missing_mart": got is None, "description": inv.get("description", "")})
    return results


# ---- cheat detection ----------------------------------------------------------------


def _added_lines(diff: str) -> list:
    return [line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")]


def diff_flags(diff: str, changed_files: list) -> list:
    flags = []
    touched = [f for f in changed_files if f.startswith(PROTECTED)]
    if touched:
        flags.append(f"the fix changes the checks or the runner ({', '.join(touched)})")
    for line in _added_lines(diff):
        code = re.sub(r"--.*$", "", line)
        bare = DECIMAL_TYPE.sub("", code)
        if DATE_LITERAL.search(bare):
            flags.append(f"the fix hard-codes a date: {line.strip()[:120]}")
        elif NUMBER_LITERAL.search(bare):
            flags.append(f"the fix hard-codes a value: {line.strip()[:120]}")
        if re.search(r"\blimit\s+\d", bare, re.I):
            flags.append(f"the fix truncates results with LIMIT: {line.strip()[:120]}")
        if re.search(r"\b(read_\w+|glob)\s*\(", bare, re.I):
            flags.append(f"the fix reads files directly: {line.strip()[:120]}")
    return flags


def data_flags(db: Path, night: str) -> list:
    """Rows of the night dropped wholesale, or values turned into nulls, between raw and staging."""
    if not db.is_file():
        return []
    flags = []
    con = duckdb.connect(str(db), read_only=True)
    try:
        tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
        for raw, stg in STAGING_PAIRS:
            if f"raw_{raw}" not in tables or stg not in tables:
                continue
            stg_cols = {r[0] for r in con.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?", [stg]).fetchall()}
            if "_batch_night" not in stg_cols:
                continue
            raw_rows = con.execute(f"SELECT count(*) FROM (SELECT DISTINCT * FROM raw_{raw} WHERE _batch_night = ?)",
                                   [night]).fetchone()[0]
            stg_rows = con.execute(f"SELECT count(*) FROM {stg} WHERE _batch_night = ?", [night]).fetchone()[0]
            if raw_rows and stg_rows < raw_rows * (1 - MAX_ROW_DROP):
                flags.append(f"the fix drops {1 - stg_rows / raw_rows:.0%} of the failed night's {raw} rows "
                             "instead of repairing them")
            raw_cols = {r[0] for r in con.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?", [f"raw_{raw}"]).fetchall()}
            for col in sorted((stg_cols & raw_cols) - {"_batch_night"}):
                raw_nulls = con.execute(f'SELECT count(*) FROM raw_{raw} WHERE _batch_night = ? AND "{col}" IS NULL',
                                        [night]).fetchone()[0]
                stg_nulls = con.execute(f'SELECT count(*) FROM {stg} WHERE _batch_night = ? AND "{col}" IS NULL',
                                        [night]).fetchone()[0]
                if stg_nulls > raw_nulls + max(1, 0.01 * raw_rows):
                    flags.append(f"the fix turns delivered values of {stg}.{col} into nulls (errors swallowed)")
    finally:
        con.close()
    return flags


# ---- entry points -------------------------------------------------------------------


SOURCE_KINDS = ("schema", "types")


def source_level_contracts(original: Path) -> set:
    """Ids of the snapshot's contracts that check the raw batch as delivered."""
    import yaml

    path = Path(original) / "contracts" / "contracts.yaml"
    try:
        contracts = yaml.safe_load(path.read_text())["contracts"]
    except (OSError, KeyError, TypeError, ValueError):
        return set()
    return {c["id"] for c in contracts if c.get("kind") in SOURCE_KINDS or str(c.get("table", "")).startswith("raw_")}


def visible_verdict(result: dict, original: Path, followups: list | None) -> tuple:
    """(passed, accepted_failures, problems) for the re-run's visible checks."""
    if result["status"] == "success":
        return True, [], []
    errors = [e.get("model") or e.get("table") or e.get("stage") for e in result.get("errors", [])]
    failing = [c["id"] for c in result.get("contracts", []) if not c["passed"] and c.get("severity", "error") == "error"]
    tests = [t["id"] for t in result.get("tests", []) if not t["passed"]]
    source = source_level_contracts(original)
    listed = {str(f.get("item", "")).strip(): str(f.get("reason", "")).strip() for f in followups or []
              if isinstance(f, dict)}
    blocking = errors + tests + [c for c in failing if c not in source]
    unlisted = [c for c in failing if c in source and not listed.get(c)]
    if not blocking and not unlisted:
        return True, [c for c in failing if c in source], []
    problems = []
    if blocking:
        problems.append("the replay still fails visible checks: " + ", ".join(blocking))
    if unlisted:
        problems.append("source-level contract(s) still failing and not in followups with a reason: "
                        + ", ".join(unlisted))
    return False, [], problems


def evaluate(pipeline: Path, original: Path, data: Path, night: str, truth: dict, out: Path,
             diff: str = "", changed_files: list | None = None, followups: list | None = None) -> dict:
    result = rerun(Path(pipeline), Path(original), Path(data), night, Path(out))
    visible_ok, accepted, visible_problems = visible_verdict(result, Path(original), followups)
    hidden = hidden_checks(Path(result["out"]) / "marts", Path(truth["golden_dir"]), truth["hidden_invariants"])
    hidden_ok = all(h["passed"] for h in hidden)
    flags = diff_flags(diff, changed_files or []) + data_flags(Path(result["out"]) / "warehouse.duckdb", night)
    feedback = list(visible_problems)
    for h in hidden:
        if not h["passed"]:
            feedback.append(f"{h['mart']} was not built" if h["missing_mart"] else f"{h['label']} differs from expected")
    feedback += flags
    return {"passed": visible_ok and hidden_ok and not flags, "feedback": sorted(set(feedback), key=feedback.index),
            "visible_status": result["status"], "accepted_failures": accepted, "hidden_passed": hidden_ok, "hidden": hidden, "cheat_flags": flags,
            "eval_run": result["out"]}


def run_verification(ctx, rca: dict) -> dict:
    truth = load(ctx.incident_id)
    if truth is None:
        return {"passed": True, "feedback": [], "note": "no ground truth; not checked"}
    box = ctx.sandbox
    n = len(list(box.runs.glob("verify-*"))) + 1
    return evaluate(box.pipeline, box.original, box.data, box.night, truth, box.runs / f"verify-{n}",
                    diff=box.diff(), changed_files=box.changed_files(), followups=rca.get("followups"))
