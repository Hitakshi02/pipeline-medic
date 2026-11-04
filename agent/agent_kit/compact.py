"""Keep the conversation the model reads small: old query results become one-line summaries.

    sent = compact(messages, compacted)    # what goes to the model this turn

The latest KEEP_FULL tool results always stay in full. Older query_data results are
replaced by a one-line summary that keeps the query id; the incident record still holds
the full result, so findings and the RCA can keep citing it (record_finding and the checks
look results up by id, never in the conversation).

Compaction happens in batches, and a result once compacted stays compacted (`compacted`, a
set of call ids, is saved with the run). Between batches every earlier turn is byte-for-byte
the same as last turn, so the provider's prefix cache keeps matching; compacting one more
result every turn would change the prefix every turn and defeat the cache.

The full conversation is still saved in progress.json (the console shows it).
"""

from __future__ import annotations

import json

KEEP_FULL = 6      # tool results always kept in full
BATCH = 6          # compact once this many old results are waiting
COMPACTED_TOOLS = ("query_data",)


def _outputs(messages: list) -> list:
    """(index, call_id, tool name) for every tool result, oldest first."""
    names = {m.get("call_id"): m.get("name") for m in messages
             if isinstance(m, dict) and m.get("type") == "function_call"}
    return [(i, m.get("call_id"), names.get(m.get("call_id")))
            for i, m in enumerate(messages) if isinstance(m, dict) and m.get("type") == "function_call_output"]


def summary(output: str) -> str:
    try:
        result = json.loads(output)
    except (TypeError, ValueError):
        return output
    if not isinstance(result, dict) or "query_id" not in result:
        return output
    qid = result["query_id"]
    if "error" in result:
        line = f"{qid}: query failed ({str(result['error'])[:120]})"
    else:
        columns = ", ".join(result.get("columns") or [])
        first = json.dumps((result.get("rows") or [None])[0], default=str)
        line = (f"{qid}: {result.get('row_count', 0)} row(s){' (truncated)' if result.get('truncated') else ''}; "
                f"columns {columns}; first row {first[:160]}")
    return json.dumps({"query_id": qid, "compacted": line + f". Full result kept in the record: cite {qid}."})


def due(messages: list, compacted: set) -> list:
    """Call ids to compact now: a batch, once BATCH old query results are waiting."""
    outputs = _outputs(messages)
    old = outputs[:-KEEP_FULL] if len(outputs) > KEEP_FULL else []
    waiting = [cid for _, cid, name in old if name in COMPACTED_TOOLS and cid not in compacted]
    return waiting if len(waiting) >= BATCH else []


def compact(messages: list, compacted: set) -> list:
    """A copy of `messages` with the results in `compacted` replaced by their summaries."""
    out = []
    for m in messages:
        if isinstance(m, dict) and m.get("type") == "function_call_output" and m.get("call_id") in compacted:
            m = dict(m, output=summary(m.get("output", "")))
        out.append(m)
    return out
