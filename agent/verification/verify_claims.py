"""Is each claim supported by the query results it cites? One model call.

    run_verification(ctx, rca) -> {"passed": bool, "feedback": [problem, ...], "unsupported": [...]}

Judges every root-cause explanation and every claim against its cited queries (the SQL, the
columns, up to 20 result rows each, and each query's context: which warehouse it read, the
failed run before any fix or a replay after a given patch, and the agent turn), plus the
results of every run_replay (which support claims that the replay passes its checks). The judge must quote the exact unsupported phrase;
code keeps only phrases that really occur in the statement, and adds any number in a flagged
statement that appears in none of the cited results. The feedback names those phrases and
numbers, so the agent knows exactly what to query and cite. An unreadable judgement never blocks.
"""

from __future__ import annotations

import json
import re

from agent_kit.model import json_call

ROWS = 20
# dates and timestamps are one token ("2026-03-07", "2026-03-07 19:33:00"), never three numbers
NUMBER = re.compile(r"(?<![\w.:-])(?:\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?|\d+(?:[.,]\d+)?)(?![\w:-])")
PROMPT = """You check an incident report for a data pipeline. Each statement below cites the
read-only SQL queries that are supposed to prove it, with their results. Decide for each statement
whether the cited results support it as written. A statement that goes beyond its evidence, or
contradicts it, is unsupported. Interpretation that follows directly from the numbers is fine.

Each query carries its context. "warehouse": "failed run (before any fix)" is the pipeline as it
broke; "replay-N (after patch P..)" is a re-run of the night with the agent's fix applied. A claim
about the state "before the fix" is supported by a query on the failed run, and a claim "after the
fix" by a query on a replay after a patch, when the numbers match. The REPLAY RESULTS below come
from the pipeline's own runs: a claim that the replay passes (or fails) its checks, or how many
checks passed, is supported by them even without a query.

For every unsupported statement, quote in "phrases" the exact words or numbers from the statement
that the cited results do not show (copy them character for character, as short as possible).

REPLAY RESULTS:
{replays}

STATEMENTS:
{statements}

Answer only with JSON:
{{"unsupported": [{{"index": <number>, "phrases": ["<exact text from the statement>"], "reason": "one sentence"}}]}}
(an empty list when every statement is supported)."""


def _turns_by_query(ctx) -> dict:
    """query id -> agent turn, from the saved progress (for queries logged before turns were recorded)."""
    try:
        from agent_kit.progress import load
        turns = load(ctx.workspace, ctx.run_id)["turns"]
    except Exception:
        return {}
    out = {}
    for t in turns:
        result = (t.get("tool") or {}).get("result") or {}
        if isinstance(result, dict) and result.get("query_id"):
            out[result["query_id"]] = t.get("turn")
    return out


def query_context(ctx, q: dict, turns: dict | None = None) -> dict:
    """Where and when a query ran: warehouse (failed run / which replay), patch, turn."""
    patches = ctx.record.data["patches"]
    after = q.get("after_patch") if "after_patch" in q else next(
        (p["id"] for p in reversed(patches) if p["at"] <= q["at"]), None)
    run = q.get("run")
    if run is None:
        if q.get("target") == "replay":
            prior = [r for r in ctx.record.data["replays"] if r["at"] <= q["at"]]
            run = f"replay-{len(prior)}" if prior else "replay"
        else:
            run = "failed"
    if run == "failed":
        warehouse = "failed run (before any fix)"
    else:
        warehouse = f"{run} (after patch {after})" if after else f"{run} (no patch applied)"
    turn = q.get("turn") or (turns or {}).get(q["id"])
    return {"warehouse": warehouse, "turn": turn, "after_patch": after}


def replay_results(ctx) -> list:
    out = []
    for r in ctx.record.data["replays"]:
        out.append({"replay": r["id"], "status": r.get("status"), "after_patches": r.get("after_patches"),
                    "checks_passed": r.get("checks_passed"), "checks_total": r.get("checks_total"),
                    "failing": [c["id"] for c in (r.get("failing_contracts") or []) + (r.get("failing_tests") or [])],
                    "source_warnings": [w["id"] for w in r.get("source_warnings") or []]})
    return out


def statements(ctx, rca: dict) -> list:
    items = []
    for n, cause in enumerate(rca.get("root_causes") or [], 1):
        items.append((f"root cause {n}", f"({cause.get('fault_type')}) {cause.get('explanation', '')}", cause.get("query_ids")))
    for n, claim in enumerate(rca.get("claims") or [], 1):
        items.append((f"claim {n}", claim.get("claim", ""), claim.get("query_ids")))
    out = []
    turns = _turns_by_query(ctx)
    for i, (label, text, qids) in enumerate(items, 1):
        evidence = []
        for qid in qids or []:
            q = ctx.record.query(str(qid))
            if q and not q["error"]:
                meta = query_context(ctx, q, turns)
                evidence.append({"id": q["id"], "warehouse": meta["warehouse"], "turn": meta["turn"],
                                 "sql": q["sql"], "columns": q["columns"], "rows": q["rows"][:ROWS],
                                 "_all_rows": q["rows"]})
        out.append({"index": i, "label": label, "statement": text, "evidence": evidence})
    return out


def missing_numbers(statement: str, evidence: list, extra: list | None = None) -> list:
    """Numbers in the statement that appear in none of the cited results (all stored rows), nor
    in `extra` (values from the replay results)."""
    cells = set()
    for value in extra or []:
        cells.add(str(value).strip().lower())
        try:
            cells.add(repr(float(value)))
        except (TypeError, ValueError):
            pass
    for e in evidence:
        # literals in the cited SQL count as shown: a query filtered to a night shows that night
        for literal in re.findall(r"'([^']*)'", e.get("sql", "")) + NUMBER.findall(re.sub(r"'[^']*'", "", e.get("sql", ""))):
            cells.add(literal.strip().lower())
            try:
                cells.add(repr(float(literal)))
            except ValueError:
                pass
        for row in e["_all_rows"]:
            for cell in row:
                cells.add(str(cell).strip().lower())
                try:
                    cells.add(repr(float(cell)))
                except (TypeError, ValueError):
                    pass
    missing = []
    for token in NUMBER.findall(statement):
        if "-" in token:  # a date: it must appear as a cell (or the start of a timestamp cell)
            if not any(c == token.lower() or c.startswith(token.lower()) for c in cells) and token not in missing:
                missing.append(token)
            continue
        plain = token.replace(",", "")
        try:
            as_float = repr(float(plain))
        except ValueError:
            as_float = None
        if plain.lower() not in cells and as_float not in cells and token not in missing:
            missing.append(token)
    return missing


def prompt(shown: list, replays: list) -> str:
    return PROMPT.format(replays=json.dumps(replays, indent=1, default=str) if replays else "(no replay was run)",
                         statements=json.dumps(shown, indent=1, default=str))


def run_verification(ctx, rca: dict) -> dict:
    items = [s for s in statements(ctx, rca) if s["evidence"]]
    if not items:
        return {"passed": True, "feedback": []}
    shown = [{k: v for k, v in s.items() if k != "label"} for s in items]
    for s in shown:
        s["evidence"] = [{k: v for k, v in e.items() if k != "_all_rows"} for e in s["evidence"]]
    replays = replay_results(ctx)
    replay_numbers = [v for r in replays for v in (r["checks_passed"], r["checks_total"]) if v is not None]
    judged = json_call(ctx.client, prompt(shown, replays))
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
        numbers = [n for n in missing_numbers(text, item["evidence"], replay_numbers) if not any(n in p for p in phrases)]
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
