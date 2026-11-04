"""run_replay: replay the failed night in the sandbox with your changes. Returns the
visible checks only (contracts and tests); hidden checks run when you submit."""

from ..sandbox import summarize

DEFINITION = {
    "type": "function",
    "name": "run_replay",
    "description": "Replay the failed night in your sandbox with your current changes (and any data that has "
    "landed). Returns the run status, model errors, failing contracts and tests, and source warnings (which "
    "describe the raw batch as delivered and do not fail the run). Query the result with target='replay'.",
    "parameters": {"type": "object", "additionalProperties": False, "properties": {}, "required": []},
}


def run(ctx, args: dict) -> dict:
    summary = summarize(ctx.sandbox.replay())
    summary["changed_files"] = ctx.sandbox.changed_files()
    replay = ctx.record.add_replay(summary)
    out = dict(summary, replay=replay["id"])
    out.pop("status", None)
    out["run_status"] = summary["status"]  # "status" is reserved for stopping the run
    return out
