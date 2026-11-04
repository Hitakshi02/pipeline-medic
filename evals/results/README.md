# evals/results/

Written by `./helper bench` (via `evals/bench.py` and `evals/metrics.py`); read by the console's Results view.

| File | What it holds |
|---|---|
| `runs.jsonl` | One JSON row per (config, incident) run: incident, split, true fault types, config (model, effort, verification), final status, detection, predicted fault types and locations, whether each was correct, visible/hidden check outcomes, cheat flags, verify_fix verdict, fix success, refusals, escalations, upstream requests, turns, tokens, wall time, estimated cost. Re-running a config on an incident replaces its row. |
| `results_<split>.json` | Aggregates for one split: per config, every metric with a bootstrap 95% CI (`summary`); per fault type (`by_fault_type`); quality vs cost points with the Pareto frontier flagged (`pareto`). |
| `results.json` | A copy of the most recently written `results_<split>.json` (what the console shows). |
| `raw/` | Per-run workspaces (progress, records) for inspection. Git-ignored. |

Rows from scripted FakeModel policies (`--fake`) are kept apart from real-model rows and labeled in `results.json` (`note`): they exercise the pipeline and say nothing about a model.

## Recorded results

- **harness-v1** (git tag): `spark-low` on 5 dev incidents (INC-017, INC-021, INC-025, INC-029, INC-037). All five fixed and passed the hidden checks; every refusal came from `verify_claims`. INC-029's fault-type error is a harness artifact: the claims judge treated the RCA's fault-type label as a claim and refused it, so the agent relabeled the fault `other`. Fixed after harness-v1.
