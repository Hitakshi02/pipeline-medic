"""The one place agent/ reads ground truth. It lives outside agent/ (GROUND_TRUTH_DIR,
default ../ground_truth), one <incident-id>.json per incident plus golden/<incident-id>/.
Only the verification checks import this module."""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT = Path(__file__).resolve().parents[2] / "ground_truth"


def load(incident_id: str) -> dict | None:
    """The incident's label with `golden_dir` resolved, or None when it has none."""
    folder = Path(os.environ.get("GROUND_TRUTH_DIR") or DEFAULT).expanduser().resolve()
    path = folder / f"{incident_id}.json"
    if not path.is_file():
        return None
    truth = json.loads(path.read_text())
    truth["golden_dir"] = str(folder / "golden" / incident_id)
    return truth
