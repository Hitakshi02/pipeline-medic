"""Is the RCA well-formed and grounded? Code only, no ground truth.

    run_verification(ctx, rca) -> {"passed": bool, "feedback": [problem, ...]}

- every root cause and every claim cites at least one logged, successful query
- every root location is real: a raw source table (and column, if given), a model file
  and line in the snapshot, or a config key; fault type 'none' goes with location 'none'
- a replay ran after the last change (a patch, or an upstream answer that may have landed data)
- no request is still open
"""

from __future__ import annotations

import csv
import json


def _cites(ctx, label: str, query_ids) -> list:
    query_ids = [str(q).strip() for q in query_ids or []]
    if not query_ids:
        return [f"{label} cites no query"]
    problems = []
    for qid in query_ids:
        q = ctx.record.query(qid)
        if q is None:
            problems.append(f"{label} cites {qid}, which is not a logged query")
        elif q["error"]:
            problems.append(f"{label} cites {qid}, which failed")
    return problems


def _source_columns(ctx) -> dict:
    """Every column each source table ever delivered, from the batch files' headers."""
    columns: dict = {}
    for path in sorted(ctx.sandbox.data.glob("night=*/*.csv")):
        with path.open(newline="") as stream:
            header = next(csv.reader(stream), [])
        columns.setdefault(path.stem, set()).update(header)
    return columns


def _location(ctx, i: int, cause: dict, sources: dict) -> list:
    loc = cause.get("location") or {}
    kind = loc.get("kind")
    label = f"root cause {i}"
    fault = cause.get("fault_type")
    if (fault == "none") != (kind == "none"):
        return [f"{label}: fault type 'none' goes with location kind 'none', and only with it"]
    if kind == "none":
        return []
    if kind == "source":
        table = str(loc.get("table") or "").removeprefix("raw_")
        if table not in sources:
            return [f"{label}: {loc.get('table')!r} is not a source table ({', '.join(sorted(sources))})"]
        column = loc.get("column")
        if column and column not in sources[table]:
            return [f"{label}: the source table {table} never delivered a column {column!r}"]
        return []
    if kind in ("model", "config"):
        file = str(loc.get("file") or ("config.json" if kind == "config" else ""))
        path = ctx.sandbox.resolve(file, ctx.sandbox.original) if file else None
        if not path or not path.is_file():
            return [f"{label}: {file or '(no file)'} is not a file of the pipeline snapshot"]
        if kind == "model" and not file.startswith("models/"):
            return [f"{label}: a model location must be a file under models/"]
        lines = path.read_text().splitlines()
        line = loc.get("line")
        if kind == "model" and not isinstance(line, int):
            return [f"{label}: give the line of the fault in {file}"]
        if isinstance(line, int) and not 1 <= line <= len(lines):
            return [f"{label}: {file} has {len(lines)} lines; line {line} does not exist"]
        if kind == "config":
            key = loc.get("key")
            config = json.loads(path.read_text()) if file.endswith(".json") else {}
            if not key or key not in config:
                return [f"{label}: name the config key ({', '.join(config)})"]
        return []
    return [f"{label}: unknown location kind {kind!r}"]


def run_verification(ctx, rca: dict) -> dict:
    problems: list = []
    causes = rca.get("root_causes") or []
    if not causes:
        problems.append("give at least one root cause")
    sources = _source_columns(ctx)
    for i, cause in enumerate(causes, 1):
        problems += _cites(ctx, f"root cause {i}", cause.get("query_ids"))
        problems += _location(ctx, i, cause, sources)
    for i, claim in enumerate(rca.get("claims") or [], 1):
        problems += _cites(ctx, f"claim {i}", claim.get("query_ids"))
    patches = ctx.record.data["patches"]
    answered = ctx.record.answered_upstream()
    replay = ctx.record.last_replay()
    changed = bool(patches) or answered > 0
    if changed and (replay is None or replay["after_patches"] < len(patches)
                    or replay.get("after_answers", 0) < answered):
        problems.append("replay the night (run_replay) after your last change before submitting")
    nothing = all(c.get("fault_type") == "none" for c in causes) if causes else False
    if not nothing and replay is None:
        problems.append("prove the fix: run run_replay before submitting")
    if ctx.record.open_requests():
        problems.append("a request is still open")
    return {"passed": not problems, "feedback": problems}
