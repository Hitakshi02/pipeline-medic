"""./helper review RUN_ID --approve | --reject --notes "..." | --answer REQ --notes "..." """

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_kit import report, settings  # noqa: E402
from agent_kit.incident_record import RecordError  # noqa: E402


def main() -> None:
    settings.load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--approve", action="store_true")
    group.add_argument("--reject", action="store_true")
    group.add_argument("--answer", metavar="REQUEST_ID")
    parser.add_argument("--notes", default="")
    args = parser.parse_args()
    ws = settings.workspace()
    try:
        if args.approve:
            print(f"approved; report in {report.approve(ws, args.run_id, args.notes)}")
        elif args.reject:
            report.reject(ws, args.run_id, args.notes)
            print("rejected; resume the run so the agent can revise: ./helper resume " + args.run_id)
        else:
            report.answer(ws, args.run_id, args.answer, args.notes)
            print("answered; resume the run: ./helper resume " + args.run_id)
    except (RecordError, FileNotFoundError) as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
