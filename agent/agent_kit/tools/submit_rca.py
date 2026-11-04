"""submit_rca: file the root-cause analysis and the fix.

Verification off: recorded unverified; the run ends ("submitted").
Verification on: verify_rca (code), then verify_fix (code: the replay against hidden
checks), then verify_claims (one model call). The first failure refuses the RCA with
its feedback. After MAX_REFUSALS refusals the run escalates to a person (needs_input).
All pass: the RCA waits for an engineer's sign-off ("awaiting_review").
"""

from __future__ import annotations

import importlib

MAX_REFUSALS = 3
FAULT_TYPES = ["schema_drift", "type_change", "null_spike", "late_partial", "duplicate_keys", "join_fanout",
               "timezone_shift", "code_regression", "config_error", "none", "other"]
CHECKS = ("verify_rca", "verify_fix", "verify_claims")

LOCATION = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "kind": {"type": "string", "enum": ["source", "model", "config", "none"]},
        "table": {"type": "string", "description": "source: the raw table, e.g. orders"},
        "column": {"type": "string", "description": "source: the column, if one"},
        "file": {"type": "string", "description": "model/config: the pipeline file"},
        "line": {"type": "integer", "description": "model/config: the line of the fault"},
        "key": {"type": "string", "description": "config: the setting"},
    },
    "required": ["kind"],
}
ROOT_CAUSE = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "fault_type": {"type": "string", "enum": FAULT_TYPES},
        "location": LOCATION,
        "explanation": {"type": "string"},
        "query_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["fault_type", "location", "explanation", "query_ids"],
}
CLAIM = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"claim": {"type": "string"}, "query_ids": {"type": "array", "items": {"type": "string"}}},
    "required": ["claim", "query_ids"],
}

DEFINITION = {
    "type": "function",
    "name": "submit_rca",
    "description": "Submit the root-cause analysis: a summary, every root cause (fault type, exact location, "
    "explanation, the query ids that show it), the evidence claims with their query ids, and the fix you "
    "replayed. If nothing is broken, submit fault_type 'none' with location kind 'none' and no changes. "
    "With verification on, the RCA and the fix are checked before an engineer reviews them.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {"type": "string", "description": "2-4 sentences for the on-call channel"},
            "root_causes": {"type": "array", "items": ROOT_CAUSE, "minItems": 1},
            "claims": {"type": "array", "items": CLAIM},
            "fix": {"type": "string", "description": "what you changed and why it is the right fix"},
        },
        "required": ["summary", "root_causes", "claims", "fix"],
    },
}


def run(ctx, args: dict) -> dict:
    ctx.record.require_plan("submit")
    rca = {
        "summary": str(args.get("summary", "")),
        "root_causes": [dict(r) for r in args.get("root_causes") or [] if isinstance(r, dict)],
        "claims": [dict(c) for c in args.get("claims") or [] if isinstance(c, dict)],
        "fix": str(args.get("fix", "")),
        "diff": ctx.sandbox.diff(),
        "changed_files": ctx.sandbox.changed_files(),
    }
    if not ctx.verification:
        ctx.record.add_submission(rca, [], "unverified")
        return {"submitted": True, "verified": False, "status": "submitted"}

    if ctx.record.open_requests():
        problems = [f"{r['id']} is still open" for r in ctx.record.open_requests()]
        results = [{"check": "verify_rca", "passed": False, "feedback": problems}]
    else:
        results = []
        for name in CHECKS:
            result = importlib.import_module(f"verification.{name}").run_verification(ctx, rca)
            results.append(dict(result, check=name))
            if not result.get("passed"):
                break
    failed = next((r for r in results if not r.get("passed")), None)
    if failed is None:
        ctx.record.add_submission(rca, results, "accepted")
        return {"submitted": True, "verified": True, "status": "awaiting_review",
                "note": "every check passed; an engineer will approve or reject the fix"}

    ctx.record.add_submission(rca, results, "refused")
    feedback = failed.get("feedback") or ["refused"]
    feedback = feedback if isinstance(feedback, list) else [str(feedback)]
    if ctx.record.refusals() >= MAX_REFUSALS:
        request = ctx.record.open_request(
            f"The RCA was refused {ctx.record.refusals()} times. Last check: {failed['check']}: "
            + "; ".join(str(f) for f in feedback) + ". How should I proceed?", kind="escalation")
        ctx.record.data["submissions"][-1]["outcome"] = "escalated"
        ctx.record._save("escalated", "runtime", {"request": request["id"]})
        return {"error": "RCA refused", "check": failed["check"], "problems": feedback,
                "escalated": request["id"], "status": "needs_input"}
    return {"error": "RCA refused", "check": failed["check"], "problems": feedback,
            "refusals": ctx.record.refusals(), "max_refusals": MAX_REFUSALS}
