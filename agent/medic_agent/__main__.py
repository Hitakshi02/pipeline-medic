"""python -m medic_agent run [--turns N] [--incident DIR] | resume RUN_ID [--turns N]"""

from __future__ import annotations

import argparse
import json
import sys

from agent_kit import settings
from agent_kit.model import MissingKeyError, make_client

from .loop import resume_run, start_run


def main() -> None:
    settings.load_env()
    parser = argparse.ArgumentParser(prog="medic_agent")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="start a run on INCIDENT_DIR")
    run.add_argument("--turns", type=int, default=40)
    run.add_argument("--incident", help="incident folder (default: INCIDENT_DIR)")
    resume = sub.add_parser("resume", help="continue a stopped run")
    resume.add_argument("run_id")
    resume.add_argument("--turns", type=int, default=20)
    args = parser.parse_args()
    try:
        client = make_client()
    except MissingKeyError as error:
        sys.exit(str(error))
    if args.command == "run":
        incident = args.incident or str(settings.incident_dir())
        result = start_run(workspace=settings.workspace(), client=client, incident_dir=incident, turns=args.turns)
    else:
        result = resume_run(workspace=settings.workspace(), client=client, run_id=args.run_id, turns=args.turns)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
