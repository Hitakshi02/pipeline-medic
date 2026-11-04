"""Phase 8: metrics (scoring, classifier, CIs, Pareto) and a small offline bench."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from evals import metrics

ROOT = Path(__file__).resolve().parents[1]
PYTHON = str(ROOT / "agent" / ".venv" / "bin" / "python") if (ROOT / "agent" / ".venv" / "bin" / "python").exists() else sys.executable


def row(**kw):
    base = {"group": "single", "detected": True, "fault_type_correct": True, "location_correct": True,
            "fix_success": True, "cheat_fix": False, "fix_evaluated": True, "verify_fix_passed": True,
            "hidden_passed": True, "escalated": False, "turns": 10, "input_tokens": 1000, "output_tokens": 100,
            "wall_seconds": 30.0, "cost": None}
    base.update(kw)
    return base


class MetricsTest(unittest.TestCase):
    def test_location_matching(self):
        model = {"kind": "model", "file": "models/marts/daily_revenue.sql", "line": 14}
        self.assertTrue(metrics.location_matches(model, dict(model, line=15)))
        self.assertFalse(metrics.location_matches(model, dict(model, line=20)))
        source = {"kind": "source", "table": "orders", "column": "order_ts"}
        self.assertTrue(metrics.location_matches(source, {"kind": "source", "table": "raw_orders", "column": "ORDER_TS"}))
        self.assertTrue(metrics.location_matches({"kind": "source", "table": "orders", "column": None},
                                                 {"kind": "source", "table": "orders"}))
        self.assertFalse(metrics.location_matches(source, {"kind": "model", "file": "x"}))
        self.assertTrue(metrics.location_matches({"kind": "config", "key": "source_path"},
                                                 {"kind": "config", "file": "config.json", "key": "source_path"}))

    def test_score_run_two_faults_and_control(self):
        truth = {"faults": [{"fault_type": "null_spike", "root_location": {"kind": "source", "table": "orders", "column": "customer_id"}},
                            {"fault_type": "join_fanout", "root_location": {"kind": "model", "file": "models/marts/daily_revenue.sql", "line": 14}}]}
        half = {"root_causes": [{"fault_type": "null_spike", "location": {"kind": "source", "table": "orders", "column": "customer_id"}}]}
        self.assertFalse(metrics.score_run(truth, half)["fault_type_correct"])
        both = {"root_causes": half["root_causes"] + [{"fault_type": "join_fanout",
                                                       "location": {"kind": "model", "file": "models/marts/daily_revenue.sql", "line": 13}}]}
        scored = metrics.score_run(truth, both)
        self.assertTrue(scored["fault_type_correct"] and scored["location_correct"])
        control = {"faults": []}
        self.assertTrue(metrics.score_run(control, {"root_causes": [{"fault_type": "none", "location": {"kind": "none"}}]})["location_correct"])
        self.assertFalse(metrics.score_run(control, None)["fault_type_correct"])

    def test_classifier_and_rates(self):
        rows = [row(), row(verify_fix_passed=False, hidden_passed=True, fix_success=False),
                row(verify_fix_passed=True, hidden_passed=False), row(verify_fix_passed=False, hidden_passed=False, fix_success=False),
                row(group="control", detected=True)]
        p = metrics.point_metrics(rows)
        self.assertAlmostEqual(p["verify_fix_precision"], 2 / 3)
        self.assertAlmostEqual(p["verify_fix_recall"], 2 / 3)
        self.assertEqual(p["false_alarm_rate"], 1.0)
        self.assertEqual(p["detection_recall"], 1.0)
        self.assertIsNone(p["cost_per_resolved"])
        self.assertAlmostEqual(p["time_per_resolved"], 5 * 30.0 / 3)

    def test_bootstrap_ci_brackets_the_value(self):
        rows = [row(fix_success=i % 3 != 0) for i in range(30)]
        ci = metrics.with_ci(rows)["fix_success_rate"]
        self.assertLessEqual(ci["lo"], ci["value"])
        self.assertGreaterEqual(ci["hi"], ci["value"])
        self.assertLess(ci["lo"], ci["hi"])

    def test_cost_and_pareto(self):
        self.assertAlmostEqual(metrics.cost({"model": "m", "input_tokens": 2e6, "output_tokens": 1e6},
                                            {"m": {"input": 1.0, "output": 2.0}}), 4.0)
        self.assertIsNone(metrics.cost({"model": "m", "input_tokens": 1, "output_tokens": 1}, {"m": {"input": None, "output": 1}}))
        summary = [{"config": n, "model": "m", "effort": n, "verification": True, "metrics": {
            "cost_per_run": {"value": c}, "mean_tokens": {"value": c}, "fix_success_rate": {"value": q}}}
            for n, c, q in (("cheap", 1, 0.5), ("mid", 2, 0.4), ("dear", 3, 0.9))]
        front = {p["config"]: p["frontier"] for p in metrics.pareto(summary)}
        self.assertEqual(front, {"cheap": True, "mid": False, "dear": True})


class BenchTest(unittest.TestCase):
    def test_offline_bench_writes_rows_and_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows_path = Path(tmp) / "runs.jsonl"
            for policy in ("oracle", "cheat"):
                done = subprocess.run([PYTHON, "evals/bench.py", "--split", "all", "--only", "spark-low", "--fake", policy,
                                       "--incidents", "INC-009", "INC-037", "--rows", str(rows_path)],
                                      cwd=ROOT, capture_output=True, text=True, timeout=600)
                self.assertEqual(done.returncode, 0, done.stderr[-2000:])
            rows = [json.loads(line) for line in rows_path.read_text().splitlines()]
            self.assertEqual(len(rows), 4)
            by = {(r["config"], r["incident_id"]): r for r in rows}
            oracle = by[("spark-low-fake-oracle", "INC-009")]
            self.assertTrue(oracle["fix_success"] and oracle["fault_type_correct"] and oracle["location_correct"])
            self.assertEqual(oracle["status"], "awaiting_review")
            cheat = by[("spark-low-fake-cheat", "INC-009")]
            self.assertTrue(cheat["cheat_fix"])
            self.assertTrue(cheat["escalated"])
            self.assertFalse(by[("spark-low-fake-oracle", "INC-037")]["detected"])
            results = json.loads((Path(tmp) / "results.json").read_text())
            self.assertIn("not a model evaluation", results["note"])
            self.assertEqual({s["config"] for s in results["summary"]}, {"spark-low-fake-oracle", "spark-low-fake-cheat"})
            self.assertTrue(all("lo" in v for v in results["summary"][0]["metrics"].values()))
            self.assertTrue(results["pareto"])
            self.assertFalse((Path(tmp) / "raw" / "spark-low-fake-oracle" / "INC-009" / "sandboxes").exists())


if __name__ == "__main__":
    unittest.main()
