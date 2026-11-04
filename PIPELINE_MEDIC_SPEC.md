# Pipeline Medic — Build Spec

An on-call agent for broken data pipelines. When a nightly pipeline breaks, the agent investigates, finds the root cause, writes a fix, and **proves the fix works by replaying the failed run against hidden checks** before a human approves it.

Architecture mirrors the Meta `legal-agent-workshop` (reference copy at `<PATH_TO_LEGAL_WORKSHOP>`): a pre-built kit, a plain OpenAI-SDK agent loop, a verification layer that sits **inside the tools** so the agent cannot skip it, ground truth kept outside the agent's folder, a simulated outside world, and a console UI.

> **Rights rule (hackathon requirement):** the workshop repo shows no license. Study its architecture and patterns, but write all code, UI, and data originally. Do not copy files or large code blocks from it.

---

## 1. Repo tree

```
pipeline-medic/
├── README.md                  # what it is, setup, how to run, results
├── HANDBOOK.md                # how to run a single incident, the bench, the console
├── agent/                     # the agent's workspace (the only place the agent lives)
│   ├── .env.example           # MODEL_API_KEY, MODEL=muse-spark-1.3, REASONING_EFFORT=low, VERIFICATION=on, INCIDENT_DIR
│   ├── helper                 # CLI: setup | run | resume | verify | test | bench
│   ├── VERIFICATION.md        # spec of every check (table: when / file / check / on failure)
│   ├── agent_kit/             # pre-built library
│   │   ├── settings.py        # loads .env, paths
│   │   ├── model.py           # OpenAI SDK client → https://api.meta.ai/v1; FakeModel for tests
│   │   ├── incident_record.py # the agent's record: plan, findings, queries run, fix, RCA, status
│   │   ├── progress.py        # save after every turn; resume a stopped run
│   │   ├── sandbox.py         # copies the incident's pipeline into a per-run sandbox; runs it; replays a night
│   │   └── tools/             # one file per tool; definitions() and run()
│   ├── medic_agent/
│   │   ├── system_instructions.md   # the playbook: plan, investigate with evidence, fix, prove, submit
│   │   └── loop.py                  # the agent loop
│   ├── verification/          # checks called from inside tools
│   └── tests/                 # offline tests (FakeModel; no API calls)
├── pipeline_template/         # the pipeline under test (copied into each incident)
│   ├── generator/             # seeded synthetic data generator
│   ├── models/                # SQL transforms: staging → marts
│   ├── contracts/             # data contracts the detector enforces (read-only to the agent)
│   ├── tests/                 # visible tests (read-only to the agent)
│   └── run_pipeline.py        # run one night end to end
├── incidents/                 # one folder per incident (like cases/)
│   └── INC-001/
│       ├── alert.md           # what the on-call engineer is paged with (the agent's assignment)
│       ├── pipeline/          # snapshot of the pipeline at failure time (with the fault)
│       ├── data/              # raw batches for the failed night and baseline nights
│       └── logs/              # run logs
├── ground_truth/              # OUTSIDE agent/. never visible to the agent
│   └── INC-001.json           # fault type, root location, expected fix type, hidden invariants, golden outputs path
├── world/                     # simulated outside world
│   ├── upstream_team.py       # answers request_info (e.g. "yes, we renamed that column on Tuesday")
│   └── release.py             # releases late data / replies, moves a needs_input run forward
├── evals/
│   ├── inject_faults.py       # builds incidents + ground truth from the template
│   ├── bench.py               # runs many incidents headless across configs
│   ├── metrics.py             # scores runs against ground truth
│   └── results/               # results.json, per-run rows, charts data
└── ui/
    └── ui.py                  # console: runs, timeline, evidence, replay diff, sign-off, results dashboard
```

---

## 2. The pipeline under test

- **Domain:** a synthetic eyewear retailer. Tables: `customers`, `products`, `orders`, `order_items`, `inventory`, `shipments`.
- **Generator:** seeded, emits one batch per "night" with realistic distributions (weekday seasonality, a few returns, some late shipments). Same seed → byte-identical output.
- **Engine:** DuckDB. Transforms are plain SQL files run in dependency order by `run_pipeline.py` (use dbt-core + dbt-duckdb only if it installs cleanly offline; otherwise the plain-SQL runner).
- **Models:** `stg_*` per source → marts `daily_revenue`, `inventory_levels`, `late_shipments`, `customer_ltv`.
- **Contracts (detector):** schema and types, row count vs trailing baseline, null rates, freshness, uniqueness of keys, invariants (revenue ≥ 0, no orphan foreign keys).
- **Golden outputs:** for every night, the correct mart outputs from a clean run are saved to `ground_truth/` as the oracle.

---

## 3. Fault injector → labeled incidents

`evals/inject_faults.py` creates incidents from the template. Each fault writes a ground-truth label.

| Fault | Example | Root location |
|---|---|---|
| schema_drift | upstream renames `order_ts` → `ordered_at` | source |
| type_change | `price` arrives as string with currency symbol | source |
| null_spike | 40% of `customer_id` null | source |
| late_partial | half the batch missing | source / freshness |
| duplicate_keys | source double-sends rows | source |
| join_fanout | new many-to-many causes revenue to double | model file + line |
| timezone_shift | timestamps silently switch to UTC | source |
| code_regression | bad edit to a model's filter | model file + line |
| config_error | wrong source path or date window | config |

Ground truth per incident:
```json
{
  "incident_id": "INC-007",
  "fault_type": "join_fanout",
  "root_location": {"kind": "model", "file": "models/marts/daily_revenue.sql", "line": 14},
  "expected_fix_type": "dedupe_or_fix_join",
  "hidden_invariants": ["sum(revenue) within 0.5% of golden", "row count equals golden"],
  "golden_outputs": "ground_truth/golden/INC-007/",
  "split": "test"
}
```

Generate ~4 incidents per fault type plus 6 clean-night controls and 4 two-fault incidents. Split **dev / test by fault seed**, never randomly.

---

## 4. Tools (agent_kit/tools/, one file each)

| Tool | What it does | Built-in guard |
|---|---|---|
| `read_alert` | returns `alert.md` | — |
| `read_logs` | reads run logs, paginated | — |
| `inspect_schema` | schema of a table at a night | — |
| `profile_table` | row count, null rates, distinct counts vs baseline nights | — |
| `query_data` | read-only SQL over DuckDB | SELECT only, row limit, every query + result logged with an id |
| `read_file` | read pipeline files in the sandbox | — |
| `set_investigation_plan` | the list of hypotheses to test (the checklist analogue) | runs `verify_plan` |
| `record_finding` | a finding must cite ≥1 logged query id | refused if the cited query does not exist or its result does not contain the cited value (the "quote-checked fact" analogue) |
| `request_info` | ask the upstream team | run → `needs_input`; `world/release.py` answers |
| `propose_fix` | write a patch in the sandbox | refused for any path under `contracts/` or `tests/` |
| `run_replay` | replay the failed night in the sandbox with the patch | returns contract results (visible checks only) |
| `submit_rca` | root cause + evidence + fix | runs `verify_rca` → `verify_fix` → `verify_claims` |

Until a plan is set, the agent can read and query but cannot record findings, propose fixes, or submit.

---

## 5. Verification layer (inside the tools)

Each check: one file, one entry point `run_verification(ctx, work) -> {"passed": bool, "feedback": ..., ...}`. The agent sees **only** the feedback.

| When | File | Check | On failure |
|---|---|---|---|
| `set_investigation_plan` | `verify_plan.py` | Muse Spark marks which ground-truth-relevant hypothesis classes the plan covers; code turns it into a coverage score | refused with feedback pointing to the **kind** of hypothesis missing, never naming the answer; after 3 tries accepted, score kept for the reviewer |
| `submit_rca` | `verify_rca.py` (code only) | every claim cites a logged query; root location points to a real file/line or source; a replay was run after the last patch; no `request_info` open | refused with the problems |
| `submit_rca` | `verify_fix.py` (code only) | **the proof:** replay output matches golden outputs within tolerance and passes **hidden invariants** the agent never saw; patch does not touch contracts/tests; no "cheat fix" (blanket row drops, `WHERE x IS NOT NULL` that removes > N% rows, swallowing errors, hard-coded values) | refused with *what* failed (e.g. "row count differs from expected"), never the golden values |
| `submit_rca` | `verify_claims.py` | one Muse Spark call: is each claim supported by its cited query results? | refused with the unsupported claims |
| 3 refused submissions | — | escalate to a human (`needs_input` with a reply box) | — |
| all pass | — | `awaiting_review`: engineer approves (→ `done`, writes `patch.diff` + `INCIDENT_REPORT.md`) or rejects with notes (→ back to the agent) | — |

`VERIFICATION=off` runs everything without checks (the baseline).

---

## 6. Agent loop (medic_agent/loop.py)

Same shape as the workshop: start a run, send `system_instructions.md` as the system prompt and `alert.md` as the first message, offer `tools.definitions()`. Each turn: model picks one tool, code runs it with `tools.run()`, the result goes back, progress is saved. Stop when a tool result says the run is over or at the turn limit (on the last turn, tell the model to submit). If the model call fails after retries, save as `paused`. `resume_run` continues a stopped run. The model chooses every tool; nothing is hard-coded per incident.

Model client: OpenAI Python SDK, `base_url="https://api.meta.ai/v1"`, `api_key=os.environ["MODEL_API_KEY"]`, model from `.env` (default `muse-spark-1.3`), reasoning effort from `.env`.

---

## 7. Console UI (ui/ui.py, original design)

Local web app at `http://127.0.0.1:8765`, standard library or a small framework.

- **Run view:** start a run on an incident, step through turns or run to end; timeline with plan / act / observe rows per turn; the investigation plan panel with findings attached; every query and its result; the patch diff; **replay diff vs golden** (pass/fail per check, without revealing golden values to the agent); refusals as red rows naming the check; handoff card for `needs_input`; reviewer approve / reject.
- **Results view:** reads `evals/results/`; tables and charts for every metric in section 8, filterable by fault type, model, effort, verification on/off.

---

## 8. Benchmark and metrics (evals/)

`./helper bench --split test --configs configs.yaml` runs every incident headless for each config:

- **Configs:** verification on vs off; `muse-spark-1.3` at efforts minimal / low / medium / high / max; `muse-wonder-1.0` at one effort.
- **Per run, record:** detection (did the contract fire), predicted fault type, predicted root location, fix passed hidden checks, cheat-fix flag, refusals, escalations, turns, tokens, wall time, estimated cost.

**Metrics** (`evals/metrics.py`):
- Detection recall on faults, false-alarm rate on clean nights.
- Root-cause accuracy: fault type correct; location correct.
- **Fix success rate** (passes hidden invariants and golden match).
- **Cheat-fix rate** (passes visible checks, fails hidden ones, or weakens checks).
- Precision / recall / F1 of `verify_fix` as a classifier of good vs bad fixes (labels: hidden-check outcome).
- Cost and time per resolved incident.
- All with bootstrap 95% CIs; per fault type; dev used for tuning, test reported once.
- Charts: quality vs cost (Pareto) across effort levels; verification on vs off; accuracy by fault type.

---

## 9. Build phases (commit after each; tests must pass)

1. **Pipeline + generator + golden outputs** — deterministic replay test.
2. **Fault injector + ground truth** — one incident per fault type, verified by contracts firing.
3. **agent_kit** — record, progress, sandbox, all tools, FakeModel.
4. **Agent loop + playbook** — run one incident end to end with FakeModel in tests.
5. **Verification layer** — all checks + tests, including cheat-fix detection tests.
6. **world/** — upstream replies and late-data release.
7. **UI** — run view, then results view.
8. **evals/** — bench, metrics, results files.
9. **Docs** — README, HANDBOOK, VERIFICATION.md.
10. **Smoke run** — one real incident with the real model, verification on (one API run only).

## 10. Rules

- Python 3.9+, minimal dependencies, works offline except calls to `api.meta.ai`.
- Never open `.env`; never print or log API keys or populated environment values.
- Tests use FakeModel; never call the API in tests.
- Nothing in `agent/` may read `ground_truth/` except `verification/` through one loader function.
- No case-specific logic in the agent or tools.
- Original code only (see the rights rule above). Credit any library in the README.
