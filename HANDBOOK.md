# Pipeline Medic handbook

How to run a single incident, the console, and the bench. All commands run from `agent/` unless noted otherwise.

## 1. One-time setup

```bash
cd agent
./helper setup            # .venv + duckdb, openai, pyyaml
cp .env.example .env      # add MODEL_API_KEY; leave the rest or adjust
./helper setup            # "MODEL_API_KEY works: muse-spark-1.3 is available"
./helper test             # offline; must be green before you spend API calls
```

The key stays in `.env`, and nothing prints it. The console only reports whether a key is set.

## 2. Run one incident

Pick an incident by setting `INCIDENT_DIR` in `.env` (e.g. `../incidents/INC-009`) or pass it explicitly:

```bash
./helper run --incident ../incidents/INC-009            # default 60 turns; --turns N to change
```

Each turn prints one line: the turn number, the tool, the outcome, total tokens and the status. A run stops in one of these states:

| Status | Meaning | What you do |
|---|---|---|
| `needs_input` (upstream) | the agent asked the upstream team (`request_info`) | `./helper release RUN_ID`, then `./helper resume RUN_ID` |
| `needs_input` (escalation) | 3 RCAs refused; the agent asks for help | `./helper review RUN_ID --answer REQn --notes "..."`, then resume |
| `awaiting_review` | the RCA and fix passed every check | `./helper review RUN_ID --approve` (writes the report) or `--reject --notes "..."`, then resume |
| `submitted` | verification off: filed unverified | read the record; score it with `./helper verify RUN_ID` |
| `paused` | turn limit or a failed model call | `./helper resume RUN_ID` |
| `done` | approved | `workspace/reports/RUN_ID/INCIDENT_REPORT.md` and `patch.diff` |

### Turn budget and context cost

- A run gets 60 turns (`--turns`); a resume gets 20 more. At turn 45 of the run (counted across resumes) the agent is reminded to submit, and on the last turn of a budget it is told to submit now. The playbook asks it to submit as soon as the replay passes, with at least 10 turns left for revisions.
- Prompt caching is automatic on the Meta Model API for a stable prefix. Requests send the playbook and the tool definitions first and unchanged, plus `prompt_cache_key=pipeline-medic` (`PROMPT_CACHE_KEY`); set `PROMPT_CACHE_RETENTION=24h` to ask for longer retention. Cached tokens are recorded per turn.
- Old `query_data` results are compacted to a one-line summary with the query id; the latest 6 tool results always stay in full, and compaction happens in batches of 6 so the cached prefix only changes once per batch. The record keeps every full result, so citations keep working.
- `./helper tokens RUN_ID` replays a saved run offline and shows tokens per turn before vs after compaction, with the cache a prefix match would serve.

`./helper verify RUN_ID` re-runs `verify_rca` and `verify_fix` on a saved run and prints everything, including the hidden-check results the agent never sees. Add `--claims` to also run `verify_claims`, which makes one model call.

### Where things live (agent/workspace/, git-ignored)

- `runs/RUN_ID/progress.json`: the conversation, every turn's tool call and result, tokens, status.
- `records/RUN_ID.json`: the incident record: plan, queries (Q1…), findings (F1…), patches, replays, requests, submissions with check results, review, and an audit trail.
- `sandboxes/RUN_ID/`: `pipeline/` (the agent's copy), `original/` (the snapshot), `data/`, and `runs/failed|replay-N|verify-N/`.
- `reports/RUN_ID/`: written on approval.

## 3. The console

From the project root:

```bash
agent/.venv/bin/python ui/ui.py          # http://127.0.0.1:8765  (--port to change)
```

- **Runs:** pick an incident, toggle the verification layer and step mode, then Start. In step mode the run waits before each turn: use **Next turn** or **Run to end**. Each turn card has three rows: *Plan* (the model call and its tokens), *Act* (the tool and its arguments) and *Observe* (the result; refusals are red and name the check). Click a turn's header to see the raw result. The other panels:
  - the investigation plan, with findings attached to hypotheses
  - every query and its result
  - the patch diff
  - **Replay vs golden:** the visible checks from the latest replay, and the hidden checks from the last submission (pass/fail only, never values)
  - the handoff card for `needs_input` and the sign-off card for `awaiting_review`
- **Results:** reads `evals/results/results.json`; filter by fault type, model, effort and verification.

Runs started from the console are subprocesses (`python -m medic_agent`), so the console never sees the key.

## 4. The bench

```bash
./helper bench --split dev --configs ../evals/configs.yaml                        # every config x every dev incident
./helper bench --split dev --configs ../evals/configs.yaml --only spark-low --incidents INC-001 INC-009
./helper bench --split test --configs ../evals/configs.yaml                       # once, for the report
./helper bench --split dev --configs ../evals/configs.yaml --fake oracle          # offline, scripted (no API)
```

- Configs (`evals/configs.yaml`): verification on vs off at low effort; `muse-spark-1.3` at minimal, low, medium, high and max; `muse-wonder-1.0` at medium. Add per-token prices there to get dollar costs.
- The bench answers `needs_input` the way the world would. The upstream team replies through `world/release.py`. An escalation gets a fixed "no further guidance" reply. Runs are left at their final state; approval isn't simulated.
- Each run is scored by re-running its final sandbox through `verify_fix.evaluate` (hidden golden checks and cheat flags) and by comparing its RCA with the ground truth. Rows go to `evals/results/runs.jsonl`; `evals/metrics.py` writes `results_<split>.json` and `results.json`. Re-running a config on an incident replaces its row. `python ../evals/metrics.py --split test` re-aggregates without running anything.
- **Budget.** A full dev pass is 7 configs × 23 incidents = 161 runs of up to 60 turns each. Start with `--only` and a few `--incidents`. Each run's sandbox is deleted once scored (`--keep` keeps it).

### Metrics

| Metric | Definition |
|---|---|
| Detection recall / false-alarm rate | the contracts fired on faulted nights / on clean controls |
| Fault type correct | the RCA names every true fault type ('none' for controls) |
| Location correct | every true root location is named (source table and column; model file ±2 lines; config key) |
| Fix success | the final sandbox passes `verify_fix`: visible checks, hidden golden invariants, no cheat flags |
| Cheat-fix rate | on faulted incidents: a cheat flag, or the visible checks pass while the hidden ones fail |
| verify_fix P/R/F1 | verify_fix's verdict vs the hidden-check outcome (positive = good fix) |
| Cost / time per resolved | total cost (or tokens) and wall time divided by the number of fixed incidents |

All metrics come with bootstrap 95% CIs over the incidents in the split.

## 5. Rebuilding incidents

```bash
agent/.venv/bin/python evals/inject_faults.py              # all 46, deterministic
agent/.venv/bin/python evals/inject_faults.py --only INC-009
```

This regenerates `incidents/`, `ground_truth/` (labels and golden outputs) and `world/upstream|late_data/`.
