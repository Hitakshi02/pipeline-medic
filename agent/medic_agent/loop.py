"""The agent loop.

    result = start_run(workspace=ws, client=client, incident_dir=path)      # new run
    result = resume_run(workspace=ws, client=client, run_id=run_id)         # continue a stopped run

Each turn: the model sees the playbook (instructions), the conversation so far and the
tool definitions, and picks one tool; tools.run() runs it; the result goes back into the
conversation; progress is saved. The run stops when a tool result carries a status
(needs_input, awaiting_review, submitted), at the turn limit (paused), or when the model
call fails after the SDK's retries (paused). Nothing here is specific to an incident.

Turn budget: DEFAULT_TURNS per start. When the run reaches REMIND_AT turns (counted over
the whole run, across resumes) the model is reminded to submit; on the last turn of a
budget it is told to submit now. Old query results are compacted (agent_kit/compact.py).
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_kit import compact, model, progress, tools
from agent_kit.sandbox import Sandbox

INSTRUCTIONS = Path(__file__).with_name("system_instructions.md")
NUDGE = "Call exactly one tool per turn. Never answer in plain text."
LAST_TURN = "This is your last turn. Submit your RCA now with submit_rca, using what you have established."
DEFAULT_TURNS = 60
DEFAULT_RESUME_TURNS = 20
REMIND_AT = 45
REMINDER = ("Turn {turn}: time to wrap up. If your replay passes, submit your RCA now with submit_rca; "
            "keep the remaining turns for revisions if it is refused.")
EXTRA_CALL = {"error": "one tool per turn: this call was not run"}


def start_run(*, workspace: Path, client, incident_dir: Path, turns: int = DEFAULT_TURNS,
              verification: bool | None = None, quiet: bool = False) -> dict:
    workspace, incident_dir = Path(workspace), Path(incident_dir).resolve()
    instructions = INSTRUCTIONS.read_text()
    alert = (incident_dir / "alert.md").read_text()
    run_id = progress.new_run(workspace, incident_dir=incident_dir, instructions=instructions, alert=alert,
                              verification=verification, quiet=quiet)
    Sandbox(workspace, run_id).create(incident_dir)
    night = json.loads((incident_dir / "incident.json").read_text())["night"]
    messages: list = [{"role": "user", "content": f"{alert}\n\n(Failed night: {night}. Your sandbox is ready. "
                                                  f"You have {turns} turns.)"}]
    return _turns(workspace, client, run_id, instructions, messages, turns, quiet)


def resume_run(*, workspace: Path, client, run_id: str, turns: int = DEFAULT_RESUME_TURNS, quiet: bool = False) -> dict:
    workspace = Path(workspace)
    state = progress.load(workspace, run_id)
    step = progress.resume_message(workspace, run_id)
    if step["message"] is None:
        return _done(workspace, run_id, step["status"], step["note"])
    instructions = (progress.folder(workspace, run_id) / "instructions.md").read_text()
    messages = list(state["messages"]) + [{"role": "user", "content": f"{step['message']} (You have {turns} more turns.)"}]
    progress.set_status(workspace, run_id, "running")
    return _turns(workspace, client, run_id, instructions, messages, turns, quiet)


def _turns(workspace: Path, client, run_id: str, instructions: str, messages: list, turns: int,
           quiet: bool) -> dict:
    definitions = tools.definitions()
    state = progress.load(workspace, run_id)
    done_turns = state["turn"]
    compacted = set(state["notes"].get("compacted") or [])
    reminded = bool(state["notes"].get("reminded"))
    for i in range(turns):
        if i == turns - 1:
            messages.append({"role": "user", "content": LAST_TURN})
        elif done_turns + i + 1 >= REMIND_AT and not reminded:
            messages.append({"role": "user", "content": REMINDER.format(turn=done_turns + i + 1)})
            reminded = True
            progress.set_status(workspace, run_id, "running", reminded=True)
        new = compact.due(messages, compacted)
        if new:
            compacted.update(new)
            progress.set_status(workspace, run_id, "running", compacted=sorted(compacted))
        try:
            response = model.create(client, instructions=instructions, input=compact.compact(messages, compacted),
                                     tools=definitions)
        except Exception as error:  # the SDK already retried rate limits and overloads
            progress.save(workspace, run_id, messages, status="paused", quiet=quiet,
                          error=f"{type(error).__name__}: {str(error)[:300]}")
            return _done(workspace, run_id, "paused", "the model call failed")
        output = [progress.plain(item) for item in (response.output or [])]
        messages += output
        calls = [item for item in output if item.get("type") == "function_call"]
        if not calls:
            messages.append({"role": "user", "content": NUDGE})
            progress.save(workspace, run_id, messages, response, quiet=quiet)
            continue
        call = calls[0]
        result = tools.run(client, workspace, run_id, call)
        messages.append({"type": "function_call_output", "call_id": call.get("call_id"),
                         "output": json.dumps(result, default=str)})
        for extra in calls[1:]:
            messages.append({"type": "function_call_output", "call_id": extra.get("call_id"),
                             "output": json.dumps(EXTRA_CALL)})
        status = result.get("status", "running")
        progress.save(workspace, run_id, messages, response, status=status, quiet=quiet,
                      tool={"name": call.get("name"), "arguments": call.get("arguments"), "result": result})
        if status != "running":
            return _done(workspace, run_id, status)
    progress.save(workspace, run_id, messages, status="paused", quiet=quiet, note="turn limit reached")
    return _done(workspace, run_id, "paused", "turn limit reached")


def _done(workspace: Path, run_id: str, status: str, note: str = "") -> dict:
    state = progress.load(workspace, run_id)
    out = {"run_id": run_id, "status": status, "turns": state["turn"],
           "input_tokens": state["input_tokens"], "output_tokens": state["output_tokens"]}
    if note:
        out["note"] = note
    return out
