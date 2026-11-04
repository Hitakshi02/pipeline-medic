"""read_logs: the incident's run logs and the sandbox's own runs, paginated."""

PAGE_LINES = 60

DEFINITION = {
    "type": "function",
    "name": "read_logs",
    "description": "Read run logs, one page at a time. Without `log`, lists the available logs: the incident's "
    "logs (the failed night and the night before) and your sandbox runs (failed, replay-1, ...).",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "log": {"type": "string", "description": "a name from the listing, e.g. run_2026-03-07.log or replay-1"},
            "page": {"type": "integer", "description": "1-based page number (default 1)"},
        },
        "required": [],
    },
}


def _logs(ctx) -> dict:
    found = {p.name: p for p in sorted((ctx.incident / "logs").glob("*.log"))}
    for run in sorted(ctx.sandbox.runs.glob("*")):
        if (run / "run.log").is_file():
            found[run.name] = run / "run.log"
    return found


def run(ctx, args: dict) -> dict:
    logs = _logs(ctx)
    name = str(args.get("log") or "").strip()
    if not name:
        return {"logs": list(logs)}
    if name not in logs:
        return {"error": f"no log {name!r}; available: {', '.join(logs)}"}
    lines = logs[name].read_text().splitlines()
    pages = max(1, -(-len(lines) // PAGE_LINES))
    page = min(max(int(args.get("page") or 1), 1), pages)
    chunk = lines[(page - 1) * PAGE_LINES: page * PAGE_LINES]
    return {"log": name, "page": page, "pages": pages, "lines": chunk}
