#!/usr/bin/env python3
"""The outside world moves a needs_input run forward: the upstream team answers the
run's open request, and re-delivered batches (if any) land in the run's sandbox.

    python world/release.py RUN_ID [--workspace DIR]      (or ./helper release RUN_ID)

Escalations (refused RCAs) need a person instead: ./helper review RUN_ID --answer REQ --notes ...
Then resume the run: ./helper resume RUN_ID
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WORLD = Path(__file__).resolve().parent
sys.path.insert(0, str(WORLD.parent / "agent"))
sys.path.insert(0, str(WORLD))

import upstream_team  # noqa: E402
from agent_kit import progress, settings  # noqa: E402
from agent_kit.incident_record import IncidentRecord  # noqa: E402
from agent_kit.sandbox import Sandbox  # noqa: E402


class NothingToRelease(RuntimeError):
    pass


def release(workspace: Path, run_id: str) -> dict:
    state = progress.load(workspace, run_id)
    if state["status"] != "needs_input":
        raise NothingToRelease(f"{run_id} is {state['status']}, not waiting for input")
    record = IncidentRecord(workspace, run_id)
    open_requests = record.open_requests()
    if not open_requests:
        raise NothingToRelease("no open request")
    request = open_requests[-1]
    if request["kind"] != "upstream":
        raise NothingToRelease(f"{request['id']} is an escalation: a person must answer it "
                               f"(./helper review {run_id} --answer {request['id']} --notes ...)")
    incident = state["incident_id"]
    released = []
    folder = upstream_team.late_data(incident)
    if folder is not None:
        released = Sandbox(workspace, run_id).add_data(folder)
    reply = upstream_team.answer(incident, request["question"])
    record.answer_request(request["id"], reply, actor="upstream_team", released=released)
    return {"run_id": run_id, "request": request["id"], "reply": reply, "released": released}


def main() -> None:
    parser = argparse.ArgumentParser(description="Answer a needs_input run's open request.")
    parser.add_argument("run_id")
    parser.add_argument("--workspace", help="default: agent/workspace (WORKSPACE_DIR)")
    args = parser.parse_args()
    settings.load_env()
    workspace = Path(args.workspace) if args.workspace else settings.workspace()
    try:
        result = release(workspace, args.run_id)
    except (NothingToRelease, FileNotFoundError) as error:
        sys.exit(str(error))
    print(json.dumps(result, indent=2))
    print(f"next: ./helper resume {args.run_id}")


if __name__ == "__main__":
    main()
