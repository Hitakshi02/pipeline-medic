"""Phase 4: the loop runs an incident end to end with a scripted FakeModel (no API)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_kit import progress
from agent_kit.incident_record import IncidentRecord
from agent_kit.model import FakeModel
from medic_agent.loop import EXTRA_CALL, LAST_TURN, NUDGE, resume_run, start_run

from .support import incident

PLAN = [{"id": "upstream_nulls", "hypothesis": "customer_id arrives null upstream", "how_to_test": "null rate by night"},
        {"id": "model_edit", "hypothesis": "a model change", "how_to_test": "diff the models"}]
BACKFILL = ("    COALESCE(customer_id, (SELECT c.customer_id FROM raw_customers AS c "
            "WHERE lower(c.email) = lower(raw_orders.customer_email))) AS customer_id,\n")
NULLS_SQL = ("SELECT count(*) AS null_ids FROM stg_orders WHERE customer_id IS NULL "
             "AND _batch_night = '2026-03-07'")


def last_output(kwargs) -> dict:
    for item in reversed(kwargs["input"]):
        if isinstance(item, dict) and item.get("type") == "function_call_output":
            return json.loads(item["output"])
    return {}


def cite_last_query(kwargs) -> dict:
    out = last_output(kwargs)
    return {"tool": "record_finding", "args": {
        "hypothesis": "upstream_nulls", "claim": "customer_id is null on many orders of the failed night",
        "verdict": "supports", "query_ids": [out["query_id"]], "cited_values": [str(out["rows"][0][0])]}}


def solve_script() -> list:
    return [
        {"tool": "read_alert"},
        {"tool": "read_logs", "args": {"log": "run_2026-03-07.log"}},
        {"tool": "set_investigation_plan", "args": {"hypotheses": PLAN}},
        {"tool": "query_data", "args": {"sql": NULLS_SQL}},
        cite_last_query,
        {"tool": "propose_fix", "args": {"path": "models/staging/stg_orders.sql", "reason": "repair ids from email",
                                         "old_text": "    customer_id,\n", "new_text": BACKFILL}},
        {"tool": "run_replay"},
        {"tool": "submit_rca", "args": {
            "summary": "customer_id arrived null; backfilled from email",
            "root_causes": [{"fault_type": "null_spike", "explanation": "nulls upstream", "query_ids": ["Q1"],
                             "location": {"kind": "source", "table": "orders", "column": "customer_id"}}],
            "claims": [{"claim": "many null customer ids", "query_ids": ["Q1"]}],
            "fix": "COALESCE with a lookup by email", "followups": []}},
    ]


class LoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_end_to_end_with_fake_model(self):
        fake = FakeModel(script=solve_script())
        result = start_run(workspace=self.ws, client=fake, incident_dir=incident("INC-009"),
                           verification=False, quiet=True)
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["turns"], 8)
        state = progress.load(self.ws, result["run_id"])
        tools_used = [t["tool"]["name"] for t in state["turns"] if t.get("tool")]
        self.assertEqual(tools_used[0], "read_alert")
        self.assertEqual(tools_used[-1], "submit_rca")
        replay = next(t for t in state["turns"] if t.get("tool", {}).get("name") == "run_replay")
        self.assertEqual(replay["tool"]["result"]["run_status"], "success")
        record = IncidentRecord(self.ws, result["run_id"])
        self.assertEqual(record.data["findings"][0]["hypothesis"], "upstream_nulls")
        self.assertEqual(record.data["rca"]["status"], "unverified")
        # the model saw the playbook and the alert, and the tools every turn
        first = fake.calls[0]
        self.assertIn("on-call playbook", first["instructions"])
        self.assertIn("PAGE", first["input"][0]["content"])
        self.assertEqual(len(first["tools"]), 12)
        self.assertGreater(state["input_tokens"], 0)

    def test_text_answer_gets_a_nudge(self):
        fake = FakeModel(script=["I think it is fine.", {"tool": "read_alert"}])
        result = start_run(workspace=self.ws, client=fake, incident_dir=incident("INC-037"), turns=2,
                           verification=False, quiet=True)
        self.assertEqual(result["status"], "paused")
        contents = [m.get("content") for m in fake.calls[1]["input"] if isinstance(m, dict)]
        self.assertIn(NUDGE, contents)
        self.assertIn(LAST_TURN, contents)  # turn 2 of 2 is the last

    def test_model_failure_pauses_and_resume_continues(self):
        fake = FakeModel(script=[{"tool": "read_alert"}, RuntimeError("overloaded")] + solve_script())
        result = start_run(workspace=self.ws, client=fake, incident_dir=incident("INC-009"),
                           verification=False, quiet=True)
        self.assertEqual(result["status"], "paused")
        self.assertEqual(progress.load(self.ws, result["run_id"])["notes"]["error"], "RuntimeError: overloaded")
        resumed = resume_run(workspace=self.ws, client=fake, run_id=result["run_id"], quiet=True)
        self.assertEqual(resumed["status"], "submitted")

    def test_extra_tool_calls_are_answered_not_run(self):
        fake = FakeModel(script=[lambda kw: {"tool": "read_alert"}])
        original = fake._respond

        def two_calls(kwargs):
            response = original(kwargs)
            if kwargs.get("tools"):
                response.output.append({"type": "function_call", "call_id": "extra", "name": "run_replay",
                                        "arguments": "{}"})
            return response

        fake._respond = two_calls
        result = start_run(workspace=self.ws, client=fake, incident_dir=incident("INC-037"), turns=1,
                           verification=False, quiet=True)
        messages = progress.load(self.ws, result["run_id"])["messages"]
        outputs = {m["call_id"]: json.loads(m["output"]) for m in messages if m.get("type") == "function_call_output"}
        self.assertEqual(outputs["extra"], EXTRA_CALL)
        self.assertEqual(IncidentRecord(self.ws, result["run_id"]).data["replays"], [])

    def test_needs_input_then_resume_after_answer(self):
        fake = FakeModel(script=[{"tool": "request_info", "args": {"question": "anything change?"}},
                                 {"tool": "read_alert"}])
        result = start_run(workspace=self.ws, client=fake, incident_dir=incident("INC-037"),
                           verification=False, quiet=True)
        self.assertEqual(result["status"], "needs_input")
        blocked = resume_run(workspace=self.ws, client=fake, run_id=result["run_id"], quiet=True)
        self.assertEqual(blocked["status"], "needs_input")  # nobody answered yet
        IncidentRecord(self.ws, result["run_id"]).answer_request("REQ1", "Nothing changed.")
        resumed = resume_run(workspace=self.ws, client=fake, run_id=result["run_id"], turns=1, quiet=True)
        self.assertEqual(resumed["status"], "paused")
        sent = [m.get("content", "") for m in fake.calls[-1]["input"] if isinstance(m, dict)]
        self.assertTrue(any("Nothing changed." in str(c) for c in sent))


if __name__ == "__main__":
    unittest.main()
