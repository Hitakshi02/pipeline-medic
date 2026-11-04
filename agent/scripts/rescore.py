"""Re-score a saved run's submissions with the current checks, offline (no API calls).

    ./helper rescore RUN_ID [--prompts DIR]

- rejected findings: would record_finding accept them now?
- each submission: verify_rca and verify_fix (code only) re-run on the run's sandbox, using
  the submission's followups (none on older submissions).
- verify_claims is a model judgement, so it is not re-run. Instead, for each statement the
  script shows what the judge would now be given that it was not before: each cited query's
  warehouse (failed run before any fix / which replay after which patch) and turn, the
  replay results, and the deterministic number scan. --prompts DIR writes the full judge
  prompts there for inspection.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_kit import settings, tools  # noqa: E402
from verification import verify_claims, verify_fix, verify_rca  # noqa: E402


def main() -> None:
    settings.load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument("--prompts", help="write each submission's judge prompt to this folder")
    args = parser.parse_args()
    ctx = tools.context(None, settings.workspace(), args.run_id)  # no client: nothing here calls the model
    record = ctx.record
    print(f"== {args.run_id} · {record.data['incident_id']}")
    for f in record.data["rejected_findings"]:
        # the tool call's own arguments hold the cited values; recover them from the progress
        print(f"rejected finding: {f['claim'][:100]}")
    from agent_kit.progress import load
    for t in load(ctx.workspace, ctx.run_id)["turns"]:
        tool = t.get("tool") or {}
        if tool.get("name") == "record_finding" and "error" in (tool.get("result") or {}):
            a = json.loads(tool["arguments"])
            ok = all(any(record.value_in_query(v, q) for q in a["query_ids"]) for v in a["cited_values"])
            print(f"  turn {t['turn']}: cited {a['cited_values']} in {a['query_ids']} -> "
                  f"{'would be ACCEPTED now' if ok else 'still refused'}")
    replays = verify_claims.replay_results(ctx)
    for sub in record.data["submissions"]:
        rca = sub["rca"]
        old = {c["check"]: c for c in sub["checks"]}
        print(f"\n-- attempt {sub['attempt']} (was {sub['outcome']}; claims check then: "
              f"{'passed' if old.get('verify_claims', {}).get('passed') else old.get('verify_claims', {}).get('feedback')})")
        rca_check = verify_rca.run_verification(ctx, rca)
        print(f"   verify_rca now: {'passed' if rca_check['passed'] else rca_check['feedback']}")
        fix = verify_fix.run_verification(ctx, rca)
        print(f"   verify_fix now: {'passed' if fix['passed'] else fix['feedback']} "
              f"(followups: {len(rca.get('followups') or [])}, deferred: {fix.get('accepted_failures')})")
        numbers = [v for r in replays for v in (r["checks_passed"], r["checks_total"]) if v is not None]
        items = [s for s in verify_claims.statements(ctx, rca) if s["evidence"]]
        for s in items:
            where = "; ".join(f"{e['id']}: {e['warehouse']}, turn {e['turn']}" for e in s["evidence"])
            missing = verify_claims.missing_numbers(s["statement"], s["evidence"], numbers)
            print(f"   {s['label']}: \"{s['statement'][:110]}\"")
            print(f"      judge now sees: {where}")
            if missing:
                print(f"      numbers not in any cited result or replay result: {missing}")
        if args.prompts:
            shown = [{k: v for k, v in s.items() if k != "label"} for s in items]
            for s in shown:
                s["evidence"] = [{k: v for k, v in e.items() if k != "_all_rows"} for e in s["evidence"]]
            out = Path(args.prompts) / f"{args.run_id}-attempt{sub['attempt']}.txt"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(verify_claims.prompt(shown, replays))
    print(f"\nreplay results the judge now sees: {json.dumps(replays)}")


if __name__ == "__main__":
    main()
