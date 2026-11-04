"""inspect_schema: the columns of a table; for raw tables, the header of each night's batch."""

import duckdb

DEFINITION = {
    "type": "function",
    "name": "inspect_schema",
    "description": "Show a table's columns and types in the warehouse of the failed run (or your latest replay). "
    "For raw_* tables also lists the header of each night's source batch, so you can compare nights. "
    "Without `table`, lists the tables.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "table": {"type": "string", "description": "e.g. raw_orders, stg_orders, daily_revenue"},
            "target": {"type": "string", "enum": ["failed", "replay"], "description": "default failed"},
        },
        "required": [],
    },
}


def run(ctx, args: dict) -> dict:
    target = args.get("target") or "failed"
    db = ctx.sandbox.db(target)
    if not db.is_file():
        return {"error": f"no warehouse for {target!r} yet"}
    con = duckdb.connect(str(db), read_only=True)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name NOT LIKE '\\_%' ESCAPE '\\' "
            "ORDER BY table_name").fetchall()]
        table = str(args.get("table") or "").strip()
        if not table:
            return {"target": target, "tables": tables}
        if table not in tables:
            return {"error": f"no table {table!r} in the {target} warehouse; tables: {', '.join(tables)}"}
        columns = [{"name": r[0], "type": r[1]} for r in con.execute(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ? "
            "ORDER BY ordinal_position", [table]).fetchall()]
        out = {"target": target, "table": table, "columns": columns}
        if table.startswith("raw_"):
            out["batch_headers"] = {night: cols.split(",") for night, cols in con.execute(
                "SELECT night, columns FROM _batch_columns WHERE table_name = ? ORDER BY night",
                [table[4:]]).fetchall()}
        return out
    finally:
        con.close()
