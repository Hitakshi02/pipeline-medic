"""Tokens per turn of a saved run, before vs after compaction, using FakeModel's counter
(characters / 4) and its simulated prefix cache. Offline: no API calls.

    ./helper tokens RUN_ID [--csv]

For each turn it rebuilds what the model was sent (instructions + tool definitions + the
conversation up to that turn) and counts it as-is ("before") and with the loop's compaction
("after"), plus the tokens a prefix cache would serve. The API's own count for the turn is
shown for comparison.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_kit import compact, progress, settings, tools  # noqa: E402
from agent_kit.model import estimate_tokens, request_segments, shared_prefix_tokens  # noqa: E402


def report(workspace: Path, run_id: str) -> list:
    state = progress.load(workspace, run_id)
    instructions = (progress.folder(workspace, run_id) / "instructions.md").read_text()
    definitions = tools.definitions()
    messages = state["messages"]
    rows, prev_end, compacted = [], 1, set()
    prev_before, prev_after = [], []
    for entry in state["turns"]:
        if not entry.get("turn"):
            continue
        sent = messages[:prev_end]
        compacted |= set(compact.due(sent, compacted))
        before = {"instructions": instructions, "tools": definitions, "input": sent}
        after = dict(before, input=compact.compact(sent, compacted))
        seg_b, seg_a = request_segments(before), request_segments(after)
        rows.append({"turn": entry["turn"], "tool": (entry.get("tool") or {}).get("name") or "",
                     "api_input": entry.get("input_tokens", 0),
                     "before": estimate_tokens(before), "before_cached": shared_prefix_tokens(prev_before, seg_b),
                     "after": estimate_tokens(after), "after_cached": shared_prefix_tokens(prev_after, seg_a)})
        prev_before, prev_after, prev_end = seg_b, seg_a, entry["end"]
    return rows


def main() -> None:
    settings.load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument("--csv", action="store_true")
    args = parser.parse_args()
    rows = report(settings.workspace(), args.run_id)
    keys = ["turn", "tool", "api_input", "before", "before_cached", "after", "after_cached"]
    if args.csv:
        print(",".join(keys))
        for r in rows:
            print(",".join(str(r[k]) for k in keys))
        return
    print(f"{'turn':>4} {'tool':<24} {'API in':>8} │ {'before':>8} {'cached':>8} │ {'after':>8} {'cached':>8}")
    for r in rows:
        print(f"{r['turn']:>4} {r['tool']:<24} {r['api_input']:>8} │ {r['before']:>8} {r['before_cached']:>8} │ "
              f"{r['after']:>8} {r['after_cached']:>8}")
    total = {k: sum(r[k] for r in rows) for k in keys[2:]}
    print(f"{'':>4} {'TOTAL':<24} {total['api_input']:>8} │ {total['before']:>8} {total['before_cached']:>8} │ "
          f"{total['after']:>8} {total['after_cached']:>8}")
    saved = 1 - total["after"] / total["before"] if total["before"] else 0
    print(f"\ninput tokens: {total['before']} -> {total['after']} ({saved:.0%} fewer); uncached input: "
          f"{total['before'] - total['before_cached']} -> {total['after'] - total['after_cached']}")


if __name__ == "__main__":
    main()
