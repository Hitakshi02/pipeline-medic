"""Golden outputs: the correct marts for a night, from a clean run of the template.

    from pipeline_template.golden import build_golden
    result = build_golden(data_dir, dest_dir, night="2026-03-07")   # writes dest/<mart>.csv

Runs an untouched copy of the template against `data_dir` in a temporary folder and
copies the exported marts. Raises if the clean run does not succeed.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent


def copy_template(dest: Path) -> Path:
    shutil.copytree(TEMPLATE, dest, ignore=shutil.ignore_patterns("target", "__pycache__", "golden.py"))
    return dest


def run_on(data_dir: Path, out: Path, night: str | None = None, pipeline: Path | None = None) -> dict:
    """Run a copy of `pipeline` (default: the template) on `data_dir`; outputs in `out`."""
    import importlib.util

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "pipeline"
        if pipeline is None:
            copy_template(work)
        else:
            shutil.copytree(pipeline, work, ignore=shutil.ignore_patterns("target", "__pycache__"))
        config = json.loads((work / "config.json").read_text())
        config["source_path"] = str(Path(data_dir).resolve())
        (work / "config.json").write_text(json.dumps(config, indent=2))
        spec = importlib.util.spec_from_file_location("_pm_runner", work / "run_pipeline.py")
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        return runner.run(night, work, Path(out), quiet=True)


def build_golden(data_dir: Path, dest: Path, night: str | None = None) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        result = run_on(data_dir, Path(tmp), night)
        if result["status"] != "success":
            raise RuntimeError(f"clean run failed; cannot build golden outputs: {result}")
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        for csv_file in sorted((Path(tmp) / "marts").glob("*.csv")):
            shutil.copy2(csv_file, dest / csv_file.name)
    return result
