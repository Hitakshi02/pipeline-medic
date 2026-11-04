"""The contract checker (the detector). Read-only for the agent.

    results = check(con, contracts, night)
    # -> [{"id", "kind", "severity", "passed", "detail"}, ...]; severity "warn" never fails a run

`con` is a DuckDB connection with the raw_* tables (all VARCHAR, plus _batch_night),
the staging and mart tables, and _batch_columns(table_name, night, columns).
"""

from __future__ import annotations

from pathlib import Path

import yaml


def load(path: Path) -> list:
    return yaml.safe_load(Path(path).read_text())["contracts"]


def _scalar(con, sql: str, params: list | None = None):
    return con.execute(sql, params or []).fetchone()[0]


def _schema(con, c: dict, night: str) -> tuple:
    row = con.execute(
        "SELECT columns FROM _batch_columns WHERE table_name = ? AND night = ?", [c["table"], night]
    ).fetchone()
    if row is None:
        return False, f"no {c['table']} batch for {night}"
    got = row[0].split(",")
    missing = [col for col in c["columns"] if col not in got]
    extra = [col for col in got if col not in c["columns"]]
    if missing or extra:
        return False, f"missing columns {missing}, unexpected columns {extra}"
    return True, "columns as expected"


def _types(con, c: dict, night: str) -> tuple:
    col = c["column"]
    bad = _scalar(
        con,
        f'SELECT count(*) FROM raw_{c["table"]} WHERE _batch_night = ? AND "{col}" IS NOT NULL '
        f'AND TRY_CAST("{col}" AS {c["type"]}) IS NULL',
        [night],
    )
    return bad == 0, f"{bad} value(s) of {col} are not {c['type']}"


def _volume(con, c: dict, night: str) -> tuple:
    rows = con.execute(
        f"SELECT _batch_night, count(*) FROM {c['table']} GROUP BY 1 ORDER BY 1"
    ).fetchall()
    counts = {n: k for n, k in rows}
    today = counts.get(night, 0)
    history = sorted(k for n, k in counts.items() if n < night)
    if len(history) < 3:
        return True, f"{today} rows; not enough history for a baseline"
    mid = len(history) // 2
    median = history[mid] if len(history) % 2 else (history[mid - 1] + history[mid]) / 2
    ratio = today / median if median else 0.0
    passed = c["min_ratio"] <= ratio <= c["max_ratio"]
    return passed, f"{today} rows vs trailing median {median:g} (ratio {ratio:.2f})"


def _nulls(con, c: dict, night: str) -> tuple:
    total, nulls = con.execute(
        f'SELECT count(*), count(*) FILTER (WHERE "{c["column"]}" IS NULL) FROM {c["table"]} '
        "WHERE _batch_night = ?",
        [night],
    ).fetchone()
    rate = nulls / total if total else 0.0
    return rate <= c["max_rate"], f"null rate {rate:.3f} ({nulls}/{total}), max {c['max_rate']}"


def _freshness(con, c: dict, night: str) -> tuple:
    col = c["date_column"]
    total, off = con.execute(
        f'SELECT count(*), count(*) FILTER (WHERE "{col}" IS DISTINCT FROM DATE \'{night}\') '
        f'FROM {c["table"]} WHERE _batch_night = ?',
        [night],
    ).fetchone()
    if total == 0:
        return False, f"no rows for {night}"
    return off == 0, f"{off}/{total} rows of the {night} batch are dated another day"


def _unique(con, c: dict, night: str) -> tuple:
    key = ", ".join(f'"{k}"' for k in c["key"])
    dupes = _scalar(
        con, f"SELECT count(*) FROM (SELECT {key} FROM {c['table']} GROUP BY {key} HAVING count(*) > 1)"
    )
    return dupes == 0, f"{dupes} duplicated key value(s) of ({', '.join(c['key'])})"


def _invariant(con, c: dict, night: str) -> tuple:
    bad = _scalar(con, c["sql"].replace("{{ night }}", night))
    return bad == 0, f"{bad} violation(s): {c.get('description', '')}".rstrip(": ")


KINDS = {
    "schema": _schema,
    "types": _types,
    "volume": _volume,
    "nulls": _nulls,
    "freshness": _freshness,
    "unique": _unique,
    "invariant": _invariant,
}


def check(con, contracts: list, night: str) -> list:
    results = []
    for c in contracts:
        try:
            passed, detail = KINDS[c["kind"]](con, c, night)
        except Exception as error:  # a table the run never built, a bad cast, ...
            passed, detail = False, f"could not check: {str(error).splitlines()[0]}"
        results.append({"id": c["id"], "kind": c["kind"], "severity": c.get("severity", "error"),
                        "passed": bool(passed), "detail": detail})
    return results
