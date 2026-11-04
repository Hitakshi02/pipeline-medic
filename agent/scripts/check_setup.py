"""./helper setup: check the incidents exist and the model key works (never prints the key)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_kit import settings  # noqa: E402
from agent_kit.model import MissingKeyError, make_client  # noqa: E402


def main() -> None:
    settings.load_env()
    incidents = sorted((settings.PROJECT_DIR / "incidents").glob("INC-*"))
    print(f"incidents: {len(incidents)}")
    try:
        incident = settings.incident_dir()
        print(f"INCIDENT_DIR: {incident.name} ({'found' if (incident / 'alert.md').is_file() else 'MISSING'})")
    except RuntimeError as error:
        print(error)
    print(f"model: {settings.model()} · effort: {settings.reasoning_effort()} · "
          f"verification: {'on' if settings.verification_enabled() else 'off'}")
    try:
        client = make_client()
    except MissingKeyError as error:
        print(error)
        return
    try:
        available = {m.id for m in client.models.list()}
    except Exception as error:
        print(f"model API check failed: {type(error).__name__}")
        sys.exit(1)
    wanted = settings.model()
    print(f"MODEL_API_KEY works: {wanted} {'is' if wanted in available else 'is NOT'} available")


if __name__ == "__main__":
    main()
