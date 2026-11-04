"""Phase 6: the upstream team answers requests and late data lands in the run's sandbox."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from agent_kit import progress, tools  # noqa: E402
from agent_kit.incident_record import IncidentRecord  # noqa: E402
from agent_kit.model import FakeModel  # noqa: E402
from agent_kit.sandbox import Sandbox  # noqa: E402

spec = importlib.util.spec_from_file_location("world_release", ROOT / "world" / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class WorldTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def start(self, iid: str) -> str:
        incident = ROOT / "incidents" / iid
        run_id = progress.new_run(self.ws, incident_dir=incident, instructions="t",
                                  alert=(incident / "alert.md").read_text(), verification=False, quiet=True)
        Sandbox(self.ws, run_id).create(incident)
        return run_id

    def call(self, run_id: str, name: str, **args) -> dict:
        return tools.run(FakeModel(), self.ws, run_id, {"name": name, "arguments": json.dumps(args)})

    def test_late_data_is_released_and_the_replay_recovers(self):
        run_id = self.start("INC-013")  # late_partial
        result = self.call(run_id, "request_info", question="Was the 2026-03-07 orders export complete?")
        progress.set_status(self.ws, run_id, result["status"])
        out = release.release(self.ws, run_id)
        self.assertIn("re-delivered", out["reply"])
        self.assertEqual(sorted(Path(p).name for p in out["released"]), ["order_items.csv", "orders.csv", "shipments.csv"])
        step = progress.resume_message(self.ws, run_id)
        self.assertIn("New files landed", step["message"])
        replay = self.call(run_id, "run_replay")
        self.assertEqual(replay["run_status"], "success", replay)
        request = IncidentRecord(self.ws, run_id).data["requests"][0]
        self.assertEqual(request["status"], "answered")

    def test_model_fault_gets_nothing_changed(self):
        run_id = self.start("INC-021")  # join_fanout
        progress.set_status(self.ws, run_id, self.call(run_id, "request_info", question="any change?")["status"])
        out = release.release(self.ws, run_id)
        self.assertIn("Nothing changed on our side", out["reply"])
        self.assertEqual(out["released"], [])

    def test_source_fault_reply_and_refusals(self):
        run_id = self.start("INC-001")  # schema_drift
        with self.assertRaises(release.NothingToRelease):
            release.release(self.ws, run_id)  # not waiting
        progress.set_status(self.ws, run_id, self.call(run_id, "request_info", question="schema change?")["status"])
        self.assertIn("now called", release.release(self.ws, run_id)["reply"])
        with self.assertRaises(release.NothingToRelease):
            release.release(self.ws, run_id)  # already answered

    def test_escalations_need_a_person(self):
        run_id = self.start("INC-037")
        IncidentRecord(self.ws, run_id, "INC-037").open_request("refused 3 times", kind="escalation")
        progress.set_status(self.ws, run_id, "needs_input")
        with self.assertRaises(release.NothingToRelease):
            release.release(self.ws, run_id)


if __name__ == "__main__":
    unittest.main()
