"""read_alert: the page or ticket the on-call engineer received (the assignment)."""

DEFINITION = {
    "type": "function",
    "name": "read_alert",
    "description": "Read the alert (page or ticket) for this incident: what failed, for which night, who is affected.",
    "parameters": {"type": "object", "additionalProperties": False, "properties": {}, "required": []},
}


def run(ctx, args: dict) -> dict:
    return {"night": ctx.night, "alert": (ctx.incident / "alert.md").read_text()}
