"""Claims judged with query context and replay results; cited names found inside column
names; follow-ups for source-level contracts that still fail."""

from __future__ import annotations

import json
import re
import unittest

from agent_kit import report, progress
from agent_kit.incident_record import IncidentRecord, column_mentions
from agent_kit.model import FakeModel
from verification import verify_claims, verify_fix
from verification.ground_truth import load

from .support import Run

BACKFILL = ("    COALESCE(customer_id, (SELECT c.customer_id FROM raw_customers AS c "
            "WHERE lower(c.email) = lower(raw_orders.customer_email))) AS customer_id,\n")
PLAN = [{"id": "nulls", "hypothesis": "ids missing", "how_to_test": "null rate"}]
NULLS = "SELECT count(*) AS null_customer_id FROM stg_orders WHERE customer_id IS NULL"


class Judge:
    """Plan covered; records every claims prompt; supports everything."""

    def __init__(self):
        self.prompts = []

    def __call__(self, prompt):
        if "Hypothesis kinds" in prompt:
            ids = re.findall(r'"id": "([a-z_0-9]+)"', prompt)
            return {"hypotheses": [{"id": i, "kinds": ["source_completeness", "source_structure"]} for i in ids]}
        self.prompts.append(prompt)
        return {"unsupported": []}


class ClaimContextTest(unittest.TestCase):
    def setUp(self):
        self.judge = Judge()
        self.run = Run("INC-009", verification=True, client=FakeModel(judge=self.judge))
        self.run.call("set_investigation_plan", hypotheses=PLAN)
        self.before = self.run.call("query_data", sql=NULLS)
        self.run.call("propose_fix", path="models/staging/stg_orders.sql", reason="r",
                      old_text="    customer_id,\n", new_text=BACKFILL)
        self.run.call("run_replay")
        self.after = self.run.call("query_data", sql=NULLS, target="replay")

    def tearDown(self):
        self.run.close()

    def rca(self, claims):
        return {"summary": "s", "fix": "f", "followups": [], "claims": claims,
                "root_causes": [{"fault_type": "null_spike", "explanation": "ids arrived null", "query_ids": ["Q1"],
                                 "location": {"kind": "source", "table": "orders", "column": "customer_id"}}]}

    def test_queries_carry_their_context(self):
        record = IncidentRecord(self.run.workspace, self.run.run_id)
        q1, q2 = record.query("Q1"), record.query("Q2")
        self.assertEqual((q1["run"], q1["after_patch"]), ("failed", None))
        self.assertEqual((q2["run"], q2["after_patch"]), ("replay-1", "P1"))

    def test_judge_sees_before_after_and_replay_results(self):
        n = self.before["rows"][0][0]
        result = self.run.call("submit_rca", **self.rca([
            {"claim": f"Before the fix, staging had {n} null customer ids", "query_ids": ["Q1"]},
            {"claim": "After the fix, staging has 0 null customer ids", "query_ids": ["Q2"]},
            {"claim": "The replay passes 25 of 25 checks", "query_ids": ["Q2"]}]))
        self.assertEqual(result.get("status"), "awaiting_review", result)
        prompt = self.judge.prompts[-1]
        self.assertIn('"warehouse": "failed run (before any fix)"', prompt)
        self.assertIn('"warehouse": "replay-1 (after patch P1)"', prompt)
        self.assertIn("REPLAY RESULTS", prompt)
        self.assertIn('"status": "success"', prompt)
        self.assertIn("supported by them even without a query", prompt)

    def test_replay_numbers_are_not_flagged_as_missing(self):
        ctx_items = verify_claims.replay_results(_ctx(self.run))
        numbers = [v for r in ctx_items for v in (r["checks_passed"], r["checks_total"])]
        self.assertEqual(verify_claims.missing_numbers(f"passes {numbers[0]} of {numbers[1]} checks", [], numbers), [])

    def test_context_is_derived_for_queries_logged_before_this_change(self):
        record = IncidentRecord(self.run.workspace, self.run.run_id)
        for q in record.data["queries"]:
            for key in ("run", "after_patch", "turn"):
                q.pop(key, None)
        record._save("test_strip", "test", {})
        ctx = _ctx(self.run)
        self.assertEqual(verify_claims.query_context(ctx, ctx.record.query("Q1"))["warehouse"], "failed run (before any fix)")
        self.assertTrue(verify_claims.query_context(ctx, ctx.record.query("Q2"))["warehouse"].startswith("replay-1"))


class SqlLiteralTest(unittest.TestCase):
    def test_literals_in_the_cited_sql_count_as_shown(self):
        evidence = [{"sql": "SELECT count(*) FROM stg_orders WHERE _batch_night = '2026-03-07' AND n > 3",
                     "_all_rows": [[52]]}]
        self.assertEqual(verify_claims.missing_numbers("on 2026-03-07, 52 of 149 ids were null", evidence), ["149"])


class ColumnNameTest(unittest.TestCase):
    def test_column_mentions(self):
        self.assertTrue(column_mentions("order_ts", "order_ts"))
        self.assertTrue(column_mentions("order_ts", "non_null_order_ts"))
        self.assertTrue(column_mentions("ORDERED_AT", "non_null_ordered_at"))
        self.assertFalse(column_mentions("order_ts", "order_tsz"))
        self.assertFalse(column_mentions("rder_ts", "order_ts"))

    def test_finding_cites_a_name_inside_a_column(self):
        run = Run("INC-001", verification=False)
        try:
            run.call("set_investigation_plan", hypotheses=PLAN)
            q = run.call("query_data", sql="SELECT _batch_night, count(order_ts) AS non_null_order_ts, "
                                           "count(ordered_at) AS non_null_ordered_at FROM raw_orders GROUP BY 1")
            ok = run.call("record_finding", hypothesis="nulls", claim="the night switched from order_ts to ordered_at",
                          verdict="supports", query_ids=[q["query_id"]], cited_values=["order_ts", "ordered_at"])
            self.assertEqual(ok.get("recorded"), "F1", ok)
            bad = run.call("record_finding", hypothesis="nulls", claim="c", verdict="supports",
                           query_ids=[q["query_id"]], cited_values=["shipped_at"])
            self.assertIn("does not appear", bad["error"])
        finally:
            run.close()


class FollowupsTest(unittest.TestCase):
    """INC-001 (schema drift). The snapshot's orders_schema contract is made error-level here,
    so a correct pipeline fix still leaves a failing source-level contract."""

    def setUp(self):
        self.run = Run("INC-001", verification=True, client=FakeModel(judge=Judge()))
        for copy in (self.run.sandbox.original, self.run.sandbox.pipeline):  # same in the snapshot and the working copy
            contracts = copy / "contracts" / "contracts.yaml"
            contracts.write_text(contracts.read_text().replace("{id: orders_schema, kind: schema, severity: warn,",
                                                               "{id: orders_schema, kind: schema,"))
        self.truth = load("INC-001")
        self.box = self.run.sandbox

    def tearDown(self):
        self.run.close()

    def evaluate(self, followups, n):
        return verify_fix.evaluate(self.box.pipeline, self.box.original, self.box.data, self.box.night, self.truth,
                                   self.box.runs / f"t-{n}", followups=followups)

    def fix(self):
        path = self.box.pipeline / "models/staging/stg_orders.sql"
        text = path.read_text()
        path.write_text(re.sub(r"(?<!AS )\border_ts\b", "COALESCE(order_ts, ordered_at)", text))

    def test_followups_is_required(self):
        self.run.call("set_investigation_plan", hypotheses=PLAN)
        result = self.run.call("submit_rca", summary="s", fix="f", claims=[], root_causes=[])
        self.assertIn("followups is required", result["error"])

    def test_source_contract_needs_a_followup_with_a_reason(self):
        self.fix()
        refused = self.evaluate([], 1)
        self.assertFalse(refused["passed"])
        self.assertIn("orders_schema", " ".join(refused["feedback"]))
        self.assertIn("not in followups", " ".join(refused["feedback"]))
        no_reason = self.evaluate([{"item": "orders_schema", "reason": ""}], 2)
        self.assertFalse(no_reason["passed"])
        accepted = self.evaluate([{"item": "orders_schema", "reason": "upstream renamed the column; contract owners "
                                                                       "must accept ordered_at"}], 3)
        self.assertTrue(accepted["passed"], accepted["feedback"])
        self.assertEqual(accepted["accepted_failures"], ["orders_schema"])
        self.assertTrue(accepted["hidden_passed"])

    def test_pipeline_contracts_cannot_be_deferred(self):
        refused = self.evaluate([{"item": "orders_order_ts_nulls", "reason": "later"},
                                 {"item": "orders_schema", "reason": "upstream"}], 4)  # no fix applied
        self.assertFalse(refused["passed"])
        text = " ".join(refused["feedback"])
        self.assertIn("still fails visible checks", text)
        self.assertIn("orders_order_ts_nulls", text)

    def test_followups_in_report(self):
        self.fix()
        followups = [{"item": "orders_schema", "reason": "upstream renamed the column"}]
        self.run.call("set_investigation_plan", hypotheses=PLAN)
        q = self.run.call("query_data", sql="SELECT count(*) AS n FROM raw_orders")
        self.run.call("run_replay")
        result = self.run.call("submit_rca", summary="renamed column mapped", fix="coalesce", followups=followups,
                               claims=[{"claim": "orders counted", "query_ids": [q["query_id"]]}],
                               root_causes=[{"fault_type": "schema_drift", "explanation": "renamed", "query_ids": [q["query_id"]],
                                             "location": {"kind": "source", "table": "orders", "column": "order_ts"}}])
        self.assertEqual(result.get("status"), "awaiting_review", result)
        progress.set_status(self.run.workspace, self.run.run_id, "awaiting_review")
        text = (report.approve(self.run.workspace, self.run.run_id) / "INCIDENT_REPORT.md").read_text()
        self.assertIn("## Follow-ups", text)
        self.assertIn("**orders_schema**: upstream renamed the column", text)
        self.assertIn("deferred as a follow-up: `orders_schema`", text)


def _ctx(run):
    from agent_kit import tools
    return tools.context(run.client, run.workspace, run.run_id)


if __name__ == "__main__":
    unittest.main()


class IncidentIdTest(unittest.TestCase):
    """Regression: a console poll before the first tool call created a record with no incident
    id, so verify_plan and verify_fix found no ground truth and passed without checking."""

    def test_readers_never_create_a_record(self):
        run = Run("INC-009", verification=True, client=FakeModel(judge=Judge()))
        try:
            self.assertEqual(IncidentRecord.read(run.workspace, run.run_id), {})
            IncidentRecord(run.workspace, run.run_id)  # a reader without an id
            self.assertFalse((run.workspace / "records" / f"{run.run_id}.json").exists())
            run.call("read_alert")
            self.assertEqual(IncidentRecord.read(run.workspace, run.run_id)["incident_id"], "INC-009")
        finally:
            run.close()

    def test_an_id_less_record_is_repaired_and_really_checked(self):
        run = Run("INC-009", verification=True, client=FakeModel(judge=Judge()))
        try:
            path = run.workspace / "records" / f"{run.run_id}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            broken = {"run_id": run.run_id, "incident_id": "", "created": "x", "plan": {}, "plan_checks": [],
                      "queries": [], "findings": [], "rejected_findings": [], "patches": [], "replays": [],
                      "requests": [], "submissions": [], "rca": None, "review": None, "audit": []}
            path.write_text(json.dumps(broken))
            run.call("set_investigation_plan", hypotheses=PLAN)
            record = IncidentRecord.read(run.workspace, run.run_id)
            self.assertEqual(record["incident_id"], "INC-009")
            self.assertEqual(record["plan_checks"][0]["score"], 1.0)  # the plan was really scored
            result = verify_fix.run_verification(_ctx(run), {"followups": []})
            self.assertFalse(result["passed"])  # unfixed: the hidden checks ran and failed
            self.assertIsNotNone(result.get("hidden_passed"))
        finally:
            run.close()

    def test_console_does_not_create_records(self):
        import importlib.util
        import os
        from .support import AGENT
        run = Run("INC-037", verification=True)
        try:
            os.environ["WORKSPACE_DIR"] = str(run.workspace)
            spec = importlib.util.spec_from_file_location("console_ui_t", AGENT.parent / "ui" / "ui.py")
            ui = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(ui)
            data = ui.Console().run(run.run_id)
            self.assertEqual(data["record"]["plan"], {})
            self.assertFalse((run.workspace / "records" / f"{run.run_id}.json").exists())
        finally:
            os.environ.pop("WORKSPACE_DIR", None)
            run.close()
