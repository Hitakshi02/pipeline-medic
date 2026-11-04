"""Phase 7: the console's API over a real (FakeModel) run, and the page itself."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from agent_kit.model import FakeModel  # noqa: E402
from medic_agent.loop import start_run  # noqa: E402

BACKFILL = ("    COALESCE(customer_id, (SELECT c.customer_id FROM raw_customers AS c "
            "WHERE lower(c.email) = lower(raw_orders.customer_email))) AS customer_id,\n")


def judge(prompt):
    if "Hypothesis kinds" in prompt:
        return {"hypotheses": [{"id": i, "kinds": ["source_completeness"]} for i in re.findall(r'"id": "([a-z_]+)"', prompt)]}
    return {"unsupported": []}


def script():
    def cite(kw):
        out = json.loads(next(m for m in reversed(kw["input"]) if m.get("type") == "function_call_output")["output"])
        return {"tool": "record_finding", "args": {"hypothesis": "nulls", "claim": "null ids", "verdict": "supports",
                                                   "query_ids": [out["query_id"]], "cited_values": [str(out["rows"][0][0])]}}
    return [
        {"tool": "set_investigation_plan", "args": {"hypotheses": [{"id": "nulls", "hypothesis": "h", "how_to_test": "t"}]}},
        {"tool": "query_data", "args": {"sql": "SELECT count(*) FROM stg_orders WHERE customer_id IS NULL"}},
        cite,
        {"tool": "propose_fix", "args": {"path": "models/staging/stg_orders.sql", "reason": "r",
                                         "old_text": "    customer_id,\n", "new_text": BACKFILL}},
        {"tool": "run_replay"},
        {"tool": "submit_rca", "args": {"summary": "fixed", "fix": "f", "claims": [{"claim": "c", "query_ids": ["Q1"]}],
                                        "root_causes": [{"fault_type": "null_spike", "query_ids": ["Q1"], "explanation": "e",
                                                         "location": {"kind": "source", "table": "orders", "column": "customer_id"}}]}},
    ]


class ConsoleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["WORKSPACE_DIR"] = cls.tmp.name
        cls.result = start_run(workspace=Path(cls.tmp.name), client=FakeModel(script=script(), judge=judge),
                               incident_dir=ROOT / "incidents" / "INC-009", verification=True, quiet=True)
        spec = importlib.util.spec_from_file_location("console_ui", ROOT / "ui" / "ui.py")
        cls.ui = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.ui)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), cls.ui.make_handler(cls.ui.Console()))
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        os.environ.pop("WORKSPACE_DIR", None)
        cls.tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path) as r:
            return r.read()

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    def test_page_and_reads(self):
        page = self.get("/").decode()
        self.assertIn("<title>Pipeline Medic</title>", page)
        self.assertEqual(self.result["status"], "awaiting_review")
        info = json.loads(self.get("/api/info"))
        self.assertIn("model", info)
        self.assertNotIn("MODEL_API_KEY", json.dumps(info))
        self.assertEqual(len(json.loads(self.get("/api/incidents"))), 46)
        runs = json.loads(self.get("/api/runs"))
        self.assertEqual(runs[0]["status"], "awaiting_review")
        run = json.loads(self.get("/api/run?id=" + self.result["run_id"]))
        self.assertEqual([t["tool"] for t in run["timeline"]][-1], "submit_rca")
        self.assertIn("COALESCE", run["diff"])
        fix = run["record"]["submissions"][-1]["checks"][1]
        self.assertTrue(all(h["passed"] for h in fix["hidden"]))
        self.assertEqual(run["latest"]["status"], "success")
        self.assertIn("results", json.loads(self.get("/api/results")))

    def test_zz_review_and_control(self):
        self.assertEqual(self.post("/api/control", {"step": True, "grant": 2})["grants"], 2)
        self.post("/api/control", {"step": False})
        self.assertIn("error", self.post("/api/review", {"run_id": self.result["run_id"], "decision": "reject", "notes": ""}))
        out = self.post("/api/review", {"run_id": self.result["run_id"], "decision": "approve", "notes": "ok"})
        self.assertIn("approved", out)
        run = json.loads(self.get("/api/run?id=" + self.result["run_id"]))
        self.assertEqual(run["state"]["status"], "done")
        self.assertIn("Incident report", run["report"])
        self.assertIn("error", self.post("/api/start", {"incident": "nope"}))


if __name__ == "__main__":
    unittest.main()
