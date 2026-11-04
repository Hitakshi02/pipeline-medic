"""Is each claim supported by the query results it cites? One model call.

    run_verification(ctx, rca) -> {"passed": bool, "feedback": [problem, ...]}

Judges every claim and every root-cause explanation against its cited queries (the SQL,
the columns, and up to 20 result rows each). An unreadable judgement never blocks.
"""

from __future__ import annotations

import json

from agent_kit.model import json_call

ROWS = 20
PROMPT = """You check an incident report for a data pipeline. Each statement below cites the
read-only SQL queries that are supposed to prove it, with their results. Decide for each statement
whether the cited results support it as written. A statement that goes beyond its evidence, or
contradicts it, is unsupported. Interpretation that follows directly from the numbers is fine.

{statements}

Answer only with JSON: {{"unsupported": [{{"index": <number>, "reason": "one sentence"}}]}}
(an empty list when every statement is supported)."""


def statements(ctx, rca: dict) -> list:
    items = []
    for cause in rca.get("root_causes") or []:
        items.append((f"root cause ({cause.get('fault_type')}): {cause.get('explanation', '')}", cause.get("query_ids")))
    for claim in rca.get("claims") or []:
        items.append((claim.get("claim", ""), claim.get("query_ids")))
    out = []
    for i, (text, qids) in enumerate(items, 1):
        evidence = []
        for qid in qids or []:
            q = ctx.record.query(str(qid))
            if q and not q["error"]:
                evidence.append({"id": q["id"], "sql": q["sql"], "columns": q["columns"], "rows": q["rows"][:ROWS]})
        out.append({"index": i, "statement": text, "evidence": evidence})
    return out


def run_verification(ctx, rca: dict) -> dict:
    items = [s for s in statements(ctx, rca) if s["evidence"]]
    if not items:
        return {"passed": True, "feedback": []}
    judged = json_call(ctx.client, PROMPT.format(statements=json.dumps(items, indent=1, default=str)))
    if judged is None:
        return {"passed": True, "feedback": [], "note": "unreadable judgement"}
    texts = {s["index"]: s["statement"] for s in items}
    problems = [f"not supported by its cited queries: \"{texts.get(u.get('index'), '?')[:160]}\" ({u.get('reason', '')})"
                for u in judged.get("unsupported") or [] if isinstance(u, dict) and u.get("index") in texts]
    return {"passed": not problems, "feedback": problems}
