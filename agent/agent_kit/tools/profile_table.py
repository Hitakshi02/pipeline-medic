"""profile_table: row counts, null rates and distinct counts per batch night."""

import duckdb

DEFINITION = {
    "type": "function",
    "name": "profile_table",
    "description": "Profile a table that has a _batch_night column (raw_* and stg_* tables): row count per night, "
    "and for the target night vs the earlier nights, each column's null rate and distinct count.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "table": {"type": "string"},
            "target": {"type": "string", "enum": ["failed", "replay"], "description": "default failed"},
        },
        "required": ["table"],
    },
}


def run(ctx, args: dict) -> dict:
    target = args.get("target") or "failed"
    table = str(args.get("table", "")).strip()
    db = ctx.sandbox.db(target)
    if not db.is_file():
        return {"error": f"no warehouse for {target!r} yet"}
    con = duckdb.connect(str(db), read_only=True)
    try:
        cols = [r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ? ORDER BY ordinal_position",
            [table]).fetchall()]
        if not cols:
            return {"error": f"no table {table!r} in the {target} warehouse"}
        if "_batch_night" not in cols:
            return {"error": f"{table} has no _batch_night column; use query_data instead"}
        counts = dict(con.execute(f'SELECT _batch_night, count(*) FROM "{table}" GROUP BY 1 ORDER BY 1').fetchall())
        night = ctx.night
        columns = {}
        for col in cols:
            if col == "_batch_night":
                continue
            row = con.execute(
                f'SELECT avg(CASE WHEN "{col}" IS NULL THEN 1.0 ELSE 0 END) FILTER (WHERE _batch_night = ?), '
                f'avg(CASE WHEN "{col}" IS NULL THEN 1.0 ELSE 0 END) FILTER (WHERE _batch_night < ?), '
                f'count(DISTINCT "{col}") FILTER (WHERE _batch_night = ?) '
                f'FROM "{table}"', [night, night, night]).fetchone()
            columns[col] = {"null_rate_night": round(row[0] or 0, 4), "null_rate_before": round(row[1] or 0, 4),
                            "distinct_night": row[2]}
        return {"target": target, "table": table, "night": night, "rows_per_night": counts, "columns": columns}
    finally:
        con.close()
