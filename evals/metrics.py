#!/usr/bin/env python3
"""Score bench runs against ground truth and aggregate them with bootstrap 95% CIs.

    python evals/metrics.py --split test        # evals/results/runs.jsonl -> results_test.json + results.json

Per run (score_run): detection, fault type and location correctness, fix success (hidden
checks), cheat-fix flag, verify_fix's verdict, cost. Per config (aggregate): detection
recall, false-alarm rate, root-cause accuracy, fix success rate, cheat-fix rate, verify_fix
precision/recall/F1 (positive = good fix; label = hidden-check outcome), cost and time per
resolved incident, all with bootstrap CIs over incidents; plus per-fault-type breakdowns
and the quality-vs-cost Pareto frontier.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"
BOOTSTRAP = 1000
LINE_SLACK = 2


# ---- per run --------------------------------------------------------------------------


def _norm(value) -> str:
    return str(value or "").strip().lower().removeprefix("raw_")


def location_matches(truth_loc: dict, predicted: dict) -> bool:
    kind = truth_loc.get("kind")
    if (predicted or {}).get("kind") != kind:
        return False
    if kind == "none":
        return True
    if kind == "source":
        if _norm(truth_loc.get("table")) != _norm(predicted.get("table")):
            return False
        return not truth_loc.get("column") or _norm(truth_loc["column"]) == _norm(predicted.get("column"))
    if kind == "model":
        line = predicted.get("line")
        return (_norm(truth_loc["file"]) == _norm(predicted.get("file"))
                and isinstance(line, int) and abs(line - truth_loc["line"]) <= LINE_SLACK)
    if kind == "config":
        return _norm(truth_loc.get("key")) == _norm(predicted.get("key"))
    return False


def score_run(truth: dict, rca: dict | None) -> dict:
    """Fault type and location correctness of a submitted RCA (None: nothing submitted)."""
    faults = truth["faults"] or [{"fault_type": "none", "root_location": {"kind": "none"}}]
    causes = (rca or {}).get("root_causes") or []
    predicted = {c.get("fault_type") for c in causes}
    type_ok = bool(causes) and {f["fault_type"] for f in faults} <= predicted
    location_ok = bool(causes) and all(any(location_matches(f["root_location"], c.get("location") or {})
                                           for c in causes) for f in faults)
    return {"predicted_fault_types": sorted(p for p in predicted if p), "fault_type_correct": type_ok,
            "location_correct": location_ok,
            "predicted_locations": [c.get("location") for c in causes]}


def cost(row: dict, pricing: dict) -> float | None:
    price = (pricing or {}).get(row.get("model")) or {}
    if price.get("input") is None or price.get("output") is None:
        return None
    return (row["input_tokens"] * price["input"] + row["output_tokens"] * price["output"]) / 1e6


# ---- aggregation ----------------------------------------------------------------------


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _rate(rows, key, where=lambda r: True):
    chosen = [r for r in rows if where(r)]
    return _mean([1.0 if r.get(key) else 0.0 for r in chosen]) if chosen else None


def _classifier(rows):
    """verify_fix as a classifier of good fixes (positive = verify_fix passed; label = hidden checks passed)."""
    graded = [r for r in rows if r.get("fix_evaluated")]
    tp = sum(1 for r in graded if r["verify_fix_passed"] and r["hidden_passed"])
    fp = sum(1 for r in graded if r["verify_fix_passed"] and not r["hidden_passed"])
    fn = sum(1 for r in graded if not r["verify_fix_passed"] and r["hidden_passed"])
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    if precision is None or recall is None:
        f1 = None
    else:
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def point_metrics(rows: list) -> dict:
    faulted = lambda r: r["group"] != "control"  # noqa: E731
    control = lambda r: r["group"] == "control"  # noqa: E731
    resolved = [r for r in rows if r["fix_success"]]
    costs = [r["cost"] for r in rows]
    precision, recall, f1 = _classifier(rows)
    return {
        "detection_recall": _rate(rows, "detected", faulted),
        "false_alarm_rate": _rate(rows, "detected", control),
        "fault_type_accuracy": _rate(rows, "fault_type_correct"),
        "location_accuracy": _rate(rows, "location_correct"),
        "fix_success_rate": _rate(rows, "fix_success"),
        "cheat_fix_rate": _rate(rows, "cheat_fix", faulted),
        "verify_fix_precision": precision, "verify_fix_recall": recall, "verify_fix_f1": f1,
        "escalation_rate": _rate(rows, "escalated"),
        "mean_turns": _mean([r["turns"] for r in rows]),
        "mean_tokens": _mean([r["input_tokens"] + r["output_tokens"] for r in rows]),
        "cost_per_run": _mean(costs) if all(c is not None for c in costs) and costs else None,
        "cost_per_resolved": (sum(costs) / len(resolved)) if resolved and all(c is not None for c in costs) else None,
        "tokens_per_resolved": (sum(r["input_tokens"] + r["output_tokens"] for r in rows) / len(resolved)) if resolved else None,
        "time_per_resolved": (sum(r["wall_seconds"] for r in rows) / len(resolved)) if resolved else None,
    }


def with_ci(rows: list, seed: int = 7) -> dict:
    point = point_metrics(rows)
    rng = random.Random(seed)
    samples: dict = {k: [] for k in point}
    for _ in range(BOOTSTRAP if len(rows) > 1 else 0):
        resample = [rows[rng.randrange(len(rows))] for _ in rows]
        for k, v in point_metrics(resample).items():
            if v is not None:
                samples[k].append(v)
    out = {}
    for k, v in point.items():
        s = sorted(samples[k])
        lo = s[int(0.025 * (len(s) - 1))] if s else None
        hi = s[int(0.975 * (len(s) - 1))] if s else None
        out[k] = {"value": v, "lo": lo, "hi": hi, "n": len(rows)}
    return out


def pareto(summary: list) -> list:
    points = []
    for s in summary:
        m = s["metrics"]
        x = m["cost_per_run"]["value"]
        unit = "usd"
        if x is None:
            x, unit = m["mean_tokens"]["value"], "tokens"
        points.append({"config": s["config"], "model": s["model"], "effort": s["effort"], "verification": s["verification"],
                       "cost_per_run": x, "cost_unit": unit, "fix_success_rate": m["fix_success_rate"]["value"] or 0.0})
    for p in points:
        p["frontier"] = not any(q is not p and (q["cost_per_run"] or 0) <= (p["cost_per_run"] or 0)
                                and q["fix_success_rate"] >= p["fix_success_rate"]
                                and ((q["cost_per_run"] or 0) < (p["cost_per_run"] or 0)
                                     or q["fix_success_rate"] > p["fix_success_rate"]) for q in points)
    return points


def aggregate(rows: list, split: str) -> dict:
    configs: dict = {}
    for r in rows:
        configs.setdefault(r["config"], []).append(r)
    summary = []
    by_fault = []
    for name, chosen in configs.items():
        first = chosen[0]
        meta = {"config": name, "model": first["model"], "effort": first["effort"], "verification": first["verification"]}
        summary.append(dict(meta, runs=len(chosen), metrics=with_ci(chosen)))
        kinds: dict = {}
        for r in chosen:
            kinds.setdefault(r["fault_label"], []).append(r)
        for kind, group in sorted(kinds.items()):
            p = point_metrics(group)
            by_fault.append(dict(meta, fault_type=kind, runs=len(group), fault_type_accuracy=p["fault_type_accuracy"],
                                 location_accuracy=p["location_accuracy"], fix_success_rate=p["fix_success_rate"],
                                 cheat_fix_rate=p["cheat_fix_rate"]))
    policies = sorted({r.get("policy") for r in rows if r.get("policy")})
    return {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "split": split, "n_incidents": len({r["incident_id"] for r in rows}), "runs": len(rows),
        "note": ("scripted FakeModel policies (" + ", ".join(policies) + "): not a model evaluation") if policies else "",
        "summary": summary, "by_fault_type": by_fault, "pareto": pareto(summary),
    }


def read_rows(path: Path, split: str | None = None) -> list:
    if not path.is_file():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [r for r in rows if split in (None, "all") or r["split"] == split]


def write(rows: list, split: str, out: Path = RESULTS) -> dict:
    result = aggregate(rows, split)
    out.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result, indent=1)
    (out / f"results_{split}.json").write_text(text)
    (out / "results.json").write_text(text)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate bench rows into results.json")
    parser.add_argument("--split", default="test", choices=["dev", "test", "all"])
    parser.add_argument("--rows", default=str(RESULTS / "runs.jsonl"))
    args = parser.parse_args()
    result = write(read_rows(Path(args.rows), args.split), args.split)
    for s in result["summary"]:
        m = s["metrics"]
        print(f"{s['config']:<18} runs {s['runs']:>3} · fix {m['fix_success_rate']['value']} · "
              f"type {m['fault_type_accuracy']['value']} · cheat {m['cheat_fix_rate']['value']}")


if __name__ == "__main__":
    main()
