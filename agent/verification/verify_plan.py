"""Does the investigation plan cover the kinds of hypothesis this incident needs?

    run_verification(ctx, hypotheses) -> {"passed", "score", "feedback", "covered", "needed"}

One model call classifies each hypothesis into hypothesis kinds (a fixed taxonomy, the
same for every incident). Code compares the kinds covered with the kinds the incident's
ground truth needs: score = needed kinds covered / needed kinds. The feedback names the
missing kind, never the fault, the table, or the fix. No ground truth: score None (passes).
"""

from __future__ import annotations

import json

from agent_kit.model import json_call

from .ground_truth import load

KINDS = {
    "source_structure": "the source's structure changed (columns renamed, added or removed)",
    "source_format": "source values arrive in a different type, format or encoding",
    "source_completeness": "the source batch is incomplete, late, partial, or missing values",
    "source_duplication": "the source sent duplicate rows or keys",
    "source_time": "timestamps, time zones or date semantics changed",
    "model_logic": "a change in the SQL models' logic (joins, filters, expressions)",
    "configuration": "the pipeline configuration (paths, windows, settings)",
    "false_alarm": "nothing is broken; the alert or the expectation is wrong",
}
NEEDED = {
    "schema_drift": "source_structure", "type_change": "source_format", "null_spike": "source_completeness",
    "late_partial": "source_completeness", "duplicate_keys": "source_duplication", "timezone_shift": "source_time",
    "join_fanout": "model_logic", "code_regression": "model_logic", "config_error": "configuration",
    "none": "false_alarm",
}
HINTS = {
    "source_structure": "how the shape of the delivered source data might differ from what the models expect",
    "source_format": "how the delivered source values might be encoded differently than before",
    "source_completeness": "whether the delivered batch is complete",
    "source_duplication": "whether rows or keys were delivered more than once",
    "source_time": "how times and dates in the source might be interpreted",
    "model_logic": "how a change in the transformation logic could produce these symptoms",
    "configuration": "how the pipeline's own settings could produce these symptoms",
    "false_alarm": "the possibility that the data and the pipeline are fine",
}

PROMPT = """You classify the hypotheses of a data-pipeline incident investigation.

Hypothesis kinds:
{kinds}

Hypotheses:
{hypotheses}

For each hypothesis, list every kind it would test (it may be none). Answer only with JSON:
{{"hypotheses": [{{"id": "<id>", "kinds": ["<kind>", ...]}}]}}"""


def needed_kinds(truth: dict) -> set:
    faults = [f["fault_type"] for f in truth.get("faults") or []] or [truth.get("fault_type", "none")]
    return {NEEDED[f] for f in faults if f in NEEDED}


def run_verification(ctx, hypotheses: list) -> dict:
    truth = load(ctx.record.data["incident_id"])
    if truth is None:
        return {"passed": True, "score": None, "feedback": ""}
    needed = needed_kinds(truth)
    listing = "\n".join(f"- {k}: {v}" for k, v in KINDS.items())
    items = [{"id": h.get("id"), "hypothesis": h.get("hypothesis"), "how_to_test": h.get("how_to_test")}
             for h in hypotheses if isinstance(h, dict)]
    judged = json_call(ctx.client, PROMPT.format(kinds=listing, hypotheses=json.dumps(items, indent=1)))
    if judged is None:
        return {"passed": True, "score": None, "feedback": "", "note": "unreadable judgement; not scored"}
    covered = set()
    for h in judged.get("hypotheses") or []:
        if isinstance(h, dict):
            covered.update(k for k in h.get("kinds") or [] if k in KINDS)
    missing = sorted(needed - covered)
    score = round((len(needed) - len(missing)) / len(needed), 2) if needed else 1.0
    feedback = ""
    if missing:
        feedback = ("The plan is missing a kind of hypothesis. Add one about "
                    + "; and one about ".join(HINTS[k] for k in missing)
                    + ", with how you would test it.")
    return {"passed": not missing, "score": score, "feedback": feedback,
            "covered": sorted(covered), "needed": sorted(needed)}
