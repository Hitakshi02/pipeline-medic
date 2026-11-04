"""propose_fix: change one pipeline file in the sandbox. Only models/**/*.sql and
config.json can change; anything under contracts/ or tests/ is refused."""

DEFINITION = {
    "type": "function",
    "name": "propose_fix",
    "description": "Change one file of the pipeline in your sandbox: models/**/*.sql or config.json. Either give "
    "the full new `content`, or replace an exact `old_text` (must occur once) with `new_text`. contracts/ and "
    "tests/ are read-only. Returns the diff. Run run_replay afterwards to prove the change.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": {"type": "string", "description": "e.g. models/staging/stg_orders.sql"},
            "reason": {"type": "string", "description": "why this change fixes the root cause"},
            "content": {"type": "string", "description": "the complete new file"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
        },
        "required": ["path", "reason"],
    },
}


def run(ctx, args: dict) -> dict:
    ctx.record.require_plan("propose a fix")
    path = str(args.get("path", "")).strip()
    reason = str(args.get("reason", "")).strip()
    if not reason:
        return {"error": "say why the change fixes the root cause (reason)"}
    if args.get("content") is not None:
        text = str(args["content"])
    elif args.get("old_text") is not None and args.get("new_text") is not None:
        current = ctx.sandbox.read(path)
        old = str(args["old_text"])
        count = current.count(old) if old else 0
        if count != 1:
            return {"error": f"old_text occurs {count} times in {path}; it must occur exactly once"}
        text = current.replace(old, str(args["new_text"]))
    else:
        return {"error": "give either content, or old_text and new_text"}
    diff = ctx.sandbox.write(path, text)
    if not diff:
        return {"error": "the change does not alter the file"}
    patch = ctx.record.add_patch(path, reason, diff)
    return {"patch": patch["id"], "diff": diff, "next": "run run_replay to test the change"}
