"""Scripted stand-ins for the model, for offline bench tests and demos. They are told the
answer (they read ground truth and the reference fixes), so their numbers say nothing
about a real model: they exist to exercise the bench, the checks and the metrics.

    oracle  investigates, applies the reference fix, replays, submits the true root causes
    cheat   drops the failed night's broken rows (a cheat fix), replays, submits
    lazy    submits a guess without investigating or fixing
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from agent_kit.model import FakeModel  # noqa: E402

from . import reference_fixes  # noqa: E402

PLAN = [{"id": f"h_{k}", "hypothesis": f"possible {k.replace('_', ' ')} problem", "how_to_test": "compare nights"}
        for k in ("source", "model", "config")]


def _judge(prompt: str) -> dict:
    if "Hypothesis kinds" in prompt:
        kinds = re.findall(r"^- (\w+):", prompt, re.M)
        ids = re.findall(r'"id": "([a-z_0-9]+)"', prompt)
        return {"hypotheses": [{"id": i, "kinds": kinds} for i in ids]}
    return {"unsupported": []}


def _cite(kw) -> dict:
    out = {}
    for item in reversed(kw["input"]):
        if isinstance(item, dict) and item.get("type") == "function_call_output":
            out = json.loads(item["output"])
            break
    return {"tool": "record_finding", "args": {"hypothesis": "h_source", "claim": "the failed night's volume",
                                               "verdict": "supports", "query_ids": [out.get("query_id", "Q1")],
                                               "cited_values": [str((out.get("rows") or [[0]])[0][0])]}}


def _causes(truth: dict) -> list:
    causes = []
    for f in truth["faults"] or [{"fault_type": "none", "root_location": {"kind": "none"}}]:
        loc = {k: v for k, v in f["root_location"].items() if v is not None}
        causes.append({"fault_type": f["fault_type"], "location": loc, "explanation": "see evidence", "query_ids": ["Q1"]})
    return causes


def _fixed_files(truth: dict) -> dict:
    """{path: new content} after the reference fix (code changes only)."""
    incident = ROOT / "incidents" / truth["incident_id"]
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        shutil.copytree(incident / "pipeline", work / "pipeline")
        shutil.copytree(incident / "data", work / "data")
        reference_fixes.apply(dict(truth, faults=[f for f in truth["faults"] if f["fault_type"] != "late_partial"]),
                              work / "pipeline", work / "data", ROOT)
        changed = {}
        for f in sorted((work / "pipeline").rglob("*")):
            rel = f.relative_to(work / "pipeline")
            original = incident / "pipeline" / rel
            if f.is_file() and (not original.is_file() or original.read_bytes() != f.read_bytes()):
                changed[str(rel)] = f.read_text()
        return changed


def script(policy: str, truth: dict) -> list:
    steps: list = [{"tool": "read_alert"}, {"tool": "set_investigation_plan", "args": {"hypotheses": PLAN}},
                   {"tool": "query_data", "args": {"sql": "SELECT _batch_night, count(*) AS n FROM raw_orders "
                                                          "GROUP BY 1 ORDER BY 1 DESC"}}, _cite]
    submit = {"summary": "scripted", "claims": [{"claim": "volume by night", "query_ids": ["Q1"]}], "fix": "scripted"}
    if policy == "lazy":
        return steps + [{"tool": "submit_rca", "args": dict(submit, root_causes=[
            {"fault_type": "other", "location": {"kind": "none"}, "explanation": "unknown", "query_ids": ["Q1"]}])}]
    if policy == "cheat":
        steps += [{"tool": "propose_fix", "args": {
            "path": "models/staging/stg_orders.sql", "reason": "drop broken rows",
            "old_text": "FROM raw_orders;", "new_text": "FROM raw_orders\nWHERE customer_id IS NOT NULL\n"
                                                        "  AND _batch_night < '2026-03-07';"}},
                  {"tool": "run_replay"}]
        return steps + [{"tool": "submit_rca", "args": dict(submit, root_causes=_causes(truth))}] * 3
    steps = steps[:2]  # the oracle proves its claims on the repaired night
    if any(f["fault_type"] == "late_partial" for f in truth["faults"]):
        steps.append({"tool": "request_info", "args": {"question": "Was the failed night's batch complete?"}})
    for path, content in _fixed_files(truth).items():
        steps.append({"tool": "propose_fix", "args": {"path": path, "reason": "reference", "content": content}})
    steps.append({"tool": "run_replay"})
    steps += [{"tool": "query_data", "args": {"target": "replay", "sql": "SELECT _batch_night, count(*) AS n "
                                                                         "FROM raw_orders GROUP BY 1 ORDER BY 1 DESC"}},
              _cite]
    return steps + [{"tool": "submit_rca", "args": dict(submit, root_causes=_causes(truth))}]


def client(policy: str, truth: dict) -> FakeModel:
    return FakeModel(script=script(policy, truth), judge=_judge,
                     exhausted={"tool": "submit_rca", "args": {"summary": "out of script", "claims": [], "fix": "",
                                                                "root_causes": _causes(truth)}})
