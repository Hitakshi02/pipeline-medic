"""verify_fix accepts the reference fix of every incident and refuses an unfixed sandbox."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from evals import reference_fixes  # noqa: E402
from verification import verify_fix  # noqa: E402
from verification.ground_truth import load  # noqa: E402


def sandbox(tmp: Path, iid: str) -> Path:
    shutil.copytree(ROOT / "incidents" / iid / "pipeline", tmp / "pipeline")
    shutil.copytree(ROOT / "incidents" / iid / "pipeline", tmp / "original")
    shutil.copytree(ROOT / "incidents" / iid / "data", tmp / "data")
    return tmp


class VerifyFixLabelsTest(unittest.TestCase):
    def test_every_reference_fix_passes_and_every_fault_unfixed_fails(self):
        for truth_file in sorted((ROOT / "ground_truth").glob("INC-*.json")):
            iid = truth_file.stem
            truth = load(iid)
            with self.subTest(iid=iid), tempfile.TemporaryDirectory() as tmp:
                box = sandbox(Path(tmp), iid)
                night = truth["night"]
                if truth["faults"]:
                    broken = verify_fix.evaluate(box / "pipeline", box / "original", box / "data", night, truth, box / "e0")
                    self.assertFalse(broken["passed"], f"{iid} unfixed passed")
                reference_fixes.apply(truth, box / "pipeline", box / "data", ROOT)
                import difflib
                diff = "".join("".join(difflib.unified_diff((box / "original" / f).read_text().splitlines(True),
                                                            (box / "pipeline" / f).read_text().splitlines(True)))
                               for f in ("models/staging/stg_orders.sql", "models/staging/stg_order_items.sql",
                                         "models/staging/stg_shipments.sql", "config.json"))
                good = verify_fix.evaluate(box / "pipeline", box / "original", box / "data", night, truth, box / "e1",
                                           diff=diff)
                self.assertTrue(good["passed"], f"{iid}: {json.dumps(good['feedback'])}")


if __name__ == "__main__":
    unittest.main()
