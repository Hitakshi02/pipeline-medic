"""The agent's tools, one module per tool. Each module has DEFINITION (the function
schema the model sees) and run(ctx, args) -> dict (what happens when the model calls it).

    defs = definitions()                                   # offered to the model every turn
    result = run(client, workspace, run_id, call)          # run the tool the model chose

A refused call returns {"error": ...} (plus feedback) so the model can correct itself.
A result carries "status" only when the run must stop: "needs_input" (a person must
act), "awaiting_review" (the RCA passed every check), "submitted" (verification off).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..incident_record import IncidentRecord, RecordError
from ..progress import load
from ..sandbox import Sandbox, SandboxError
from . import (inspect_schema, profile_table, propose_fix, query_data, read_alert, read_file, read_logs,
               record_finding, request_info, run_replay, set_investigation_plan, submit_rca)

MODULES = [read_alert, read_logs, inspect_schema, profile_table, query_data, read_file,
           set_investigation_plan, record_finding, request_info, propose_fix, run_replay, submit_rca]
BY_NAME = {m.DEFINITION["name"]: m for m in MODULES}


@dataclass
class Context:
    client: Any
    workspace: Path
    run_id: str
    incident: Path
    record: IncidentRecord
    sandbox: Sandbox
    verification: bool

    @property
    def night(self) -> str:
        return self.sandbox.night

    @property
    def incident_id(self) -> str:
        """The run's incident, from progress.json (the authority)."""
        return load(self.workspace, self.run_id)["incident_id"]


def definitions() -> list:
    return [m.DEFINITION for m in MODULES]


def names() -> list:
    return list(BY_NAME)


def context(client, workspace: Path, run_id: str) -> Context:
    state = load(workspace, run_id)
    record = IncidentRecord(workspace, run_id, state["incident_id"])
    if record.data.get("incident_id") != state["incident_id"]:  # repair a record created without its incident
        record.data["incident_id"] = state["incident_id"]
        record._save("incident_id_repaired", "runtime", {"incident_id": state["incident_id"]})
    return Context(client=client, workspace=Path(workspace), run_id=run_id, incident=Path(state["incident_dir"]),
                   record=record, sandbox=Sandbox(workspace, run_id), verification=bool(state["verification"]))

    # verification reads the incident id from ctx.incident_id (the run's progress), never from the record


def run(client, workspace: Path, run_id: str, call: Any) -> dict:
    name, raw = _field(call, "name"), _field(call, "arguments") or "{}"
    try:
        args = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (TypeError, ValueError) as error:
        return {"error": f"the arguments are not valid JSON: {error}"}
    if not isinstance(args, dict):
        return {"error": "the arguments must be a JSON object"}
    if name not in BY_NAME:
        return {"error": f"unknown tool {name!r}; available: {', '.join(BY_NAME)}"}
    ctx = context(client, workspace, run_id)
    try:
        result = BY_NAME[name].run(ctx, args)
    except (RecordError, SandboxError) as error:
        return {"error": str(error)}
    except Exception as error:  # the model reads it and tries something else
        return {"error": f"{type(error).__name__}: {str(error)[:500]}"}
    return result if isinstance(result, dict) else {"result": result}


def _field(call: Any, key: str) -> Any:
    return call.get(key) if isinstance(call, dict) else getattr(call, key, None)
