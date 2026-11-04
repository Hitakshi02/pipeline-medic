"""Test helpers: temporary workspaces and runs on the real incidents. Offline only:
every client here is a FakeModel."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from agent_kit import progress
from agent_kit.model import FakeModel
from agent_kit.sandbox import Sandbox

AGENT = Path(__file__).resolve().parents[1]
INCIDENTS = AGENT.parent / "incidents"

os.environ.pop("MODEL_API_KEY", None)  # tests never reach the API


def incident(incident_id: str) -> Path:
    return INCIDENTS / incident_id


class Run:
    """A run on a real incident in a temporary workspace."""

    def __init__(self, incident_id: str, verification: bool = False, client=None) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.incident = incident(incident_id)
        self.client = client or FakeModel()
        self.run_id = progress.new_run(self.workspace, incident_dir=self.incident, instructions="test",
                                       alert=(self.incident / "alert.md").read_text(),
                                       verification=verification, quiet=True)
        self.sandbox = Sandbox(self.workspace, self.run_id)
        self.failed = self.sandbox.create(self.incident)

    def call(self, name: str, **args) -> dict:
        from agent_kit import tools

        return tools.run(self.client, self.workspace, self.run_id, {"name": name, "arguments": json.dumps(args)})

    def close(self) -> None:
        self.tmp.cleanup()
