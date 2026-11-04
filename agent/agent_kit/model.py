"""The model client and a scripted stand-in for tests.

    client = make_client()            # OpenAI SDK -> https://api.meta.ai/v1 (MODEL_API_KEY)
    client = FakeModel(script=[...])  # tests: same responses.create(...) shape, no network

Both expose client.responses.create(model=..., input=..., instructions=..., tools=...,
reasoning=..., text=...). The key never leaves the SDK client.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable

from . import settings

MAX_RETRIES = 4
TIMEOUT_SECONDS = 180


class MissingKeyError(RuntimeError):
    pass


def make_client():
    from openai import OpenAI

    key = os.environ.get("MODEL_API_KEY", "")
    if not key:
        raise MissingKeyError("MODEL_API_KEY is not set: add it to agent/.env")
    return OpenAI(api_key=key, base_url=settings.base_url(), max_retries=MAX_RETRIES, timeout=TIMEOUT_SECONDS)


def create(client, **kwargs):
    """responses.create with the configured model and reasoning effort."""
    kwargs.setdefault("model", settings.model())
    effort = settings.reasoning_effort()
    if effort and "reasoning" not in kwargs:
        kwargs["reasoning"] = {"effort": effort}
    return client.responses.create(**kwargs)


def json_call(client, prompt: str) -> dict | None:
    """One judgement call that must answer with a JSON object; None if unreadable."""
    response = create(client, input=prompt, text={"format": {"type": "json_object"}})
    try:
        value = json.loads(response.output_text)
    except (TypeError, ValueError, AttributeError):
        return None
    return value if isinstance(value, dict) else None


# ---- FakeModel ----------------------------------------------------------------------


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class FakeResponse:
    output: list
    output_text: str = ""
    usage: Usage = field(default_factory=Usage)


class _Responses:
    def __init__(self, owner: "FakeModel") -> None:
        self.owner = owner

    def create(self, **kwargs) -> FakeResponse:
        return self.owner._respond(kwargs)


class FakeModel:
    """A scripted model. Agent turns (calls offering `tools`) pop the next script step;
    judgement calls (no tools) go to `judge(prompt) -> dict`.

    A step is {"tool": name, "args": {...}}, a plain string (a text answer, no tool),
    an Exception instance (the call fails), or a callable(kwargs) returning a step.
    When the script runs out the model answers with `exhausted` (default: text).
    """

    def __init__(self, script: list | None = None, judge: Callable[[str], dict] | None = None,
                 exhausted: Any = "I have nothing more to do.") -> None:
        self.script = list(script or [])
        self.judge = judge or (lambda prompt: {})
        self.exhausted = exhausted
        self.calls: list = []
        self.responses = _Responses(self)
        self._n = 0

    def _respond(self, kwargs: dict) -> FakeResponse:
        self.calls.append(kwargs)
        if not kwargs.get("tools"):
            prompt = kwargs.get("input")
            text = json.dumps(self.judge(prompt if isinstance(prompt, str) else json.dumps(prompt)))
            return FakeResponse(output=[{"type": "message", "role": "assistant",
                                         "content": [{"type": "output_text", "text": text}]}],
                                output_text=text, usage=Usage(50, 20))
        step = self.script.pop(0) if self.script else self.exhausted
        if callable(step) and not isinstance(step, dict):
            step = step(kwargs)
        if isinstance(step, BaseException):
            raise step
        self._n += 1
        if isinstance(step, str):
            return FakeResponse(output=[{"type": "message", "role": "assistant",
                                         "content": [{"type": "output_text", "text": step}]}],
                                output_text=step, usage=Usage(100, 10))
        call = {"type": "function_call", "call_id": f"call_{self._n}", "name": step["tool"],
                "arguments": json.dumps(step.get("args", {}))}
        return FakeResponse(output=[call], usage=Usage(120, 30))
