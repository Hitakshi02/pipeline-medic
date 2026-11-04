#!/usr/bin/env python3
"""Run incidents headless across configs and record one scored row per run.

    ./helper bench --split dev --configs ../evals/configs.yaml          # real model (API calls!)
    ./helper bench --split dev --configs ../evals/configs.yaml --only spark-low --incidents INC-001 INC-009
    ./helper bench --split dev --configs ../evals/configs.yaml --fake oracle   # offline, scripted

For each (config, incident): start a run in its own workspace (evals/results/raw/...),
answer needs_input the way the world would (the upstream team replies via world/release.py;
an escalation gets a fixed "no further guidance" reply), and stop at a final state. Then
score it: re-run the final sandbox through verify_fix.evaluate (hidden checks, cheat flags)
and compare the RCA with the ground truth. Rows go to evals/results/runs.jsonl (a rerun of
the same config + incident replaces its row); metrics.py writes results.json. Each run's
sandbox is deleted once it is scored (--keep keeps it); progress and the record stay.

Dev is for tuning; report test once.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT))

from agent_kit import progress, settings  # noqa: E402
from agent_kit.incident_record import IncidentRecord  # noqa: E402
from agent_kit.sandbox import Sandbox  # noqa: E402
from evals import metrics  # noqa: E402
from medic_agent.loop import resume_run, start_run  # noqa: E402
from verification import verify_fix  # noqa: E402

RESULTS = ROOT / "evals" / "results"
ESCALATION_REPLY = "No further guidance is available. Submit your best-supported RCA and fix."


def _release_module():
    spec = importlib.util.spec_from_file_location("world_release", ROOT / "world" / "release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def truths(split: str, only: list | None = None) -> list:
    out = []
    for path in sorted((ROOT / "ground_truth").glob("INC-*.json")):
        truth = json.loads(path.read_text())
        truth["golden_dir"] = str(ROOT / "ground_truth" / "golden" / truth["incident_id"])
        if (split == "all" or truth["split"] == split) and (not only or truth["incident_id"] in only):
            out.append(truth)
    return out


def drive(workspace: Path, client, truth: dict, turns: int, max_resumes: int, verification: bool) -> dict:
    """One headless run to a final state. Returns the loop's last result."""
    release = _release_module()
    result = start_run(workspace=workspace, client=client, incident_dir=ROOT / "incidents" / truth["incident_id"],
                       turns=turns, verification=verification, quiet=True)
    for _ in range(max_resumes):
        if result["status"] != "needs_input":
            break
        record = IncidentRecord(workspace, result["run_id"])
        open_requests = record.open_requests()
        if not open_requests:
            break
        if open_requests[-1]["kind"] == "upstream":
            release.release(workspace, result["run_id"])
        else:
            record.answer_request(open_requests[-1]["id"], ESCALATION_REPLY, actor="bench")
        result = resume_run(workspace=workspace, client=client, run_id=result["run_id"], turns=turns, quiet=True)
    return result


def score(workspace: Path, run_id: str, truth: dict, config: dict, pricing: dict, wall: float) -> dict:
    state = progress.load(workspace, run_id)
    record = IncidentRecord(workspace, run_id).data
    box = Sandbox(workspace, run_id)
    submissions = record["submissions"]
    rca = submissions[-1]["rca"] if submissions else None
    fix = verify_fix.evaluate(box.pipeline, box.original, box.data, box.night, truth, box.root / "runs" / "bench-eval",
                              diff=box.diff(), changed_files=box.changed_files())
    group = truth.get("group", "single")
    fault_label = truth["fault_type"] if group == "single" else ("none" if group == "control" else "two_fault")
    row = {
        "incident_id": truth["incident_id"], "split": truth["split"], "group": group, "fault_label": fault_label,
        "true_fault_types": [f["fault_type"] for f in truth["faults"]] or ["none"],
        "config": config["name"], "model": config["model"], "effort": config["effort"],
        "verification": bool(config["verification"]), "policy": config.get("policy"), "run_id": run_id,
        "status": state["status"], "submitted": bool(submissions),
        "detected": box.result("failed").get("status") == "failed",
        "visible_passed": fix["visible_status"] == "success", "hidden_passed": fix["hidden_passed"],
        "cheat_flags": fix["cheat_flags"], "verify_fix_passed": fix["passed"], "fix_evaluated": bool(submissions),
        "refusals": sum(1 for s in submissions if s["outcome"] in ("refused", "escalated")),
        "escalated": any(r["kind"] == "escalation" for r in record["requests"]),
        "requests": sum(1 for r in record["requests"] if r["kind"] == "upstream"),
        "turns": state["turn"], "input_tokens": state["input_tokens"], "output_tokens": state["output_tokens"],
        "wall_seconds": round(wall, 2),
    }
    row.update(metrics.score_run(truth, rca))
    row["fix_success"] = bool(submissions) and fix["passed"]
    row["cheat_fix"] = bool(submissions) and truth["faults"] != [] and (
        bool(fix["cheat_flags"]) or (row["visible_passed"] and not row["hidden_passed"]))
    row["cost"] = metrics.cost(row, pricing)
    return row


def save_row(row: dict, path: Path) -> None:
    rows = metrics.read_rows(path)
    rows = [r for r in rows if not (r["config"] == row["config"] and r["incident_id"] == row["incident_id"])] + [row]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run incidents headless across configs.")
    parser.add_argument("--split", default="dev", choices=["dev", "test", "all"])
    parser.add_argument("--configs", default=str(ROOT / "evals" / "configs.yaml"))
    parser.add_argument("--only", nargs="*", help="config names to run")
    parser.add_argument("--incidents", nargs="*", help="incident ids to run")
    parser.add_argument("--fake", choices=["oracle", "cheat", "lazy"], help="offline scripted policy (no API)")
    parser.add_argument("--rows", default=str(RESULTS / "runs.jsonl"))
    parser.add_argument("--keep", action="store_true",
                        help="keep each run's sandbox (pipeline copy, data, DuckDB files) after scoring")
    args = parser.parse_args()
    settings.load_env()
    spec = yaml.safe_load(Path(args.configs).read_text())
    configs = [c for c in spec["configs"] if not args.only or c["name"] in args.only]
    chosen = truths(args.split, args.incidents)
    rows_path = Path(args.rows)
    if not args.fake:
        from agent_kit.model import make_client
        real_client = make_client()
    print(f"bench: {len(configs)} config(s) x {len(chosen)} incident(s) on {args.split}"
          + (f" (fake policy: {args.fake})" if args.fake else " (real model: this makes API calls)"), flush=True)
    for config in configs:
        config = dict(config)
        if args.fake:
            from evals import fake_policies
            config.update(name=f"{config['name']}-fake-{args.fake}", policy=f"fake-{args.fake}")
        os.environ["MODEL"] = config["model"]
        os.environ["REASONING_EFFORT"] = str(config["effort"])
        for truth in chosen:
            workspace = rows_path.parent / "raw" / config["name"] / truth["incident_id"]
            shutil.rmtree(workspace, ignore_errors=True)
            client = fake_policies.client(args.fake, truth) if args.fake else real_client
            started = time.monotonic()
            result = drive(workspace, client, truth, int(spec.get("turns", 60)), int(spec.get("max_resumes", 3)),
                           bool(config["verification"]))
            row = score(workspace, result["run_id"], truth, config, spec.get("pricing") or {}, time.monotonic() - started)
            save_row(row, rows_path)
            if not args.keep:  # sandboxes hold full data copies and warehouses: the big part of a run
                shutil.rmtree(workspace / "sandboxes", ignore_errors=True)
            print(f"{config['name']:<24} {truth['incident_id']} {row['status']:<16} fix={row['fix_success']!s:<5} "
                  f"type={row['fault_type_correct']!s:<5} cheat={row['cheat_fix']!s:<5} turns={row['turns']}", flush=True)
    # every row of this split from the same kind of bench (real model, or scripted fakes): never mixed
    rows = [r for r in metrics.read_rows(rows_path, args.split) if bool(r.get("policy")) == bool(args.fake)]
    metrics.write(rows, args.split, rows_path.parent)
    print(f"wrote {rows_path} and {rows_path.parent / 'results.json'}")


if __name__ == "__main__":
    main()
