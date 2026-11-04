"""Phase 5: the verification layer, including cheat-fix detection. Offline (FakeModel judge)."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from agent_kit import progress, report
from agent_kit.incident_record import IncidentRecord
from agent_kit.model import FakeModel
from verification import verify_fix

from .support import AGENT, Run

BACKFILL = ("    COALESCE(customer_id, (SELECT c.customer_id FROM raw_customers AS c "
            "WHERE lower(c.email) = lower(raw_orders.customer_email))) AS customer_id,\n")
PLAN = [{"id": "upstream_nulls", "hypothesis": "ids missing upstream", "how_to_test": "null rate"},
        {"id": "model_edit", "hypothesis": "a model change", "how_to_test": "diff"}]


def judge_kinds(*kinds, unsupported=None):
    def judge(prompt: str) -> dict:
        if "Hypothesis kinds" in prompt:
            ids = re.findall(r'"id": "([a-z_0-9]+)"', prompt)
            return {"hypotheses": [{"id": i, "kinds": list(kinds)} for i in ids]}
        return {"unsupported": unsupported or []}
    return judge


def rca(qid="Q1", **cause):
    root = {"fault_type": "null_spike", "explanation": "customer_id arrived null", "query_ids": [qid],
            "location": {"kind": "source", "table": "orders", "column": "customer_id"}}
    root.update(cause)
    return {"summary": "s", "root_causes": [root], "claims": [{"claim": "many nulls", "query_ids": [qid]}],
            "fix": "backfill from email"}


class PlanCheckTest(unittest.TestCase):
    def test_covering_plan_passes_with_score(self):
        run = Run("INC-009", verification=True, client=FakeModel(judge=judge_kinds("source_completeness")))
        try:
            result = run.call("set_investigation_plan", hypotheses=PLAN)
            self.assertIn("plan", result)
            check = IncidentRecord(run.workspace, run.run_id).data["plan_checks"][0]
            self.assertEqual(check["score"], 1.0)
        finally:
            run.close()

    def test_missing_kind_is_refused_by_kind_then_accepted_after_three(self):
        run = Run("INC-009", verification=True, client=FakeModel(judge=judge_kinds("model_logic")))
        try:
            for attempt in (1, 2):
                result = run.call("set_investigation_plan", hypotheses=PLAN)
                self.assertEqual(result["error"], "plan refused")
                self.assertIn("complete", result["feedback"])
                for leak in ("null", "customer_id", "null_spike", "email", "orders"):
                    self.assertNotIn(leak, result["feedback"])
            self.assertIn("plan", run.call("set_investigation_plan", hypotheses=PLAN))
            checks = IncidentRecord(run.workspace, run.run_id).data["plan_checks"]
            self.assertEqual([c["accepted"] for c in checks], [False, False, True])
            self.assertEqual(checks[-1]["score"], 0.0)
        finally:
            run.close()

    def test_two_fault_and_control(self):
        run = Run("INC-045", verification=True, client=FakeModel(judge=judge_kinds("source_completeness")))
        try:
            result = run.call("set_investigation_plan", hypotheses=PLAN)
            self.assertIn("transformation logic", result["feedback"])
            self.assertEqual(IncidentRecord(run.workspace, run.run_id).data["plan_checks"][0]["score"], 0.5)
        finally:
            run.close()
        run = Run("INC-037", verification=True, client=FakeModel(judge=judge_kinds("false_alarm")))
        try:
            self.assertIn("plan", run.call("set_investigation_plan", hypotheses=PLAN))
        finally:
            run.close()


class SubmitFlowTest(unittest.TestCase):
    def setUp(self):
        self.run = Run("INC-009", verification=True, client=FakeModel(judge=judge_kinds("source_completeness")))
        self.run.call("set_investigation_plan", hypotheses=PLAN)
        self.q = self.run.call("query_data", sql="SELECT count(*) AS n FROM stg_orders WHERE customer_id IS NULL")

    def tearDown(self):
        self.run.close()

    def fix(self, new_text=BACKFILL, old_text="    customer_id,\n"):
        return self.run.call("propose_fix", path="models/staging/stg_orders.sql", reason="r",
                             old_text=old_text, new_text=new_text)

    def test_rca_grounding_problems(self):
        self.fix()
        result = self.run.call("submit_rca", **rca())
        self.assertEqual(result["check"], "verify_rca")
        self.assertIn("replay", " ".join(result["problems"]))
        self.run.call("run_replay")
        for bad, why in ((rca(qid="Q7"), "not a logged query"),
                         (rca(location={"kind": "source", "table": "orders", "column": "nope"}), "never delivered"),
                         (rca(location={"kind": "model", "file": "models/nope.sql", "line": 3}), "not a file"),
                         (rca(location={"kind": "none"}), "none")):
            result = self.run.call("submit_rca", **bad)
            self.assertEqual(result["check"], "verify_rca", bad)
            self.assertIn(why, " ".join(result["problems"]))
            if result.get("status"):  # the third refusal escalates
                self.assertEqual(result["status"], "needs_input")
                break

    def test_good_fix_reaches_review_and_approval_writes_report(self):
        self.fix()
        self.run.call("run_replay")
        result = self.run.call("submit_rca", **rca())
        self.assertEqual(result["status"], "awaiting_review", result)
        record = IncidentRecord(self.run.workspace, self.run.run_id)
        checks = record.data["submissions"][-1]["checks"]
        self.assertEqual([c["check"] for c in checks], ["verify_rca", "verify_fix", "verify_claims"])
        self.assertTrue(checks[1]["hidden_passed"])
        progress.set_status(self.run.workspace, self.run.run_id, "awaiting_review")
        folder = report.approve(self.run.workspace, self.run.run_id, "lgtm")
        self.assertIn("COALESCE", (folder / "patch.diff").read_text())
        text = (folder / "INCIDENT_REPORT.md").read_text()
        self.assertIn("null_spike", text)
        self.assertIn("approved", text)
        self.assertEqual(progress.load(self.run.workspace, self.run.run_id)["status"], "done")

    def test_reject_sends_notes_back(self):
        self.fix()
        self.run.call("run_replay")
        self.run.call("submit_rca", **rca())
        progress.set_status(self.run.workspace, self.run.run_id, "awaiting_review")
        report.reject(self.run.workspace, self.run.run_id, "explain the email join")
        step = progress.resume_message(self.run.workspace, self.run.run_id)
        self.assertIn("explain the email join", step["message"])

    def test_cheat_fix_drop_rows_is_refused(self):
        self.fix(old_text="FROM raw_orders;", new_text="FROM raw_orders\nWHERE customer_id IS NOT NULL;")
        self.run.call("run_replay")
        result = self.run.call("submit_rca", **rca())
        self.assertEqual(result["check"], "verify_fix")
        text = " ".join(result["problems"])
        self.assertIn("drops", text)
        self.assertIn("differs from expected", text)
        self.assertNotRegex(text, r"\d{3,}\.\d\d")  # no golden values leak

    def test_hard_coded_value_passes_visible_but_fails_hidden(self):
        self.fix(new_text="    COALESCE(customer_id, 'C00001') AS customer_id,\n")
        replay = self.run.call("run_replay")
        self.assertEqual(replay["run_status"], "success")  # the visible checks are fooled
        result = self.run.call("submit_rca", **rca())
        self.assertEqual(result["check"], "verify_fix")
        self.assertIn("customer_ltv", " ".join(result["problems"]))

    def test_unsupported_claims_are_refused(self):
        self.run.client.judge = judge_kinds("source_completeness", unsupported=[{"index": 2, "reason": "no"}])
        self.fix()
        self.run.call("run_replay")
        result = self.run.call("submit_rca", **rca())
        self.assertEqual(result["check"], "verify_claims")
        self.assertIn("many nulls", result["problems"][0])

    def test_claim_feedback_names_the_unsupported_number(self):
        def judge(prompt):
            if "Hypothesis kinds" in prompt:
                return judge_kinds("source_completeness")(prompt)
            return {"unsupported": [{"index": 1, "phrases": ["52/149", "not in the text"], "reason": "no total"}]}
        self.run.client.judge = judge
        self.fix()
        self.run.call("run_replay")
        n = self.q["rows"][0][0]
        result = self.run.call("submit_rca", **rca(explanation=f"{n} of 149 orders arrived with null ids (52/149)"))
        self.assertEqual(result["check"], "verify_claims")
        text = result["problems"][0]
        self.assertTrue(text.startswith("root cause 1:"), text)
        self.assertIn('"52/149"', text)          # the judge's phrase, verified to be in the statement
        self.assertNotIn("not in the text", text)  # a phrase the statement does not contain is dropped
        self.assertIn("cite that query id", text)
        record = IncidentRecord(self.run.workspace, self.run.run_id)
        unsupported = record.data["submissions"][-1]["checks"][-1]["unsupported"][0]
        self.assertIn("149", unsupported["numbers"] + unsupported["phrases"][0].split("/"))
        self.assertNotIn(str(n), unsupported["numbers"])  # n is in Q1's result

    def test_three_refusals_escalate(self):
        for attempt in range(3):
            result = self.run.call("submit_rca", **rca(qid="Q99"))
        self.assertEqual(result["status"], "needs_input")
        record = IncidentRecord(self.run.workspace, self.run.run_id)
        self.assertEqual(record.data["requests"][-1]["kind"], "escalation")
        self.assertEqual(record.refusals(), 0)
        progress.set_status(self.run.workspace, self.run.run_id, "needs_input")
        report.answer(self.run.workspace, self.run.run_id, "REQ1", "Use the email to repair ids.")
        self.assertIn("Use the email", progress.resume_message(self.run.workspace, self.run.run_id)["message"])


class ClaimNumbersTest(unittest.TestCase):
    def test_dates_are_one_token(self):
        from verification.verify_claims import missing_numbers
        evidence = [{"_all_rows": [["2026-03-07", 52], ["2026-03-06 19:33:00", 1.5]]}]
        self.assertEqual(missing_numbers("batch 2026-03-07 had 52 nulls of 149", evidence), ["149"])
        self.assertEqual(missing_numbers("on 2026-03-06 the rate was 1.5", evidence), [])
        self.assertEqual(missing_numbers("on 2026-03-05 it was fine", evidence), ["2026-03-05"])
        self.assertEqual(missing_numbers("1,204 rows", [{"_all_rows": [[1204]]}]), [])


class CheatDetectionTest(unittest.TestCase):
    def test_diff_flags(self):
        diff = ("+++ b/models/staging/stg_orders.sql\n"
                "+    CASE WHEN order_date = DATE '2026-03-07' THEN 1 END,\n"
                "+    CAST(x AS DECIMAL(12, 2)) AS y,\n"
                "+    COALESCE(total, 4521.18) AS total\n"
                "+SELECT * FROM t LIMIT 50\n"
                "+FROM read_csv('../../ground_truth/golden/x.csv')\n")
        flags = " ".join(verify_fix.diff_flags(diff, ["contracts/contracts.yaml", "models/x.sql"]))
        for expected in ("changes the checks", "hard-codes a date", "hard-codes a value", "LIMIT", "reads files"):
            self.assertIn(expected, flags)
        self.assertEqual(verify_fix.diff_flags("+    CAST(price AS DECIMAL(10, 2)),\n+  INTERVAL 5 HOUR\n", []), [])

    def test_swallowed_errors_are_flagged(self):
        run = Run("INC-005", verification=True, client=FakeModel(judge=judge_kinds("source_format")))
        try:
            run.call("set_investigation_plan", hypotheses=PLAN)
            run.call("propose_fix", path="models/staging/stg_order_items.sql", reason="r",
                     old_text="CAST(unit_price AS DECIMAL(10, 2))", new_text="TRY_CAST(unit_price AS DECIMAL(10, 2))")
            ctx_rca = {"root_causes": [], "claims": []}
            from agent_kit import tools
            result = verify_fix.run_verification(tools.context(run.client, run.workspace, run.run_id), ctx_rca)
            self.assertFalse(result["passed"])
            self.assertTrue(any("nulls" in f for f in result["cheat_flags"]), result["cheat_flags"])
        finally:
            run.close()

    def test_untouched_control_passes(self):
        run = Run("INC-037", verification=True)
        try:
            from agent_kit import tools
            result = verify_fix.run_verification(tools.context(run.client, run.workspace, run.run_id), {})
            self.assertTrue(result["passed"], result["feedback"])
        finally:
            run.close()


class GroundTruthIsolationTest(unittest.TestCase):
    def test_only_the_loader_reads_ground_truth(self):
        offenders = []
        for path in AGENT.rglob("*.py"):
            if ".venv" in path.parts or "tests" in path.parts or path.name == "ground_truth.py":
                continue
            text = path.read_text()
            if re.search(r"""["']ground_truth["'/]|ground_truth/|GROUND_TRUTH_DIR""", text):
                offenders.append(str(path.relative_to(AGENT)))
        self.assertEqual(offenders, [])
        importers = [str(p.relative_to(AGENT)) for p in AGENT.rglob("*.py")
                     if ".venv" not in p.parts and "tests" not in p.parts and "ground_truth import" in p.read_text()]
        self.assertTrue(all(p.startswith("verification/") for p in importers), importers)


if __name__ == "__main__":
    unittest.main()
