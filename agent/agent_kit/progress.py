"""Run progress on disk: saved after every turn so the console can follow a run and a
stopped run can be resumed.

    run_id = new_run(ws, incident_dir=..., instructions=..., alert=...)
    save(ws, run_id, messages, response, status="running")   # end of every turn
    state = load(ws, run_id)                                  # messages, status, turns, ...
    step = resume_message(ws, run_id)                         # what to tell the model on resume, or None

workspace/runs/<run-id>/progress.json, plus instructions.md and alert.md as sent.
Step mode (workspace/control.json, written by the console): after each saved turn a
running agent waits until the console grants another turn.
"""

from __future__ import annotations

import datetime as dt
import json
import time
import uuid
from pathlib import Path
from typing import Any

from . import settings

STOPPED = ("paused", "needs_input", "awaiting_review", "submitted", "done")


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def folder(workspace: Path, run_id: str) -> Path:
    return Path(workspace) / "runs" / run_id


def new_run(workspace: Path, *, incident_dir: Path, instructions: str, alert: str,
            verification: bool | None = None, quiet: bool = False) -> str:
    if verification is None:
        verification = settings.verification_enabled()
    info = json.loads((Path(incident_dir) / "incident.json").read_text())
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"run-{stamp}-{uuid.uuid4().hex[:6]}"
    path = folder(workspace, run_id)
    path.mkdir(parents=True)
    (path / "instructions.md").write_text(instructions)
    (path / "alert.md").write_text(alert)
    _write(workspace, run_id, {
        "run_id": run_id, "incident_id": info["incident_id"], "incident_dir": str(incident_dir),
        "verification": bool(verification), "model": settings.model(), "effort": settings.reasoning_effort(),
        "started": now(), "updated": now(), "status": "running", "turn": 0,
        "input_tokens": 0, "output_tokens": 0, "messages": [], "turns": [], "notes": {},
    })
    if not quiet:
        print(f"run {run_id} · {info['incident_id']} · verification {'on' if verification else 'off'} · "
              f"model {settings.model()} · effort {settings.reasoning_effort()}", flush=True)
    return run_id


def load(workspace: Path, run_id: str) -> dict:
    path = folder(workspace, run_id) / "progress.json"
    if not path.is_file():
        raise FileNotFoundError(f"no such run: {run_id}")
    return json.loads(path.read_text())


def list_runs(workspace: Path) -> list:
    runs = Path(workspace) / "runs"
    return sorted((p.name for p in runs.glob("run-*") if (p / "progress.json").is_file()), reverse=True)


def plain(value: Any) -> Any:
    """SDK objects -> JSON-safe dicts (so the conversation can be saved and sent again)."""
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def save(workspace: Path, run_id: str, messages: list, response: Any = None, *, status: str = "running",
         tool: dict | None = None, quiet: bool = False, **notes: Any) -> dict:
    """End of a turn. `tool` = {"name", "arguments", "result"} for the call this turn ran."""
    state = load(workspace, run_id)
    entry: dict = {"time": now(), "status": status, "end": len(messages)}
    if response is not None:
        usage = getattr(response, "usage", None)
        tokens_in = int(getattr(usage, "input_tokens", 0) or 0)
        tokens_out = int(getattr(usage, "output_tokens", 0) or 0)
        state["turn"] += 1
        state["input_tokens"] += tokens_in
        state["output_tokens"] += tokens_out
        entry.update(turn=state["turn"], input_tokens=tokens_in, output_tokens=tokens_out,
                     text=(getattr(response, "output_text", "") or "")[:2000])
    if tool:
        entry["tool"] = tool
    state["turns"].append(entry)
    state["notes"].update(notes)
    state.update(messages=[plain(m) for m in messages], status=status, updated=now())
    _write(workspace, run_id, state)
    if not quiet:
        print(_line(state, entry), flush=True)
    if status == "running":
        wait_for_step(workspace)
    return state


def set_status(workspace: Path, run_id: str, status: str, **notes: Any) -> dict:
    state = load(workspace, run_id)
    state["status"] = status
    state["notes"].update(notes)
    state["updated"] = now()
    _write(workspace, run_id, state)
    return state


def resume_message(workspace: Path, run_id: str) -> dict:
    """What a stopped run should hear next: {"message": str | None, "status", "note"}.

    paused -> continue; needs_input -> the answer to the open request (once someone
    answered it); awaiting_review -> approved (run ends: done) or the rejection notes.
    """
    from .incident_record import IncidentRecord

    state = load(workspace, run_id)
    status = state["status"]
    record = IncidentRecord(workspace, run_id)
    if status == "paused":
        return _go("You have more turns. Continue the investigation and finish with submit_rca.")
    if status == "needs_input":
        requests = record.data["requests"]
        if not requests:
            return _stop(status, "no request to answer")
        latest = requests[-1]
        if latest["status"] == "open":
            return _stop(status, f"{latest['id']} is still open: answer it (world/release.py or the console)")
        message = f"Reply to your request {latest['id']}: {latest['response']}"
        released = latest.get("released") or []
        if released:
            message += f"\nNew files landed in the sandbox data folder: {', '.join(released)}"
        return _go(message + "\nContinue: replay if the data changed, then finish with submit_rca.")
    if status == "awaiting_review":
        review = record.data.get("review")
        if not review:
            return _stop(status, "waiting for an engineer to approve or reject the RCA")
        if review["decision"] == "approved":
            set_status(workspace, run_id, "done")
            return _stop("done", "the engineer approved the fix")
        return _go(f"The on-call engineer rejected your RCA: {review['notes']}\n"
                   "Address the notes, replay, and submit a revised RCA.")
    return _stop(status, f"the run is {status}; nothing to resume")


def _go(message: str) -> dict:
    return {"message": message, "status": "running", "note": ""}


def _stop(status: str, note: str) -> dict:
    return {"message": None, "status": status, "note": note}


def _write(workspace: Path, run_id: str, state: dict) -> None:
    path = folder(workspace, run_id) / "progress.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, default=str))
    tmp.replace(path)


def _line(state: dict, entry: dict) -> str:
    tool = entry.get("tool") or {}
    name = tool.get("name") or ("(text)" if entry.get("turn") else "")
    result = tool.get("result") or {}
    outcome = "refused" if "error" in result else result.get("status", "ok")
    return (f"turn {state['turn']:>3} · {name:<24} · {outcome:<16} · "
            f"tokens {state['input_tokens'] + state['output_tokens']:>8} · {entry['status']}")


# ---- step mode ----------------------------------------------------------------------


def control_path(workspace: Path) -> Path:
    return Path(workspace) / "control.json"


def read_control(workspace: Path) -> dict:
    try:
        return json.loads(control_path(workspace).read_text())
    except (OSError, ValueError):
        return {"step": False, "grants": 0}


def write_control(workspace: Path, control: dict) -> None:
    path = control_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(control))
    tmp.replace(path)


def wait_for_step(workspace: Path, poll: float = 0.3) -> None:
    """In step mode, block until the console grants a turn (and use it up)."""
    while True:
        control = read_control(workspace)
        if not control.get("step"):
            return
        if control.get("grants", 0) > 0:
            control["grants"] -= 1
            write_control(workspace, control)
            return
        time.sleep(poll)
