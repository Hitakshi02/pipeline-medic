# Verification layer

With `VERIFICATION=on` (fixed per run when it starts), checks run **inside the tools** at fixed points, so the agent cannot skip them. Each check is one file in `verification/` with one entry point:

```python
run_verification(ctx, work) -> {"passed": bool, "feedback": ..., ...}
```

The agent sees **only the feedback**. Scores, hidden-check details and cheat flags are kept in the incident record for the reviewer, and the console shows them. Ground truth is read only through `verification/ground_truth.load()`, and a test enforces that no other code in `agent/` touches it.

| When | File | Check | On failure |
|---|---|---|---|
| `set_investigation_plan` | `verify_plan.py` | One model call marks which hypothesis kinds each hypothesis tests. The kinds are a fixed taxonomy: source structure, source format, source completeness, duplication, time semantics, model logic, configuration, false alarm. Code computes coverage = kinds the incident needs that the plan covers / kinds needed. | Refused, with feedback that names the missing **kind** ("whether the delivered batch is complete"), never the fault, table, column or fix. On the 3rd try the plan is accepted anyway and the score is kept for the reviewer. |
| `submit_rca` | `verify_rca.py` (code only) | Every root cause and claim cites at least one logged, successful query. Each root location is real: a source table and a column it delivered, a model file and line in the snapshot, or a config key. Fault type `none` goes only with location `none`. A replay ran after the last patch or upstream answer. No request is still open. | Refused with the list of problems. |
| `submit_rca` | `verify_fix.py` (code only) | **The proof.** It re-runs the night from scratch with the agent's models and config but the snapshot's own runner, contracts and tests, so the agent can't weaken them. The visible checks must pass, and the **hidden invariants** must hold: for every mart, the row count, the key set, and sums within 0.5% of the golden outputs. Then cheat-fix detection: changes to checks or the runner, more than 2% of the failed night's rows dropped between raw and staging, delivered values turned into nulls (swallowed errors), hard-coded dates or numbers ≥ 100, `LIMIT`, file access. | Refused with *what* failed ("row count of customer_ltv differs from expected", "the fix drops 41% of the failed night's orders rows …"), never golden values. |
| `submit_rca` | `verify_claims.py` | One model call: is each claim and root-cause explanation supported by its cited queries' SQL and results (up to 20 rows each)? An unreadable judgement never blocks. | Refused with the unsupported statements. |
| 3 refused submissions | `tools/submit_rca.py` | — | Escalates: an `escalation` request opens and the run stops as `needs_input`. A person answers (`./helper review RUN --answer REQn --notes ...`), and the refusal count resets. |
| all pass | `tools/submit_rca.py` | — | `awaiting_review`. The engineer approves (→ `done`; writes `workspace/reports/RUN/patch.diff` and `INCIDENT_REPORT.md`) or rejects with notes (→ back to the agent on resume). |

The checks run in order (`verify_rca` → `verify_fix` → `verify_claims`), and the first failure stops the rest.

## Built-in guards (always on, with or without verification)

These belong to the tools, not to the verification layer:

- `query_data`: one read-only statement (SELECT / WITH), a row limit, a read-only connection with file access disabled. Every query and its result is logged with an id.
- `record_finding`: refused unless every cited query exists and succeeded, and every cited value appears in those results.
- `propose_fix`: only `models/**/*.sql` and `config.json` can change; `contracts/` and `tests/` are refused.
- Until a plan is set, the agent can read and query but cannot record findings, propose fixes or submit.
- The pipeline runner disables DuckDB file access after loading the raw batches, so model SQL cannot read files (such as golden outputs).

## VERIFICATION=off (the baseline)

`set_investigation_plan` and `submit_rca` run without checks. `submit_rca` files the RCA unverified and the run ends as `submitted`. There is no escalation or sign-off. The built-in guards still apply. The bench scores off runs the same way as on runs (it re-runs `verify_fix.evaluate` on the final sandbox), so the two are directly comparable.

## Running the checks by hand

```bash
./helper verify RUN_ID            # verify_rca + verify_fix on the run's sandbox and last submission
./helper verify RUN_ID --claims   # plus verify_claims (one model call)
```
