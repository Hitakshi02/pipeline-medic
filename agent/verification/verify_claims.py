"""Is each claim supported by the query results it cites? One model call.

    run_verification(ctx, rca) -> {"passed": bool, "feedback": [problem, ...], "unsupported": [...]}

Judges every root-cause explanation and every claim. Evidence comes in labeled kinds, and a
claim is supported only by the kind that can show it:

- query_results     cited queries: SQL, columns, up to 20 rows, and context (the warehouse it
                    read: failed run before any fix, or replay-N after patch Pn; the turn)
- failed_run_checks the failed night's model errors, failing contracts and tests (what fired)
- original_code     the snapshot's lines at each claimed model/config location, numbered
- patch_diff        the agent's patches
- replay_results    every run_replay: status and checks passed

The fault-type label of a root cause is a classification, not a claim, and is not judged.
The judge must quote the exact unsupported phrase;
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
PROMPT = """You check an incident report for a data pipeline. Decide for each statement whether
the evidence supports it as written. A statement that goes beyond its evidence, or contradicts it,
is unsupported. Interpretation that follows directly from the evidence is fine.

The evidence is labeled by kind. Accept a statement only on evidence of the kind that can show it:
- facts about the data (counts, values, rows, nulls, duplicates, dates): its own cited QUERY RESULTS.
  Each query has a "warehouse": "failed run (before any fix)" is the pipeline as it broke;
  "replay-N (after patch P..)" is the night re-run with the fix. "Before the fix" claims need a
  failed-run query, "after the fix" claims a replay query.
- what the SQL or the config does or did (a join, a filter, a cast, a setting, a line): ORIGINAL CODE
  (as it was at failure) and PATCH DIFF (what the fix changed).
- which contracts or tests fired, errors, and their details on the failed night: FAILED RUN CHECKS.
- whether the replay passes its checks, and how many: REPLAY RESULTS.
Do not accept a data fact on the strength of code, or a code fact on the strength of query numbers.
A root cause's "classification" (its fault type and location) is a label, not a claim: never judge it.

For every unsupported statement, quote in "phrases" the exact words or numbers from the statement
that its evidence does not show (copy them character for character, as short as possible).

FAILED RUN CHECKS:
{failed_checks}

ORIGINAL CODE (at the claimed locations, numbered lines):
{original_code}

PATCH DIFF:
{patch_diff}

REPLAY RESULTS:
{replays}

STATEMENTS (each with its own cited QUERY RESULTS):
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


DIFF_CHARS = 4000
CODE_CONTEXT = 4


def failed_run_checks(ctx) -> dict | None:
    """The failed night's model errors, failing contracts and tests (what fired), or None."""
    try:
        result = ctx.sandbox.result("failed")
    except Exception:
        result = {}
    if not result:
        return None
    contracts = result.get("contracts", [])
    return {"status": result.get("status"),
            "errors": [{"where": e.get("model") or e.get("table") or e.get("stage"),
                        "error": str(e.get("error", "")).splitlines()[0][:300]} for e in result.get("errors", [])],
            "failing_contracts": [{"id": c["id"], "kind": c["kind"], "detail": c["detail"]} for c in contracts
                                  if not c["passed"] and c.get("severity", "error") == "error"],
            "failing_tests": [{"id": t["id"], "detail": t["detail"]} for t in result.get("tests", []) if not t["passed"]],
            "source_warnings": [{"id": c["id"], "detail": c["detail"]} for c in contracts
                                if not c["passed"] and c.get("severity") == "warn"]}


def _snapshot_file(ctx, rel: str):
    from pathlib import Path, PurePosixPath
    rel = str(PurePosixPath(str(rel).strip().lstrip("/")))
    if ".." in rel.split("/"):
        return None
    for base in (ctx.sandbox.original, Path(ctx.incident) / "pipeline"):
        path = Path(base) / rel
        if path.is_file():
            return path
    return None


def original_code(ctx, rca: dict) -> list:
    """Numbered snapshot lines around each claimed model/config location."""
    out, seen = [], set()
    for n, cause in enumerate(rca.get("root_causes") or [], 1):
        loc = cause.get("location") or {}
        if loc.get("kind") not in ("model", "config"):
            continue
        rel = loc.get("file") or ("config.json" if loc.get("kind") == "config" else "")
        path = _snapshot_file(ctx, rel) if rel else None
        if path is None or (rel, loc.get("line")) in seen:
            continue
        seen.add((rel, loc.get("line")))
        lines = path.read_text().splitlines()
        line = loc.get("line") if isinstance(loc.get("line"), int) else None
        lo, hi = (max(1, line - CODE_CONTEXT), min(len(lines), line + CODE_CONTEXT)) if line else (1, min(len(lines), 60))
        out.append({"root_cause": n, "file": rel, "claimed_line": line,
                    "lines": {str(i): lines[i - 1] for i in range(lo, hi + 1)}})
    return out


def patch_diff(ctx) -> list:
    return [{"patch": p["id"], "file": p["path"], "diff": p["diff"][:DIFF_CHARS]} for p in ctx.record.data["patches"]]


def shared_evidence(ctx, rca: dict) -> dict:
    return {"failed_run_checks": failed_run_checks(ctx), "original_code": original_code(ctx, rca),
            "patch_diff": patch_diff(ctx), "replay_results": replay_results(ctx)}


def evidence_numbers(shared: dict) -> list:
    """Every number and date in the non-query evidence (check details, code lines and line numbers,
    diffs, replay counts): a statement may quote them."""
    found = []
    for key in ("failed_run_checks", "original_code", "patch_diff", "replay_results"):
        text = json.dumps(shared.get(key) or [], default=str)
        found += NUMBER.findall(text.replace('\\n', ' ').replace("\\", " "))
    return found


def statements(ctx, rca: dict) -> list:
    items = []
    for n, cause in enumerate(rca.get("root_causes") or [], 1):
        items.append((f"root cause {n}", str(cause.get("explanation", "")), cause.get("query_ids"),
                      {"fault_type": cause.get("fault_type"), "location": cause.get("location")}))
    for n, claim in enumerate(rca.get("claims") or [], 1):
        items.append((f"claim {n}", claim.get("claim", ""), claim.get("query_ids"), None))
    out = []
    turns = _turns_by_query(ctx)
    for i, (label, text, qids, classification) in enumerate(items, 1):
        evidence = []
        for qid in qids or []:
            q = ctx.record.query(str(qid))
            if q and not q["error"]:
                meta = query_context(ctx, q, turns)
                evidence.append({"id": q["id"], "warehouse": meta["warehouse"], "turn": meta["turn"],
                                 "sql": q["sql"], "columns": q["columns"], "rows": q["rows"][:ROWS],
                                 "_all_rows": q["rows"]})
        entry = {"index": i, "label": label, "statement": text, "evidence": evidence}
        if classification:
            entry["classification"] = classification
        out.append(entry)
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


def prompt(shown: list, shared: dict) -> str:
    def block(value, empty):
        return json.dumps(value, indent=1, default=str) if value else empty
    return PROMPT.format(failed_checks=block(shared.get("failed_run_checks"), "(not available)"),
                         original_code=block(shared.get("original_code"), "(no model or config location claimed)"),
                         patch_diff=block(shared.get("patch_diff"), "(no patch)"),
                         replays=block(shared.get("replay_results"), "(no replay was run)"),
                         statements=json.dumps(shown, indent=1, default=str))


def shown_statements(items: list) -> list:
    shown = [{k: v for k, v in s.items() if k != "label"} for s in items]
    for s in shown:
        s["query_results"] = [{k: v for k, v in e.items() if k != "_all_rows"} for e in s.pop("evidence")]
    return shown


def run_verification(ctx, rca: dict) -> dict:
    items = [s for s in statements(ctx, rca) if s["evidence"]]
    if not items:
        return {"passed": True, "feedback": []}
    shown = shown_statements(items)
    shared = shared_evidence(ctx, rca)
    extra = evidence_numbers(shared)
    judged = json_call(ctx.client, prompt(shown, shared))
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
        numbers = [n for n in missing_numbers(text, item["evidence"], extra) if not any(n in p for p in phrases)]
        cited = ", ".join(e["id"] for e in item["evidence"])
        named = [f'"{p}"' for p in phrases] + [f'"{n}"' for n in numbers]
        if named:
            what = f"{', '.join(named)} {'is' if len(named) == 1 else 'are'} not shown by its evidence (queries {cited})"
        else:
            what = f'"{text[:160]}" is not supported by its evidence (queries {cited})'
        problems.append(f"{item['label']}: {what}. Back it with the right kind of evidence (a query result for "
                        f"data; the code or your patch for what the SQL does; the failed run's checks for what "
                        f"fired), or remove it. Judge: {u.get('reason', '')}")
        unsupported.append({"statement": item["label"], "phrases": phrases, "numbers": numbers})
    return {"passed": not problems, "feedback": problems, "unsupported": unsupported}
