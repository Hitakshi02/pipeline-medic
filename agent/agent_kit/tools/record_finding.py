"""record_finding: one finding about one hypothesis, backed by logged queries. Refused when
a cited query does not exist (or failed), or a cited value does not appear in the cited
query results: a finding is only as good as the data it quotes."""

DEFINITION = {
    "type": "function",
    "name": "record_finding",
    "description": "Record a finding for one hypothesis of your plan. Cite the query ids (from query_data) that "
    "show it and the exact values from those results that prove it (e.g. a count, a column name, a sample "
    "value). Refused unless every cited value appears in the cited results.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "hypothesis": {"type": "string", "description": "id of a hypothesis in your plan"},
            "claim": {"type": "string", "description": "one sentence: what the data shows"},
            "verdict": {"type": "string", "enum": ["supports", "refutes"]},
            "query_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "cited_values": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                             "description": "values copied from the cited query results"},
        },
        "required": ["hypothesis", "claim", "verdict", "query_ids", "cited_values"],
    },
}


def run(ctx, args: dict) -> dict:
    finding = ctx.record.record_finding(
        str(args.get("hypothesis", "")), str(args.get("claim", "")), str(args.get("verdict", "")),
        list(args.get("query_ids") or []), list(args.get("cited_values") or []))
    plan = {k: v["status"] for k, v in ctx.record.data["plan"].items()}
    return {"recorded": finding["id"], "plan_status": plan}
