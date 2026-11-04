"""Re-score a saved run's submissions with the current checks, offline (no API calls).

    ./helper rescore RUN_ID [--workspace DIR] [--prompts DIR] [--json]

- rejected findings: would record_finding accept them now?
- each submission: verify_rca and verify_fix (code only), when the run's sandbox still exists.
  A bench run's sandbox is deleted after scoring; then the failed-run snapshot is rebuilt from
  the incident (deterministic) so the claims evidence is complete, and verify_fix is skipped.
- verify_claims is a model judgement and is not re-run. For every phrase a past judge refused,
  the script says what changed: the phrase was a fault-type label (no longer judged), or it is
  now covered by evidence of a kind the judge did not have (failed_run_checks, original_code,
  patch_diff, replay_results), or it still has no evidence. --prompts DIR writes the full new
  judge prompts.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_kit import settings, tools  # noqa: E402
from agent_kit.progress import load  # noqa: E402
from agent_kit.sandbox import Sandbox  # noqa: E402
from verification import verify_claims, verify_fix, verify_rca  # noqa: E402

STOP = {"the", "and", "with", "not", "was", "were", "has", "had", "its", "for", "from", "that", "this", "only",
        "into", "than", "then", "but", "are", "all", "any", "under", "instead", "making", "showing", "shows"}
FAULT_TYPES = {"schema_drift", "type_change", "null_spike", "late_partial", "duplicate_keys", "join_fanout",
               "timezone_shift", "code_regression", "config_error", "none", "other", "fault_type"}


def terms(text: str) -> list:
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}|\d+(?:\.\d+)?(?:/\d+)?", text)
    return [w.lower() for w in words if w.lower() not in STOP]


def classify(phrase: str, shared: dict, statement_evidence: list) -> str:
    bare = phrase.strip().strip('"').strip()
    if re.fullmatch(r"\(?\s*[a-z_]+\s*\)?", bare) and bare.strip("() ").lower() in FAULT_TYPES:
        return "fault-type label: no longer judged"
    if re.search(r"fault_type\s+\w+", bare, re.I):
        return "fault-type label: no longer judged"
    words = terms(bare)
    if not words:
        return "no evidence-bearing terms"
    covered = []
    for kind in ("failed_run_checks", "original_code", "patch_diff", "replay_results"):
        text = json.dumps(shared.get(kind) or [], default=str).lower().replace("\\n", " ")
        hits = sum(1 for w in words if w in text)
        if hits / len(words) >= 0.5:
            covered.append(kind)
    if covered:
        return "now covered by " + " + ".join(covered)
    query_text = json.dumps([{k: e[k] for k in ("sql", "columns", "_all_rows")} for e in statement_evidence],
                            default=str).lower()
    if sum(1 for w in words if w in query_text) / len(words) >= 0.5:
        return "terms were in its cited queries (judged unsupported on meaning)"
    return "still unsupported"


def main() -> None:
    settings.load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument("--workspace", help="default: agent/workspace; a bench run: evals/results/raw/<config>/<incident>")
    parser.add_argument("--prompts", help="write each submission's new judge prompt to this folder")
    parser.add_argument("--json", action="store_true", help="print one JSON summary at the end")
    args = parser.parse_args()
    workspace = Path(args.workspace).resolve() if args.workspace else settings.workspace()
    state = load(workspace, args.run_id)
    box = Sandbox(workspace, args.run_id)
    rebuilt = False
    if not box.exists():
        box.create(Path(state["incident_dir"]))  # failed-run snapshot only; the agent's patches are in the record
        rebuilt = True
    ctx = tools.context(None, workspace, args.run_id)  # no client: nothing here calls the model
    record = ctx.record
    print(f"== {args.run_id} · {state['incident_id']}" + (" (sandbox rebuilt from the incident)" if rebuilt else ""))
    for t in state["turns"]:
        tool = t.get("tool") or {}
        if tool.get("name") == "record_finding" and "error" in (tool.get("result") or {}):
            a = json.loads(tool["arguments"])
            ok = bool(a["query_ids"]) and all(any(record.value_in_query(v, q) for q in a["query_ids"])
                                              for v in a["cited_values"])
            print(f"  rejected finding, turn {t['turn']}: cited {a['cited_values'][:4]} in {a['query_ids']} -> "
                  f"{'would be ACCEPTED now' if ok else 'still refused'}")
    summary = []
    for sub in record.data["submissions"]:
        rca = sub["rca"]
        old = {c["check"]: c for c in sub["checks"]}
        claims_then = old.get("verify_claims", {})
        print(f"\n-- attempt {sub['attempt']} (was {sub['outcome']})")
        if not rebuilt:
            rca_check = verify_rca.run_verification(ctx, rca)
            fix = verify_fix.run_verification(ctx, rca)
            print(f"   verify_rca now: {'passed' if rca_check['passed'] else rca_check['feedback']}")
            print(f"   verify_fix now: {'passed' if fix['passed'] else fix['feedback']}")
        shared = verify_claims.shared_evidence(ctx, rca)
        items = {s["label"]: s for s in verify_claims.statements(ctx, rca)}
        if claims_then and not claims_then.get("passed"):
            for entry in claims_then.get("unsupported") or []:
                item = items.get(entry["statement"], {"evidence": []})
                for phrase in entry.get("phrases") or []:
                    verdict = classify(phrase, shared, item["evidence"])
                    summary.append({"attempt": sub["attempt"], "statement": entry["statement"], "phrase": phrase,
                                    "change": verdict})
                    print(f"   {entry['statement']}: \"{phrase}\" -> {verdict}")
            if not claims_then.get("unsupported"):
                for line in claims_then.get("feedback") or []:
                    print(f"   (old-format feedback) {line[:160]}")
        if args.prompts:
            out = Path(args.prompts) / f"{args.run_id}-attempt{sub['attempt']}.txt"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(verify_claims.prompt(verify_claims.shown_statements(
                [s for s in items.values() if s["evidence"]]), shared))
    if args.json:
        print(json.dumps({"run_id": args.run_id, "incident_id": state["incident_id"], "phrases": summary}))


if __name__ == "__main__":
    main()
