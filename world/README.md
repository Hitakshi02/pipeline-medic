# world/

The simulated outside world: the people and systems the agent cannot see.

- `upstream_team.py` answers `request_info` questions from what the team knows about each incident (`upstream/<incident>.json`).
- `release.py RUN_ID` moves a `needs_input` run forward: the team answers the open request and any re-delivered batches (`late_data/<incident>/`) land in that run's sandbox. Then `./helper resume RUN_ID`.
- `upstream/` and `late_data/` are written by `evals/inject_faults.py`. Nothing under `agent/` reads them.
