#!/usr/bin/env python3
"""Run one night of the pipeline end to end: load raw batches, build models, check
contracts, run the visible tests, export the marts.

    python run_pipeline.py --night 2026-03-07                 # outputs in ./target/
    python run_pipeline.py --night 2026-03-07 --out /tmp/x    # somewhere else

Writes to the output folder:
    warehouse.duckdb   every raw_*, stg_* and mart table
    marts/<mart>.csv   the mart outputs, sorted (deterministic)
    run_result.json    status, model errors, contract and test results
                       (a "warn" contract reports the raw source batch and never fails the run)
    run.log            the run log

Exit code 0 when the night succeeds, 1 when anything failed.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
MARTS = ("daily_revenue", "inventory_levels", "late_shipments", "customer_ltv")


class Log:
    def __init__(self, path: Path, quiet: bool) -> None:
        self.path, self.quiet = path, quiet
        path.write_text("")

    def __call__(self, level: str, message: str) -> None:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = f"{stamp} {level:<5} {message}"
        with self.path.open("a") as stream:
            stream.write(line + "\n")
        if not self.quiet:
            print(line, flush=True)


def render(sql: str, variables: dict) -> str:
    for key, value in variables.items():
        sql = sql.replace("{{ " + key + " }}", str(value))
    return sql


def batch_nights(source: Path) -> list:
    return sorted(p.name.split("=", 1)[1] for p in source.glob("night=*") if p.is_dir())


def load_raw(con, source: Path, tables: list, nights: list, log: Log, shown: str | None = None) -> list:
    """`shown` is how the source is named in messages (the configured path, never an absolute one)."""
    """raw_<table> for every table (all VARCHAR, plus _batch_night). Returns errors."""
    errors = []
    con.execute("CREATE TABLE _batch_columns (table_name VARCHAR, night VARCHAR, columns VARCHAR)")
    for table in tables:
        files = [source / f"night={n}" / f"{table}.csv" for n in nights]
        files = [f for f in files if f.is_file()]
        if not files:
            message = f"no input files for table {table} under {shown or source.name}"
            log("ERROR", message)
            errors.append({"stage": "load", "table": table, "error": message})
            continue
        for f in files:
            with f.open(newline="") as stream:
                header = next(csv.reader(stream), [])
            night = f.parent.name.split("=", 1)[1]
            con.execute("INSERT INTO _batch_columns VALUES (?, ?, ?)", [table, night, ",".join(header)])
        listing = ", ".join("'" + str(f).replace("'", "''") + "'" for f in files)
        con.execute(
            f"CREATE TABLE raw_{table} AS "
            f"SELECT * EXCLUDE (filename), regexp_extract(filename, 'night=([0-9-]+)', 1) AS _batch_night "
            f"FROM read_csv([{listing}], all_varchar = true, union_by_name = true, header = true, filename = true)"
        )
        count = con.execute(f"SELECT count(*) FROM raw_{table}").fetchone()[0]
        log("INFO", f"loaded raw_{table}: {count} rows from {len(files)} batch(es)")
    return errors


def run_models(con, pipeline: Path, variables: dict, log: Log) -> list:
    errors = []
    for layer in ("staging", "marts"):
        for model in sorted((pipeline / "models" / layer).glob("*.sql")):
            name = f"models/{layer}/{model.name}"
            try:
                con.execute(render(model.read_text(), variables))
                log("INFO", f"built {model.stem}")
            except Exception as error:
                first = str(error).splitlines()[0]
                log("ERROR", f"model {name} failed: {first}")
                errors.append({"stage": "model", "model": name, "error": str(error)})
    return errors


def run_tests(con, tests_dir: Path, variables: dict, log: Log) -> list:
    results = []
    for test in sorted(tests_dir.glob("*.sql")):
        try:
            failing = len(con.execute(render(test.read_text(), variables)).fetchall())
            passed, detail = failing == 0, f"{failing} failing row(s)"
        except Exception as error:
            passed, detail = False, f"could not run: {str(error).splitlines()[0]}"
        log("INFO" if passed else "ERROR", f"test {test.stem}: {'pass' if passed else 'FAIL'} ({detail})")
        results.append({"id": test.stem, "passed": passed, "detail": detail})
    return results


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, dt.datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def export_marts(con, out: Path, log: Log) -> list:
    folder = out / "marts"
    folder.mkdir(parents=True, exist_ok=True)
    exported = []
    for mart in MARTS:
        try:
            cursor = con.execute(f"SELECT * FROM {mart} ORDER BY ALL")
        except Exception:
            log("WARN", f"mart {mart} was not built; not exported")
            continue
        columns = [d[0] for d in cursor.description]
        with (folder / f"{mart}.csv").open("w", newline="") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(columns)
            for row in cursor.fetchall():
                writer.writerow([_cell(v) for v in row])
        exported.append(mart)
    return exported


def run(
    night: str | None = None,
    pipeline: Path = HERE,
    out: Path | None = None,
    contracts_dir: Path | None = None,
    tests_dir: Path | None = None,
    quiet: bool = False,
) -> dict:
    pipeline = Path(pipeline).resolve()
    out = Path(out or pipeline / "target").resolve()
    out.mkdir(parents=True, exist_ok=True)
    contracts_dir = Path(contracts_dir or pipeline / "contracts")
    tests_dir = Path(tests_dir or pipeline / "tests")
    log = Log(out / "run.log", quiet)
    config = json.loads((pipeline / "config.json").read_text())
    source = (pipeline / config["source_path"]).resolve()
    available = batch_nights(source) if source.is_dir() else []
    night = night or (available[-1] if available else None)
    log("INFO", f"run started for night {night}; source {config['source_path']}")
    result: dict = {"night": night, "status": "failed", "errors": [], "contracts": [], "tests": [], "marts": []}
    if night is None:
        log("ERROR", f"no batches found under source path {config['source_path']}")
        result["errors"].append({"stage": "load", "error": f"no batches under {config['source_path']}"})
        return _finish(out, result, log)

    first = dt.date.fromisoformat(night) - dt.timedelta(days=int(config["lookback_nights"]) - 1)
    window = [n for n in available if first.isoformat() <= n <= night]
    variables = {
        "night": night,
        "window_start": first.isoformat(),
        "store_utc_offset_hours": config.get("store_utc_offset_hours", 0),
    }
    db = out / "warehouse.duckdb"
    if db.exists():
        db.unlink()
    con = duckdb.connect(str(db))
    try:
        result["errors"] += load_raw(con, source, config["tables"], window, log, shown=config["source_path"])
        # models and checks work only on the loaded tables: no file access past this point
        con.execute("SET enable_external_access = false")
        result["errors"] += run_models(con, pipeline, variables, log)
        sys.path.insert(0, str(contracts_dir))
        try:
            import importlib

            checker = importlib.import_module("checker")
            checker = importlib.reload(checker)
            result["contracts"] = checker.check(con, checker.load(contracts_dir / "contracts.yaml"), night)
        finally:
            sys.path.remove(str(contracts_dir))
        for c in result["contracts"]:
            log("INFO" if c["passed"] else ("WARN" if c["severity"] == "warn" else "ERROR"),
                f"contract {c['id']} ({c['kind']}): {'pass' if c['passed'] else 'FAIL'} - {c['detail']}")
        result["tests"] = run_tests(con, tests_dir, variables, log)
        result["marts"] = export_marts(con, out, log)
    finally:
        con.close()
    ok = not result["errors"] and all(c["passed"] for c in failing_checks(result, everything=True))
    result["status"] = "success" if ok else "failed"
    return _finish(out, result, log)


def failing_checks(result: dict, everything: bool = False) -> list:
    """Contracts (severity error) and tests; with everything=True, passing ones too."""
    checks = [c for c in result["contracts"] if c.get("severity", "error") == "error"] + result["tests"]
    return checks if everything else [c for c in checks if not c["passed"]]


def _finish(out: Path, result: dict, log: Log) -> dict:
    failed = [c["id"] for c in failing_checks(result)]
    log("INFO" if result["status"] == "success" else "ERROR",
        f"run {result['status']}: {len(result['errors'])} error(s), {len(failed)} failed check(s)"
        + (f" ({', '.join(failed)})" if failed else ""))
    (out / "run_result.json").write_text(json.dumps(result, indent=2))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one night of the pipeline.")
    parser.add_argument("--night", help="YYYY-MM-DD (default: the latest batch)")
    parser.add_argument("--out", help="output folder (default: ./target)")
    parser.add_argument("--contracts", help="contracts folder (default: ./contracts)")
    parser.add_argument("--tests", help="tests folder (default: ./tests)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    result = run(args.night, HERE, args.out and Path(args.out),
                 args.contracts and Path(args.contracts), args.tests and Path(args.tests), args.quiet)
    sys.exit(0 if result["status"] == "success" else 1)


if __name__ == "__main__":
    main()
