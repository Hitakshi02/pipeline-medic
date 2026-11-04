"""Phase 3: the kit. Record, progress, sandbox and every tool, offline."""

from __future__ import annotations

import json
import unittest

from agent_kit import progress, tools
from agent_kit.incident_record import IncidentRecord
from agent_kit.model import FakeModel

from .support import Run

PLAN = [{"id": "upstream_nulls", "hypothesis": "customer_id arrives null", "how_to_test": "null rate by night"},
        {"id": "model_change", "hypothesis": "a model edit", "how_to_test": "read the models"}]
BACKFILL = ("    COALESCE(customer_id, (SELECT c.customer_id FROM raw_customers AS c "
            "WHERE lower(c.email) = lower(raw_orders.customer_email))) AS customer_id,\n")


class DefinitionsTest(unittest.TestCase):
    def test_twelve_tools_with_strict_schemas(self):
        defs = tools.definitions()
        names = [d["name"] for d in defs]
        self.assertEqual(len(names), 12)
        self.assertEqual(len(set(names)), 12)
        for d in defs:
            self.assertEqual(d["type"], "function")
            self.assertFalse(d["parameters"]["additionalProperties"], d["name"])
            json.dumps(d)


class FakeModelTest(unittest.TestCase):
    def test_script_and_judge(self):
        fake = FakeModel(script=[{"tool": "read_alert"}, "hello"], judge=lambda p: {"ok": True})
        r = fake.responses.create(model="m", input=[], tools=[{}])
        self.assertEqual(r.output[0]["name"], "read_alert")
        self.assertEqual(fake.responses.create(model="m", input=[], tools=[{}]).output_text, "hello")
        self.assertEqual(json.loads(fake.responses.create(model="m", input="judge").output_text), {"ok": True})
        self.assertEqual(len(fake.calls), 3)


class NullSpikeKitTest(unittest.TestCase):
    """INC-009: null_spike on orders.customer_id."""

    def setUp(self):
        self.run = Run("INC-009")

    def tearDown(self):
        self.run.close()

    def test_sandbox_reproduces_the_failure(self):
        self.assertEqual(self.run.failed["status"], "failed")
        self.assertTrue(self.run.sandbox.db("failed").is_file())
        self.assertEqual(self.run.sandbox.changed_files(), [])

    def test_read_tools(self):
        alert = self.run.call("read_alert")
        self.assertIn("orders_customer_id_nulls", alert["alert"])
        logs = self.run.call("read_logs")
        self.assertIn("run_2026-03-07.log", logs["logs"])
        self.assertIn("failed", logs["logs"])
        page = self.run.call("read_logs", log="run_2026-03-07.log")
        self.assertTrue(page["lines"])
        self.assertIn("raw_orders", self.run.call("inspect_schema")["tables"])
        schema = self.run.call("inspect_schema", table="raw_orders")
        self.assertIn("2026-03-07", schema["batch_headers"])
        profile = self.run.call("profile_table", table="stg_orders")
        self.assertGreater(profile["columns"]["customer_id"]["null_rate_night"], 0.3)
        self.assertEqual(profile["columns"]["customer_id"]["null_rate_before"], 0)
        files = self.run.call("read_file")["files"]
        self.assertIn("models/staging/stg_orders.sql", files)
        self.assertIn("   1  ", self.run.call("read_file", path="config.json")["content"])

    def test_query_data_is_read_only_and_logged(self):
        ok = self.run.call("query_data", sql="SELECT count(*) AS n FROM stg_orders WHERE customer_id IS NULL "
                                             "AND _batch_night = '2026-03-07'")
        self.assertEqual(ok["query_id"], "Q1")
        self.assertGreater(ok["rows"][0][0], 0)
        self.assertIn("query_id", self.run.call("query_data", sql="SELECT replace('a$', '$', '') AS x"))
        for bad in ("DELETE FROM stg_orders", "CREATE TABLE x AS SELECT 1", "SELECT 1; SELECT 2",
                    "SELECT * FROM read_csv('../../ground_truth/INC-009.json')", "ATTACH 'x.db'",
                    "COPY stg_orders TO 'x.csv'"):
            result = self.run.call("query_data", sql=bad)
            self.assertIn("refused", result.get("error", ""), bad)
        failing = self.run.call("query_data", sql="SELECT nope FROM stg_orders")
        self.assertIn("error", failing)
        record = IncidentRecord(self.run.workspace, self.run.run_id)
        self.assertEqual([q["id"] for q in record.data["queries"]], ["Q1", "Q2", "Q3"])
        self.assertTrue(record.data["queries"][2]["error"])
        self.assertEqual(self.run.call("query_data", sql="SELECT * FROM stg_orders", limit=5)["row_count"], 5)

    def test_plan_gates_findings_fixes_and_submit(self):
        q = self.run.call("query_data", sql="SELECT count(*) AS nulls FROM stg_orders WHERE customer_id IS NULL")
        nulls = str(q["rows"][0][0])
        self.assertIn("plan", self.run.call("record_finding", hypothesis="x", claim="c", verdict="supports",
                                            query_ids=["Q1"], cited_values=[nulls])["error"])
        self.assertIn("plan", self.run.call("propose_fix", path="config.json", reason="r", content="{}")["error"])
        self.assertIn("plan", self.run.call("submit_rca", summary="s", root_causes=[], claims=[], fix="f", followups=[])["error"])
        self.assertIn("plan", self.run.call("set_investigation_plan", hypotheses=PLAN))
        found = self.run.call("record_finding", hypothesis="upstream_nulls", claim="many nulls", verdict="supports",
                              query_ids=["Q1"], cited_values=[nulls])
        self.assertEqual(found["recorded"], "F1")
        self.assertEqual(found["plan_status"]["upstream_nulls"], "supported")
        for bad_q, bad_v, why in ((["Q9"], [nulls], "not a logged query"), (["Q1"], ["123456"], "does not appear")):
            result = self.run.call("record_finding", hypothesis="upstream_nulls", claim="c", verdict="supports",
                                   query_ids=bad_q, cited_values=bad_v)
            self.assertIn(why, result["error"])
        self.assertIn("unknown hypothesis", self.run.call(
            "record_finding", hypothesis="nope", claim="c", verdict="supports", query_ids=["Q1"],
            cited_values=[nulls])["error"])

    def test_fix_guards_replay_and_submit(self):
        self.run.call("set_investigation_plan", hypotheses=PLAN)
        for path in ("contracts/contracts.yaml", "tests/assert_no_future_dates.sql", "run_pipeline.py",
                     "../config.json", "contracts/../contracts/checker.py"):
            result = self.run.call("propose_fix", path=path, reason="r", content="x")
            self.assertIn("error", result, path)
        fixed = self.run.call("propose_fix", path="models/staging/stg_orders.sql", reason="backfill from email",
                              old_text="    customer_id,\n", new_text=BACKFILL)
        self.assertEqual(fixed["patch"], "P1")
        self.assertIn("+    COALESCE(customer_id", fixed["diff"])
        replay = self.run.call("run_replay")
        self.assertEqual(replay["run_status"], "success", replay)
        self.assertNotIn("status", replay)
        self.assertEqual(replay["changed_files"], ["models/staging/stg_orders.sql"])
        q = self.run.call("query_data", sql="SELECT count(*) FROM stg_orders WHERE customer_id IS NULL",
                          target="replay")
        self.assertEqual(q["rows"][0][0], 0)
        done = self.run.call("submit_rca", summary="s", fix="f", followups=[], claims=[{"claim": "c", "query_ids": ["Q1"]}],
                             root_causes=[{"fault_type": "null_spike", "explanation": "e", "query_ids": ["Q1"],
                                           "location": {"kind": "source", "table": "orders", "column": "customer_id"}}])
        self.assertEqual(done["status"], "submitted")
        record = IncidentRecord(self.run.workspace, self.run.run_id)
        self.assertEqual(record.data["rca"]["status"], "unverified")
        self.assertIn("stg_orders.sql", record.data["rca"]["diff"])

    def test_request_info_stops_the_run(self):
        result = self.run.call("request_info", question="Did anything change upstream?")
        self.assertEqual(result["status"], "needs_input")
        self.assertIn("still open", self.run.call("request_info", question="again?")["error"])

    def test_bad_calls(self):
        self.assertIn("unknown tool", tools.run(self.run.client, self.run.workspace, self.run.run_id,
                                                {"name": "rm_rf", "arguments": "{}"})["error"])
        self.assertIn("JSON", tools.run(self.run.client, self.run.workspace, self.run.run_id,
                                        {"name": "read_alert", "arguments": "{nope"})["error"])


class ProgressTest(unittest.TestCase):
    def test_save_load_resume(self):
        run = Run("INC-037")
        try:
            ws, rid = run.workspace, run.run_id
            progress.save(ws, rid, [{"role": "user", "content": "hi"}], quiet=True)
            state = progress.load(ws, rid)
            self.assertEqual(state["status"], "running")
            self.assertEqual(state["incident_id"], "INC-037")
            progress.set_status(ws, rid, "paused")
            self.assertIn("more turns", progress.resume_message(ws, rid)["message"])
            progress.set_status(ws, rid, "submitted")
            self.assertIsNone(progress.resume_message(ws, rid)["message"])
            self.assertEqual(progress.list_runs(ws), [rid])
        finally:
            run.close()

    def test_step_mode_grants(self):
        run = Run("INC-037")
        try:
            progress.write_control(run.workspace, {"step": True, "grants": 1})
            progress.wait_for_step(run.workspace)  # uses the grant
            self.assertEqual(progress.read_control(run.workspace)["grants"], 0)
            progress.write_control(run.workspace, {"step": False, "grants": 0})
            progress.wait_for_step(run.workspace)  # returns at once
        finally:
            run.close()


if __name__ == "__main__":
    unittest.main()
