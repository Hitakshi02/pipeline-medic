"""Turn budget, the submit reminder, compaction of old query results, prompt-cache stability."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_kit import compact, model, progress
from agent_kit.incident_record import IncidentRecord
from agent_kit.model import FakeModel
from medic_agent import loop

from .support import incident

QUERY = {"tool": "query_data", "args": {"sql": "SELECT _batch_night, count(*) AS n FROM raw_orders GROUP BY 1 ORDER BY 1"}}


def user_texts(call) -> list:
    return [m.get("content") for m in call["input"] if isinstance(m, dict) and m.get("role") == "user"]


class BudgetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults(self):
        self.assertEqual(loop.DEFAULT_TURNS, 60)
        self.assertEqual(loop.REMIND_AT, 45)
        import inspect
        self.assertEqual(inspect.signature(loop.start_run).parameters["turns"].default, 60)

    def test_reminder_at_turn_45_then_last_turn(self):
        fake = FakeModel(exhausted={"tool": "read_alert"})
        result = loop.start_run(workspace=self.ws, client=fake, incident_dir=incident("INC-037"), turns=47,
                                verification=False, quiet=True)
        self.assertEqual(result["turns"], 47)
        reminder = loop.REMINDER.format(turn=45)
        self.assertNotIn(reminder, user_texts(fake.calls[43]))      # turn 44
        self.assertIn(reminder, user_texts(fake.calls[44]))         # turn 45
        self.assertEqual(sum(t == reminder for t in user_texts(fake.calls[46])), 1)  # once only
        self.assertIn(loop.LAST_TURN, user_texts(fake.calls[46]))
        self.assertIn("You have 47 turns", user_texts(fake.calls[0])[0])

    def test_reminder_counts_turns_across_resume(self):
        fake = FakeModel(exhausted={"tool": "read_alert"})
        first = loop.start_run(workspace=self.ws, client=fake, incident_dir=incident("INC-037"), turns=44,
                               verification=False, quiet=True)
        self.assertEqual(first["status"], "paused")
        before = len(fake.calls)
        loop.resume_run(workspace=self.ws, client=fake, run_id=first["run_id"], turns=3, quiet=True)
        self.assertIn(loop.REMINDER.format(turn=45), user_texts(fake.calls[before]))


class CompactionTest(unittest.TestCase):
    def test_summary_keeps_the_query_id(self):
        out = json.dumps({"query_id": "Q7", "columns": ["n"], "rows": [[52]] * 40, "row_count": 40, "truncated": True})
        line = json.loads(compact.summary(out))
        self.assertEqual(line["query_id"], "Q7")
        self.assertIn("cite Q7", line["compacted"])
        self.assertLess(len(compact.summary(out)), len(out))
        self.assertEqual(compact.summary('{"plan": {}}'), '{"plan": {}}')  # not a query result

    def test_batches_and_sticky(self):
        messages = []
        for n in range(1, 20):
            messages += [{"type": "function_call", "call_id": f"c{n}", "name": "query_data" if n % 4 else "read_file"},
                         {"type": "function_call_output", "call_id": f"c{n}", "output": json.dumps({"query_id": f"Q{n}", "rows": []})}]
            if n == 11:  # 5 old query results waiting: below the batch size
                self.assertEqual(compact.due(messages, set()), [])
        due = compact.due(messages, set())
        self.assertGreaterEqual(len(due), compact.BATCH)
        last_six = {m["call_id"] for m in messages if m["type"] == "function_call_output"}
        last_six = sorted(last_six, key=lambda c: int(c[1:]))[-compact.KEEP_FULL:]
        self.assertFalse(set(due) & set(last_six))
        self.assertTrue(all(int(c[1:]) % 4 for c in due))  # only query_data results
        self.assertEqual(compact.due(messages, set(due)), [])  # nothing new until another batch waits

    def test_run_compacts_keeps_citations_and_cache_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)

            def cite_first(kw):
                return {"tool": "record_finding", "args": {"hypothesis": "h", "claim": "nights counted", "verdict": "supports",
                                                           "query_ids": ["Q1"], "cited_values": ["2026-03-01"]}}
            script = [{"tool": "set_investigation_plan", "args": {"hypotheses": [{"id": "h", "hypothesis": "x", "how_to_test": "y"}]}}]
            script += [QUERY] * 16 + [cite_first]
            fake = FakeModel(script=script)
            result = loop.start_run(workspace=ws, client=fake, incident_dir=incident("INC-009"), turns=18,
                                    verification=False, quiet=True)
            state = progress.load(ws, result["run_id"])
            compacted = state["notes"].get("compacted") or []
            self.assertGreaterEqual(len(compacted), compact.BATCH)
            # the model saw a summary for Q1, yet the finding citing Q1's value was accepted from the record
            sent = fake.calls[-1]["input"]
            q1 = next(m for m in sent if m.get("type") == "function_call_output" and m["call_id"] == compacted[0])
            self.assertIn("compacted", json.loads(q1["output"]))
            self.assertEqual(IncidentRecord(ws, result["run_id"]).data["findings"][0]["query_ids"], ["Q1"])
            # the saved conversation keeps full results
            saved = next(m for m in state["messages"] if m.get("type") == "function_call_output" and m["call_id"] == compacted[0])
            self.assertIn("rows", json.loads(saved["output"]))
            # between compaction batches the whole previous request is a cached prefix
            turns = [t for t in state["turns"] if t.get("turn")]
            stable = [t for prev, t in zip(turns, turns[1:]) if t["cached_tokens"] >= prev["input_tokens"] - 1]
            self.assertGreaterEqual(len(stable), len(turns) - 3)

    def test_cache_hints_sent(self):
        fake = FakeModel(script=[{"tool": "read_alert"}])
        model.create(fake, input=[{"role": "user", "content": "hi"}], tools=[{"type": "function", "name": "x"}])
        self.assertEqual(fake.calls[0]["prompt_cache_key"], "pipeline-medic")
        self.assertNotIn("prompt_cache_retention", fake.calls[0])


if __name__ == "__main__":
    unittest.main()
