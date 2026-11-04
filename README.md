# Pipeline Medic

An on-call agent for broken data pipelines. When a nightly pipeline breaks, the agent investigates, finds the root cause, writes a fix, and **proves the fix by replaying the failed night against hidden checks** before a human approves it.

The pipeline is a synthetic eyewear retailer's nightly DuckDB job (`raw_*` → `stg_*` → four marts). A fault injector breaks it in nine realistic ways, each labeled with ground truth. The agent works each incident in a private sandbox with twelve tools. A verification layer sits *inside* those tools, so the agent cannot skip it, and a console shows every turn and lets an engineer sign off.

## How it works

```
alert.md ──► agent loop (medic_agent/loop.py) ──► one tool per turn (agent_kit/tools/)
                    │                                   │
                    │                    sandbox: copy of pipeline + data at failure
                    │                                   │
                    └─ progress saved every turn        ├─ set_investigation_plan ─► verify_plan
                                                        ├─ record_finding (must quote a logged query)
                                                        ├─ propose_fix (never contracts/ or tests/)
                                                        ├─ run_replay (visible checks only)
                                                        └─ submit_rca ─► verify_rca ─► verify_fix ─► verify_claims
                                                                              │ (hidden golden checks,
                                                                              │  cheat-fix detection)
                                              3 refusals ─► escalate          ▼
                                                                     awaiting_review ─► engineer approves
                                                                                        (patch.diff + INCIDENT_REPORT.md)
```

- **The agent** (`agent/medic_agent/`): a plain loop over the OpenAI Python SDK pointed at `https://api.meta.ai/v1` (`muse-spark-1.3` by default). The playbook is `system_instructions.md`. The model chooses every tool; nothing is specific to an incident.
- **The kit** (`agent/agent_kit/`): settings, the model client (plus `FakeModel` for tests), the incident record, progress and resume, the per-run sandbox, and the tools.
- **Verification** (`agent/verification/`): one file per check. It is the only code in `agent/` that reads `ground_truth/`, and it does so through a single loader. See [agent/VERIFICATION.md](agent/VERIFICATION.md).
- **The world** (`world/`): the upstream team answers `request_info`, and late data is re-delivered.
- **Evals** (`evals/`): the fault injector, bench, metrics and results.
- **Console** (`ui/`): run view and results view.

## Setup

Requires Python 3.9+, macOS or Linux, and a Meta Model API key (only for real runs).

```bash
cd agent
./helper setup                 # creates agent/.venv, installs duckdb, openai, pyyaml
cp .env.example .env           # then add MODEL_API_KEY=... (never commit .env)
./helper setup                 # again: checks the key can see the model
./helper test                  # offline tests: FakeModel only, never calls the API
```

`.env` holds `MODEL_API_KEY`, `MODEL` (default `muse-spark-1.3`), `REASONING_EFFORT` (`minimal|low|medium|high|max`, default `low`), `VERIFICATION` (`on|off`, default `on`) and `INCIDENT_DIR` (default `../incidents/INC-001`).

## Running

```bash
./helper run                                   # one incident (INCIDENT_DIR), until it stops
./helper release RUN_ID                        # needs_input: the upstream team answers
./helper review RUN_ID --approve               # or --reject --notes "..." / --answer REQ2 --notes "..."
./helper resume RUN_ID                         # continue after a reply, release or rejection
python3 ../ui/ui.py                            # console at http://127.0.0.1:8765
./helper bench --split dev --configs ../evals/configs.yaml   # headless benchmark (API calls)
```

[HANDBOOK.md](HANDBOOK.md) walks through all of it.

## Incidents

`python evals/inject_faults.py` rebuilds all 46 incidents deterministically:

| Fault | Example (variants differ per incident) | Root location |
|---|---|---|
| schema_drift | `order_ts` arrives as `ordered_at` | source column |
| type_change | `unit_price` arrives as `"$129.00"` | source column |
| null_spike | 35–50% of `customer_id` null | source column |
| late_partial | the export cut off: 55–70% of orders missing | source (needs re-delivery) |
| duplicate_keys | retries re-send orders / lines | source table |
| join_fanout | a join to a many-row table doubles revenue | model file + line |
| timezone_shift | `order_ts` silently switches to UTC | source column |
| code_regression | a bad edit to a filter or date expression | model file + line |
| config_error | wrong source path or lookback window | config key |

There are 4 incidents per fault, 6 clean-night controls (an analyst's ticket, nothing broken) and 4 two-fault incidents. The **dev/test split follows the fault seed** (variants 0–1 are dev, 2–3 are test; controls 3/3), never randomness. Every incident fires the contracts (controls fire nothing), and each one has a reference fix that replays to the golden outputs byte for byte; the tests check both.

## Results

**No real-model benchmark has been run yet.** The bench makes many API calls, so run it deliberately:

```bash
./helper bench --split dev  --configs ../evals/configs.yaml    # tune on dev
./helper bench --split test --configs ../evals/configs.yaml    # report test once
```

`evals/results/README.md` describes every results file. The console's Results view charts them: quality vs cost (Pareto) across effort levels, verification on vs off, and accuracy by fault type, all with bootstrap 95% CIs. To get dollar figures, fill in per-token prices in `evals/configs.yaml`; without them, cost is shown in tokens.

The offline bench (`--fake oracle|cheat|lazy`) runs scripted stand-ins that are *given the answer*. It exists to test the pipeline end to end. For example, the cheat policy is refused, escalated and counted as a cheat fix. It says nothing about any model, and its results are labeled as such.

## Repository

```
agent/          the agent's world: kit, loop + playbook, verification, tests, helper
pipeline_template/  the pipeline under test: generator, SQL models, contracts, visible tests, runner
incidents/      46 incidents: alert.md, pipeline/ snapshot, data/, logs/
ground_truth/   labels + golden outputs (outside agent/; read only by verification/)
world/          upstream team replies, late data, release.py
evals/          inject_faults.py, reference_fixes.py, bench.py, metrics.py, configs.yaml, results/
ui/             console (ui.py + index.html)
tests/          project-level tests (pipeline, faults, world, console, evals, verify_fix labels)
```

## Credits

- Architecture inspired by Meta's `legal-agent-workshop` (a pre-built kit, a plain SDK loop, checks inside the tools, ground truth outside the agent's folder, a simulated world and a console). That repository carries no license, so no files or code from it were copied. Everything here was written for this project.
- No code from `meta-oss-cookbook` or `meta-model-cookbook` is reused.
- Libraries: [DuckDB](https://duckdb.org) (MIT), the [OpenAI Python SDK](https://github.com/openai/openai-python) (Apache-2.0) and [PyYAML](https://pyyaml.org) (MIT). Everything else uses the Python standard library.
