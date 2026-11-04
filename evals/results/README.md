# evals/results/

Written by `./helper bench` (via `evals/bench.py` and `evals/metrics.py`); read by the console's Results view.

| File | What it holds |
|---|---|
| `runs.jsonl` | One JSON row per (config, incident) run: incident, split, true fault types, config (model, effort, verification), final status, detection, predicted fault types and locations, whether each was correct, visible/hidden check outcomes, cheat flags, verify_fix verdict, fix success, refusals, escalations, upstream requests, turns, tokens, wall time, estimated cost. Re-running a config on an incident replaces its row. |
| `results_<split>.json` | Aggregates for one split: per config, every metric with a bootstrap 95% CI (`summary`); per fault type (`by_fault_type`); quality vs cost points with the Pareto frontier flagged (`pareto`). |
| `results.json` | A copy of the most recently written `results_<split>.json` (what the console shows). |
| `raw/` | Per-run workspaces (progress, records) for inspection. Git-ignored. |

Rows from scripted FakeModel policies (`--fake`) are kept apart from real-model rows and labeled in `results.json` (`note`): they exercise the pipeline and say nothing about a model.
