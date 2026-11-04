"""Settings from the environment. load_env() reads agent/.env into os.environ without
overriding values already set; nothing here ever prints or logs a value."""

from __future__ import annotations

import os
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = AGENT_DIR.parent
DEFAULT_BASE_URL = "https://api.meta.ai/v1"
DEFAULT_MODEL = "muse-spark-1.3"
EFFORTS = ("minimal", "low", "medium", "high", "max")
_TRUE = {"on", "1", "true", "yes"}


def load_env(path: Path | None = None) -> None:
    path = Path(path or AGENT_DIR / ".env")
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def model() -> str:
    return os.environ.get("MODEL") or DEFAULT_MODEL


def reasoning_effort() -> str | None:
    """REASONING_EFFORT (default low); empty or 'none' sends no reasoning setting."""
    value = os.environ.get("REASONING_EFFORT", "low").strip().lower()
    return None if value in ("", "none") else value


def base_url() -> str:
    return os.environ.get("MODEL_API_BASE_URL") or DEFAULT_BASE_URL


def verification_enabled() -> bool:
    return os.environ.get("VERIFICATION", "on").strip().lower() in _TRUE


def workspace() -> Path:
    """Where runs, records and sandboxes live (WORKSPACE_DIR, default agent/workspace)."""
    value = os.environ.get("WORKSPACE_DIR")
    return Path(value).resolve() if value else AGENT_DIR / "workspace"


def incident_dir() -> Path:
    value = os.environ.get("INCIDENT_DIR", "").strip()
    if not value:
        raise RuntimeError("INCIDENT_DIR is not set: point it at an incident folder in agent/.env")
    path = Path(value).expanduser()
    return path if path.is_absolute() else (AGENT_DIR / path).resolve()


def has_key() -> bool:
    return bool(os.environ.get("MODEL_API_KEY"))
