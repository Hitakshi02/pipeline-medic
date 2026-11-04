"""The simulated upstream data team. They answer what they know about the night in
question: a change they shipped, an outage, a re-delivery. Their knowledge for each
incident is in world/upstream/<incident-id>.json (written by evals/inject_faults.py)."""

from __future__ import annotations

import json
from pathlib import Path

WORLD = Path(__file__).resolve().parent
SIGN_OFF = " (upstream data team)"


def knowledge(incident_id: str) -> dict:
    path = WORLD / "upstream" / f"{incident_id}.json"
    if not path.is_file():
        return {"reply": "We have no record of that pipeline night.", "late_data": False}
    return json.loads(path.read_text())


def late_data(incident_id: str) -> Path | None:
    """The folder of re-delivered batches for the incident, if they re-deliver."""
    folder = WORLD / "late_data" / incident_id
    return folder if knowledge(incident_id).get("late_data") and folder.is_dir() else None


def answer(incident_id: str, question: str) -> str:
    return knowledge(incident_id)["reply"].strip() + SIGN_OFF
