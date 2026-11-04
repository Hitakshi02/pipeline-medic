"""./helper verify RUN_ID [--claims]: run the RCA and fix checks on a saved run's current
sandbox and last submission, and print every result (including what the agent never sees)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_kit import settings, tools  # noqa: E402


def main() -> None:
    settings.load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument("--claims", action="store_true", help="also run verify_claims (one model call)")
    args = parser.parse_args()
    client = None
    if args.claims:
        from agent_kit.model import make_client
        client = make_client()
    ctx = tools.context(client, settings.workspace(), args.run_id)
    submissions = ctx.record.data["submissions"]
    rca = submissions[-1]["rca"] if submissions else {"root_causes": [], "claims": []}
    from verification import verify_claims, verify_fix, verify_rca
    out = {"verify_rca": verify_rca.run_verification(ctx, rca), "verify_fix": verify_fix.run_verification(ctx, rca)}
    if args.claims:
        out["verify_claims"] = verify_claims.run_verification(ctx, rca)
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
