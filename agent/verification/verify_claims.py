"""Is each claim supported by the query results it cites? One model call.

    run_verification(ctx, rca) -> {"passed": bool, "feedback": [problem, ...], "unsupported": [...]}

Judges every root-cause explanation and every claim against its cited queries (the SQL, the
columns, and up to 20 result rows each). The judge must quote the exact unsupported phrase;
code keeps only phrases that really occur in the statement, and adds any number in a flagged
statement that appears in none of the cited results. The feedback names those phrases and
numbers, so the agent knows exactly what to query and cite. An unreadable judgement never blocks.
"""

from __future__ import annotations

import json
import re

from agent_kit.model import json_call

ROWS = 20
NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?(?![\w])")
PROMPT = """You check an incident report for a data pipeline. Each statement below cites the
read-only SQL queries that are supposed to prove it, with their results. Decide for each statement
whether the cited results support it as written. A statement that goes beyond its evidence, or
contradicts it, is unsupported. Interpretation that follows directly from the numbers is fine.

For every unsupported statement, quote in "phrases" the exact words or numbers from the statement
that the cited results do not show (copy them character for character, as short as possible).

{statements}

Answer only with JSON:
{{"unsupported": [{{"index": <number>, "phrases": ["<exact text from the statement>"], "reason": "one sentence"}}]}}
(an empty list when every statement is supported)."""


def statements(ctx, rca: dict) -> list:
    items = []
    for n, cause in enumerate(rca.get("root_causes") or [], 1):
        items.append((f"root cause {n}", f"({cause.get('fault_type')}) {cause.get('explanation', '')}", cause.get("query_ids")))
    for n, claim in enumerate(rca.get("claims") or [], 1):
        items.append((f"claim {n}", claim.get("claim", ""), claim.get("query_ids")))
    out = []
    for i, (label, text, qids) in enumerate(items, 1):
        evidence = []
        for qid in qids or []:
            q = ctx.record.query(str(qid))
            if q and not q["error"]:
                evidence.append({"id": q["id"], "sql": q["sql"], "columns": q["columns"], "rows": q["rows"][:ROWS],
                                 "_all_rows": q["rows"]})
        out.append({"index": i, "label": label, "statement": text, "evidence": evidence})
    return out


def missing_numbers(statement: str, evidence: list) -> list:
    """Numbers in the statement that appear in none of the cited results (all stored rows)."""
    cells = set()
    for e in evidence:
        for row in e["_all_rows"]:
            for cell in row:
                cells.add(str(cell).strip().lower())
                try:
                    cells.add(repr(float(cell)))
                except (TypeError, ValueError):
                    pass
    missing = []
    for token in NUMBER.findall(statement):
        plain = token.replace(",", "")
        try:
            as_float = repr(float(plain))
        except ValueError:
            as_float = None
        if plain.lower() not in cells and as_float not in cells and token not in missing:
            missing.append(token)
    return missing


def run_verification(ctx, rca: dict) -> dict:
    items = [s for s in statements(ctx, rca) if s["evidence"]]
    if not items:
        return {"passed": True, "feedback": []}
    shown = [{k: v for k, v in s.items() if k != "label"} for s in items]
    for s in shown:
        s["evidence"] = [{k: v for k, v in e.items() if k != "_all_rows"} for e in s["evidence"]]
    judged = json_call(ctx.client, PROMPT.format(statements=json.dumps(shown, indent=1, default=str)))
    if judged is None:
        return {"passed": True, "feedback": [], "note": "unreadable judgement"}
    by_index = {s["index"]: s for s in items}
    problems, unsupported = [], []
    for u in judged.get("unsupported") or []:
        if not isinstance(u, dict) or u.get("index") not in by_index:
            continue
        item = by_index[u["index"]]
        text = item["statement"]
        phrases = [str(p) for p in u.get("phrases") or [] if str(p).strip() and str(p).lower() in text.lower()]
        numbers = [n for n in missing_numbers(text, item["evidence"]) if not any(n in p for p in phrases)]
        cited = ", ".join(e["id"] for e in item["evidence"])
        named = [f'"{p}"' for p in phrases] + [f'"{n}"' for n in numbers]
        if named:
            what = f"{', '.join(named)} {'is' if len(named) == 1 else 'are'} not shown by its cited queries ({cited})"
        else:
            what = f'"{text[:160]}" is not supported by its cited queries ({cited})'
        problems.append(f"{item['label']}: {what}. Run a query whose result shows it and cite that query id, "
                        f"or remove it. Judge: {u.get('reason', '')}")
        unsupported.append({"statement": item["label"], "phrases": phrases, "numbers": numbers})
    return {"passed": not problems, "feedback": problems, "unsupported": unsupported}
