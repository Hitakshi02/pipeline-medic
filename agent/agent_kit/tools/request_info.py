"""request_info: ask the upstream data team a question. The run stops (needs_input) until
someone answers (world/release.py, or the console); late data may land in the sandbox."""

DEFINITION = {
    "type": "function",
    "name": "request_info",
    "description": "Ask the upstream data team a question about the source data (changes, outages, late or "
    "re-delivered batches). The run pauses until they answer, which costs time: ask only what your queries "
    "cannot establish.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"question": {"type": "string"}},
        "required": ["question"],
    },
}


def run(ctx, args: dict) -> dict:
    question = str(args.get("question", "")).strip()
    if not question:
        return {"error": "ask a question"}
    if ctx.record.open_requests():
        return {"error": f"{ctx.record.open_requests()[0]['id']} is still open"}
    request = ctx.record.open_request(question)
    return {"request": request["id"], "status": "needs_input",
            "note": "the upstream team has been asked; the run resumes when they answer"}
