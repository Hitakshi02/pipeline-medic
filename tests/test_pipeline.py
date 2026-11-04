"""Phase 1: the generator is deterministic, a clean night passes every check, and a
replay of the same night reproduces the golden outputs byte for byte."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from pipeline_template.generator import generate
from pipeline_template.golden import build_golden, run_on


def digest(folder: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(Path(folder).rglob("*.csv")):
        h.update(str(f.relative_to(folder)).encode())
        h.update(f.read_bytes())
    return h.hexdigest()


class GeneratorTest(unittest.TestCase):
    def test_same_seed_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            generate.write(generate.generate(11), Path(a))
            generate.write(generate.generate(11), Path(b))
            self.assertEqual(digest(Path(a)), digest(Path(b)))

    def test_different_seed_differs(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            generate.write(generate.generate(11), Path(a))
            generate.write(generate.generate(12), Path(b))
            self.assertNotEqual(digest(Path(a)), digest(Path(b)))

    def test_shape(self):
        batches = generate.generate(3)
        self.assertEqual(len(batches), 7)
        for night, tables in batches.items():
            self.assertEqual(set(tables), set(generate.TABLES))
            self.assertGreater(len(tables["orders"]), 20)
            self.assertGreater(len(tables["customers"]), 0)
        # weekend nights are busier on average than Monday-Tuesday
        sat = len(batches["2026-03-07"]["orders"])
        mon = len(batches["2026-03-02"]["orders"])
        self.assertGreater(sat, mon)


class PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.data = Path(cls.tmp.name) / "data"
        generate.write(generate.generate(7), cls.data)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_clean_night_passes_every_check(self):
        with tempfile.TemporaryDirectory() as out:
            result = run_on(self.data, Path(out))
            self.assertEqual(result["errors"], [])
            failed = [c for c in result["contracts"] + result["tests"] if not c["passed"]]
            self.assertEqual(failed, [])
            self.assertEqual(result["status"], "success")
            self.assertEqual(sorted(result["marts"]),
                             ["customer_ltv", "daily_revenue", "inventory_levels", "late_shipments"])

    def test_replay_matches_golden_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as golden, tempfile.TemporaryDirectory() as out:
            build_golden(self.data, Path(golden), night="2026-03-07")
            run_on(self.data, Path(out), night="2026-03-07")
            self.assertEqual(digest(Path(golden)), digest(Path(out) / "marts"))
            self.assertEqual(len(list(Path(golden).glob("*.csv"))), 4)

    def test_earlier_night_is_its_own_replay(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            run_on(self.data, Path(a), night="2026-03-05")
            run_on(self.data, Path(b), night="2026-03-05")
            self.assertEqual(digest(Path(a) / "marts"), digest(Path(b) / "marts"))
            text = (Path(a) / "marts" / "daily_revenue.csv").read_text()
            self.assertNotIn("2026-03-06", text)


if __name__ == "__main__":
    unittest.main()
