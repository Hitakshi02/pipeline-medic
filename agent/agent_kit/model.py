"""The model client and a scripted stand-in for tests.

    client = make_client()            # OpenAI SDK -> https://api.meta.ai/v1 (MODEL_API_KEY)
    client = FakeModel(script=[...])  # tests: same responses.create(...) shape, no network

Both expose client.responses.create(model=..., input=..., instructions=..., tools=...,
reasoning=..., text=...). The key never leaves the SDK client.

Prompt caching (Meta Model API, https://dev.meta.ai/docs/prompt-caching): automatic for the
stable prefix of a request. create() also sends a stable prompt_cache_key (PROMPT_CACHE_KEY,
default "pipeline-medic") and, when PROMPT_CACHE_RETENTION is set (in_memory | 24h), that hint.
The loop keeps the prefix stable: instructions and tool definitions first and unchanged, and
old tool results compacted in batches (agent_kit/compact.py) so earlier turns stop changing.
Cached tokens come back in usage.input_tokens_details.cached_tokens.
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
    """responses.create with the configured model, reasoning effort and prompt-cache hints."""
    kwargs.setdefault("model", settings.model())
    effort = settings.reasoning_effort()
    if effort and "reasoning" not in kwargs:
        kwargs["reasoning"] = {"effort": effort}
    kwargs.setdefault("prompt_cache_key", os.environ.get("PROMPT_CACHE_KEY") or "pipeline-medic")
    retention = os.environ.get("PROMPT_CACHE_RETENTION", "").strip()
    if retention and "prompt_cache_retention" not in kwargs:
        kwargs["prompt_cache_retention"] = retention
    return client.responses.create(**kwargs)


def cached_tokens(usage: Any) -> int:
    """usage.input_tokens_details.cached_tokens (0 when absent)."""
    details = getattr(usage, "input_tokens_details", None)
    if isinstance(details, dict):
        return int(details.get("cached_tokens") or 0)
    return int(getattr(details, "cached_tokens", 0) or 0)


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
class TokenDetails:
    cached_tokens: int = 0


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    input_tokens_details: TokenDetails = field(default_factory=TokenDetails)


CHARS_PER_TOKEN = 4


def request_segments(kwargs: dict) -> list:
    """A request as the ordered text the model reads: instructions, tools, then each input item."""
    segments = [str(kwargs.get("instructions") or ""), json.dumps(kwargs.get("tools") or [], sort_keys=True)]
    items = kwargs.get("input")
    for item in items if isinstance(items, list) else [items]:
        segments.append(item if isinstance(item, str) else json.dumps(item, sort_keys=True, default=str))
    return segments


def estimate_tokens(kwargs: dict) -> int:
    """FakeModel's token count for a request: characters / 4 over everything the model reads."""
    return sum(len(s) for s in request_segments(kwargs)) // CHARS_PER_TOKEN


def shared_prefix_tokens(previous: list, current: list) -> int:
    """Tokens of `current` that repeat `previous` from the start (how a prefix cache matches)."""
    chars = 0
    for a, b in zip(previous, current):
        if a == b:
            chars += len(a)
            continue
        n = 0
        while n < min(len(a), len(b)) and a[n] == b[n]:
            n += 1
        chars += n
        break
    return chars // CHARS_PER_TOKEN


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

    Agent turns report token counts from the request itself (estimate_tokens), and a
    simulated prefix cache: cached_tokens = the part of the request that repeats the
    previous agent request from the start. Judgement calls report fixed small counts.
    """

    def __init__(self, script: list | None = None, judge: Callable[[str], dict] | None = None,
                 exhausted: Any = "I have nothing more to do.") -> None:
        self.script = list(script or [])
        self.judge = judge or (lambda prompt: {})
        self.exhausted = exhausted
        self.calls: list = []
        self.responses = _Responses(self)
        self._n = 0
        self._previous: list = []

    def _respond(self, kwargs: dict) -> FakeResponse:
        self.calls.append(kwargs)
        if not kwargs.get("tools"):
            prompt = kwargs.get("input")
            text = json.dumps(self.judge(prompt if isinstance(prompt, str) else json.dumps(prompt)))
            return FakeResponse(output=[{"type": "message", "role": "assistant",
                                         "content": [{"type": "output_text", "text": text}]}],
                                output_text=text, usage=Usage(50, 20))
        segments = request_segments(kwargs)
        usage = Usage(estimate_tokens(kwargs), 30, TokenDetails(shared_prefix_tokens(self._previous, segments)))
        self._previous = segments
        step = self.script.pop(0) if self.script else self.exhausted
        if callable(step) and not isinstance(step, dict):
            step = step(kwargs)
        if isinstance(step, BaseException):
            raise step
        self._n += 1
        if isinstance(step, str):
            return FakeResponse(output=[{"type": "message", "role": "assistant",
                                         "content": [{"type": "output_text", "text": step}]}],
                                output_text=step, usage=usage)
        call = {"type": "function_call", "call_id": f"call_{self._n}", "name": step["tool"],
                "arguments": json.dumps(step.get("args", {}))}
        return FakeResponse(output=[call], usage=usage)
