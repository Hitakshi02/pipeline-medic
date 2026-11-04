"""set_investigation_plan: the hypotheses to test (the plan the work is judged against).

With verification on, verification/verify_plan.py checks the plan first. A plan that
misses a kind of hypothesis is refused with feedback about the kind only; after
MAX_TRIES it is accepted anyway and the coverage score stays in the record for the
reviewer.
"""

from __future__ import annotations

import importlib

MAX_TRIES = 3

DEFINITION = {
    "type": "function",
    "name": "set_investigation_plan",
    "description": "Set your investigation plan: the competing hypotheses for what broke, each with how you will "
    "test it. Required before you record findings, propose a fix, or submit. Calling it again replaces the plan "
    "(hypotheses that already have findings must be kept).",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "hypotheses": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "id": {"type": "string", "description": "snake_case, e.g. upstream_rename"},
                        "hypothesis": {"type": "string", "description": "what might have gone wrong"},
                        "how_to_test": {"type": "string", "description": "the query or check that would confirm or rule it out"},
                    },
                    "required": ["id", "hypothesis", "how_to_test"],
                },
            }
        },
        "required": ["hypotheses"],
    },
}


def run(ctx, args: dict) -> dict:
    hypotheses = args.get("hypotheses") or []
    ctx.record.parse_plan(hypotheses)  # malformed plans are refused before any check
    if ctx.verification:
        check = importlib.import_module("verification.verify_plan").run_verification(ctx, hypotheses)
        if check.get("score") is not None:
            attempt = len(ctx.record.data["plan_checks"]) + 1
            accepted = bool(check["passed"]) or attempt >= MAX_TRIES
            ctx.record.record_plan_check(dict(check, attempt=attempt, accepted=accepted))
            if not accepted:
                return {"error": "plan refused", "feedback": check.get("feedback", ""),
                        "attempt": attempt, "max_tries": MAX_TRIES}
    plan = ctx.record.set_plan(hypotheses)
    return {"plan": {k: v["hypothesis"] for k, v in plan.items()},
            "next": "test each hypothesis with query_data and record what you find with record_finding"}
