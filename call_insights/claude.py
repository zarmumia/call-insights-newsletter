"""Minimal Anthropic Messages API client with retries (no SDK dependency)."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

RETRYABLE_STATUS = {429, 500, 502, 503, 504, 529}


class ClaudeClient:
    def __init__(self, api_key: str | None = None, model: str | None = None, network_retries: int = 3,
                 backoff: float = 1.0, sleep: Callable[[float], None] = time.sleep):
        self.api_key = api_key or os.environ["ANTHROPIC_API_KEY"]
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
        self.network_retries = network_retries
        self.backoff = backoff
        self._sleep = sleep

    def complete(self, prompt: str, max_tokens: int = 1500) -> str:
        for attempt in range(self.network_retries + 1):
            try:
                return self._request(prompt, max_tokens)
            except urllib.error.HTTPError as e:
                if e.code not in RETRYABLE_STATUS or attempt == self.network_retries:
                    raise
            except (urllib.error.URLError, TimeoutError):
                if attempt == self.network_retries:
                    raise
            self._sleep(self.backoff * 2 ** attempt)
        raise RuntimeError("unreachable")

    def _request(self, prompt: str, max_tokens: int) -> str:
        body = {"model": self.model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(body).encode(),
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.load(r)
        return "".join(block.get("text", "") for block in data["content"])


def extract_json(raw: str) -> Any:
    """Models sometimes wrap JSON in prose or code fences. Pull out the first object or array."""
    match = re.search(r"(\{.*\}|\[.*\])", raw, re.DOTALL)
    if not match:
        raise ValueError("No JSON found in model output")
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}") from e


def ask_json(client: ClaudeClient, prompt: str, validate: Callable[[Any], Any], retries: int = 2,
             max_tokens: int = 1500) -> Any:
    """Ask, parse, validate; re-ask on invalid output. Bad data never leaves this function."""
    last: ValueError | None = None
    for _ in range(retries + 1):
        try:
            return validate(extract_json(client.complete(prompt, max_tokens)))
        except ValueError as e:
            last = e
    assert last is not None
    raise last
