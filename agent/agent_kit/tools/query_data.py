"""query_data: read-only SQL over the run's DuckDB warehouse. SELECT (or WITH ... SELECT)
only, one statement, a row limit, no file access. Every query and its result is logged in
the incident record with an id (Q1, Q2, ...) that findings and the RCA must cite."""

from __future__ import annotations

import datetime as dt
import decimal
import re

import duckdb

DEFAULT_LIMIT, MAX_LIMIT = 50, 200
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|create|drop|alter|copy|attach|detach|install|load|pragma|export|import|call|"
    r"set|reset|checkpoint|vacuum|truncate|merge|grant|use)\b", re.I)
FILE_FUNCS = re.compile(r"\b(read_\w+|glob|parquet_\w+|sniff_csv)\s*\(", re.I)

DEFINITION = {
    "type": "function",
    "name": "query_data",
    "description": "Run one read-only SQL query (SELECT or WITH ... SELECT, DuckDB dialect) against the warehouse "
    "of the failed run or your latest replay. Tables: raw_* (as delivered, all VARCHAR, with _batch_night), "
    "stg_*, and the marts. Returns at most `limit` rows. Every query is logged with an id (Q1, Q2, ...): cite "
    "these ids in findings and in the RCA.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "sql": {"type": "string"},
            "target": {"type": "string", "enum": ["failed", "replay"], "description": "default failed"},
            "limit": {"type": "integer", "description": f"max rows returned (default {DEFAULT_LIMIT}, max {MAX_LIMIT})"},
        },
        "required": ["sql"],
    },
}


def strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", " ", sql)


def check_sql(sql: str) -> str | None:
    """Why the SQL is refused, or None."""
    body = strip_comments(sql).strip().rstrip(";").strip()
    no_strings = re.sub(r"'(?:[^']|'')*'", "''", body)
    if not body:
        return "the query is empty"
    if ";" in no_strings:
        return "one statement per query"
    if not re.match(r"^(select|with|from|values|describe|summarize|show)\b", no_strings, re.I):
        return "only read-only queries (SELECT / WITH ... SELECT) are allowed"
    if FORBIDDEN.search(no_strings):
        return f"read-only: {FORBIDDEN.search(no_strings).group(1).upper()} is not allowed"
    if FILE_FUNCS.search(no_strings):
        return "query the warehouse tables; reading files is not allowed"
    return None


def cell(value):
    if value is None or isinstance(value, (int, float, bool, str)):
        return value
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, dt.datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value)


def execute(db, sql: str, limit: int) -> tuple:
    con = duckdb.connect(str(db), read_only=True, config={"enable_external_access": False})
    try:
        cursor = con.execute(strip_comments(sql).strip().rstrip(";"))
        columns = [d[0] for d in cursor.description or []]
        rows = cursor.fetchmany(limit + 1)
        return columns, [[cell(v) for v in r] for r in rows[:limit]], len(rows) > limit
    finally:
        con.close()


def run(ctx, args: dict) -> dict:
    sql = str(args.get("sql", ""))
    target = args.get("target") or "failed"
    limit = min(max(int(args.get("limit") or DEFAULT_LIMIT), 1), MAX_LIMIT)
    problem = check_sql(sql)
    if problem:
        return {"error": f"query refused: {problem}"}
    db = ctx.sandbox.db(target)
    if not db.is_file():
        return {"error": f"no warehouse for {target!r} yet"}
    try:
        columns, rows, truncated = execute(db, sql, limit)
    except Exception as error:
        entry = ctx.record.log_query(sql, target, [], [], 0, str(error).splitlines()[0][:400])
        return {"query_id": entry["id"], "error": entry["error"]}
    entry = ctx.record.log_query(sql, target, columns, rows, len(rows))
    return {"query_id": entry["id"], "target": ctx.sandbox.latest() if target == "replay" else "failed",
            "columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated}
