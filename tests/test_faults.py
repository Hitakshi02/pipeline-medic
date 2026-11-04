"""Phase 2: every fault type produces a labeled incident whose contracts fire, clean
controls fire nothing, and the reference fix for each incident replays to the golden
outputs exactly (so every incident is solvable)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from evals import inject_faults, reference_fixes


def _digest(folder: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(Path(folder).glob("*.csv")):
        h.update(f.name.encode() + f.read_bytes())
    return h.hexdigest()


def _replay(pipeline: Path, out: Path) -> dict:
    spec = importlib.util.spec_from_file_location(f"_r{id(out)}", pipeline / "run_pipeline.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return runner.run(inject_faults.NIGHT, pipeline, out, quiet=True)


class FaultTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.specs = [s for s in inject_faults.plan()
                     if s["group"] == "two_fault" or s["variant"] in (0, 2)]
        cls.truth = {s["id"]: inject_faults.build_incident(s, cls.root) for s in cls.specs}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_plan_counts_and_split(self):
        specs = inject_faults.plan()
        self.assertEqual(len(specs), 9 * 4 + 6 + 4)
        self.assertEqual(len({s["id"] for s in specs}), len(specs))
        for s in specs:  # the split follows the fault seed's variant, never randomness
            self.assertEqual(s["split"], "dev" if s["fault_seed"] % 100 < (3 if s["group"] == "control" else 2) else "test")
        self.assertEqual({s["split"] for s in specs}, {"dev", "test"})

    def test_every_fault_type_fires_contracts(self):
        seen = set()
        for iid, truth in self.truth.items():
            if truth["fault_type"] == "none":
                self.assertFalse(truth["detected_by_contracts"], iid)
            else:
                self.assertTrue(truth["detected_by_contracts"], f"{iid} {truth['fault_type']}")
                seen.update(f["fault_type"] for f in truth["faults"])
        self.assertEqual(seen, set(inject_faults.FAULT_TYPES))

    def test_ground_truth_shape(self):
        for iid, truth in self.truth.items():
            for key in ("incident_id", "fault_type", "root_location", "expected_fix_type",
                        "hidden_invariants", "golden_outputs", "split"):
                self.assertIn(key, truth)
            golden = self.root / truth["golden_outputs"]
            self.assertEqual(len(list(golden.glob("*.csv"))), 4, iid)
            alert = (self.root / "incidents" / iid / "alert.md").read_text()
            for fault in truth["faults"]:  # the page never names the answer
                self.assertNotIn(fault["fault_type"], alert)
            if truth["root_location"]["kind"] == "model":
                loc = truth["root_location"]
                line = (self.root / "incidents" / iid / "pipeline" / loc["file"]).read_text().splitlines()[loc["line"] - 1]
                self.assertTrue(line.strip(), iid)

    def test_reference_fix_replays_to_golden(self):
        for iid, truth in self.truth.items():
            with self.subTest(iid=iid), tempfile.TemporaryDirectory() as tmp:
                sandbox = Path(tmp)
                shutil.copytree(self.root / "incidents" / iid / "pipeline", sandbox / "pipeline")
                shutil.copytree(self.root / "incidents" / iid / "data", sandbox / "data")
                reference_fixes.apply(truth, sandbox / "pipeline", sandbox / "data", self.root)
                result = _replay(sandbox / "pipeline", sandbox / "out")
                self.assertEqual(result["status"], "success", json.dumps(
                    [c for c in result["contracts"] + result["tests"] if not c["passed"] and c.get("severity") != "warn"] + result["errors"])[:600])
                self.assertEqual(_digest(sandbox / "out" / "marts"), _digest(self.root / truth["golden_outputs"]), iid)

    def test_world_files(self):
        for iid, truth in self.truth.items():
            reply = json.loads((self.root / "world" / "upstream" / f"{iid}.json").read_text())
            self.assertTrue(reply["reply"])
            late = any(f["fault_type"] == "late_partial" for f in truth["faults"])
            self.assertEqual(reply["late_data"], late)
            self.assertEqual((self.root / "world" / "late_data" / iid).is_dir(), late)


if __name__ == "__main__":
    unittest.main()
