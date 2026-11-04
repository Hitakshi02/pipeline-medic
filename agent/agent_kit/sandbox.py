"""The per-run sandbox: a private copy of the incident's pipeline and data.

    box = Sandbox(workspace, run_id)
    box.create(incident_dir)          # copy pipeline/ + data/, keep a pristine copy, run the failed night
    box.write(path, text)             # change a pipeline file (never contracts/ or tests/)
    result = box.replay()             # run the night again with the changes -> runs/replay-N/
    box.diff()                        # unified diff of every change vs the incident snapshot
    box.db("failed" | "replay")       # the DuckDB file to query

workspace/sandboxes/<run-id>/
    pipeline/   the agent's working copy        original/  the snapshot at failure (read-only)
    data/       raw batches (late data lands here when released)
    runs/failed/, runs/replay-1/, ...           warehouse.duckdb, marts/, run_result.json, run.log
"""

from __future__ import annotations

import difflib
import json
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

PROTECTED = ("contracts", "tests")
EDITABLE = "models/**/*.sql and config.json"
REPLAY_TIMEOUT = 180


class SandboxError(ValueError):
    """A refused sandbox operation. The message is safe to show the model."""


class Sandbox:
    def __init__(self, workspace: Path, run_id: str) -> None:
        self.root = Path(workspace) / "sandboxes" / run_id
        self.pipeline = self.root / "pipeline"
        self.original = self.root / "original"
        self.data = self.root / "data"
        self.runs = self.root / "runs"

    @property
    def meta(self) -> dict:
        path = self.root / "sandbox.json"
        return json.loads(path.read_text()) if path.is_file() else {}

    @property
    def night(self) -> str:
        return self.meta["night"]

    def exists(self) -> bool:
        return self.pipeline.is_dir()

    def create(self, incident: Path) -> dict:
        incident = Path(incident)
        if self.exists():
            raise SandboxError("sandbox already exists")
        ignore = shutil.ignore_patterns("target", "__pycache__")
        shutil.copytree(incident / "pipeline", self.pipeline, ignore=ignore)
        shutil.copytree(incident / "pipeline", self.original, ignore=ignore)
        shutil.copytree(incident / "data", self.data)
        info = json.loads((incident / "incident.json").read_text())
        (self.root / "sandbox.json").write_text(json.dumps(
            {"incident_id": info["incident_id"], "night": info["night"], "incident_dir": str(incident)}, indent=2))
        return self._run("failed")

    # ---- files ----
    def resolve(self, path: str, base: Path | None = None) -> Path:
        base = base or self.pipeline
        rel = PurePosixPath(str(path).strip().lstrip("/"))
        if any(part == ".." for part in rel.parts):
            raise SandboxError("paths must stay inside the pipeline folder")
        return base / rel

    def read(self, path: str) -> str:
        target = self.resolve(path)
        if not target.is_file():
            raise SandboxError(f"no such file: {path}")
        return target.read_text()

    def listing(self) -> list:
        return sorted(str(p.relative_to(self.pipeline)) for p in self.pipeline.rglob("*")
                      if p.is_file() and "__pycache__" not in p.parts and "target" not in p.parts)

    def write(self, path: str, text: str) -> str:
        rel = PurePosixPath(str(path).strip().lstrip("/"))
        if rel.parts and rel.parts[0] in PROTECTED:
            raise SandboxError(f"{path} is under {rel.parts[0]}/, which is read-only: fix the pipeline, not the checks")
        editable = (rel.parts[:1] == ("models",) and rel.suffix == ".sql") or str(rel) == "config.json"
        if not editable:
            raise SandboxError(f"only {EDITABLE} can be changed; {path} is not one of them")
        target = self.resolve(path)
        before = target.read_text() if target.is_file() else ""
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        return "".join(difflib.unified_diff(before.splitlines(True), text.splitlines(True),
                                            f"a/{rel}", f"b/{rel}"))

    def changed_files(self) -> list:
        files = set(self.listing()) | {str(p.relative_to(self.original)) for p in self.original.rglob("*")
                                       if p.is_file() and "__pycache__" not in p.parts}
        changed = []
        for rel in sorted(files):
            a, b = self.original / rel, self.pipeline / rel
            if (a.read_bytes() if a.is_file() else None) != (b.read_bytes() if b.is_file() else None):
                changed.append(rel)
        return changed

    def diff(self) -> str:
        out = []
        for rel in self.changed_files():
            a, b = self.original / rel, self.pipeline / rel
            before = a.read_text().splitlines(True) if a.is_file() else []
            after = b.read_text().splitlines(True) if b.is_file() else []
            out.append("".join(difflib.unified_diff(before, after, f"a/{rel}", f"b/{rel}")))
        return "".join(out)

    # ---- runs ----
    def replay(self) -> dict:
        n = len(list(self.runs.glob("replay-*"))) + 1
        return self._run(f"replay-{n}")

    def latest(self) -> str:
        replays = sorted(self.runs.glob("replay-*"), key=lambda p: int(p.name.split("-")[1]))
        return replays[-1].name if replays else "failed"

    def db(self, target: str = "failed") -> Path:
        name = self.latest() if target in ("replay", "latest") else "failed"
        return self.runs / name / "warehouse.duckdb"

    def result(self, name: str) -> dict:
        path = self.runs / name / "run_result.json"
        return json.loads(path.read_text()) if path.is_file() else {}

    def _run(self, name: str) -> dict:
        out = self.runs / name
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        command = [sys.executable, "run_pipeline.py", "--night", self.night, "--out", str(out), "--quiet"]
        try:
            proc = subprocess.run(command, cwd=self.pipeline, capture_output=True, text=True, timeout=REPLAY_TIMEOUT)
        except subprocess.TimeoutExpired:
            result = {"night": self.night, "status": "failed", "contracts": [], "tests": [], "marts": [],
                      "errors": [{"stage": "runner", "error": f"the run took longer than {REPLAY_TIMEOUT}s"}]}
            (out / "run_result.json").write_text(json.dumps(result))
            return dict(result, run=name)
        result = self.result(name)
        if not result:  # the runner itself crashed (e.g. an edit broke run_pipeline.py)
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
            result = {"night": self.night, "status": "failed", "contracts": [], "tests": [], "marts": [],
                      "errors": [{"stage": "runner", "error": " | ".join(tail) or "the runner crashed"}]}
            (out / "run_result.json").write_text(json.dumps(result))
        return dict(result, run=name)

    def add_data(self, source: Path) -> list:
        """Copy released batch files (a folder of night=*/<table>.csv) into the sandbox data."""
        added = []
        for f in sorted(Path(source).rglob("*.csv")):
            rel = f.relative_to(source)
            target = self.data / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
            added.append(str(rel))
        return added


def summarize(result: dict) -> dict:
    """What the agent sees from a run: visible checks only."""
    contracts = result.get("contracts", [])
    failing = [{"id": c["id"], "kind": c["kind"], "detail": c["detail"]} for c in contracts
               if not c["passed"] and c.get("severity", "error") == "error"]
    warnings = [{"id": c["id"], "kind": c["kind"], "detail": c["detail"]} for c in contracts
                if not c["passed"] and c.get("severity") == "warn"]
    tests = [{"id": t["id"], "detail": t["detail"]} for t in result.get("tests", []) if not t["passed"]]
    return {
        "run": result.get("run"),
        "status": result.get("status"),
        "errors": [{k: (str(v).splitlines()[0][:300] if k == "error" else v) for k, v in e.items()}
                   for e in result.get("errors", [])],
        "failing_contracts": failing,
        "failing_tests": tests,
        "source_warnings": warnings,
        "checks_passed": sum(1 for c in contracts + result.get("tests", []) if c["passed"]),
        "checks_total": len(contracts) + len(result.get("tests", [])),
        "marts_built": result.get("marts", []),
    }
