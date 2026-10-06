"""
Tiny Ollama HTTP client — plain urllib to match the rest of the backend
(see app/ingestion/youtube.py; no `requests`, no `httpx` dependency).

Scope: `generate()` wraps POST /api/generate. Returns the model's text, or
parsed JSON when `want_json=True` (sends `format: "json"` so Ollama forces
JSON-mode output). Deliberately small: synthesis prompts and query-rewrite
callers compose their own strings; this is only the transport.

Design pin for the 8 GB M2 setup: the chosen 3B model is memory-bound and
slow on long inputs, so the synthesis layer must cap input size (ranked
subset of comments, not the whole batch) rather than relying on the client
to chunk. See `OllamaError` → surface failures to the caller instead of
silently retrying; a bad response is more informative than a swallowed one.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from app.config import settings


DEFAULT_TIMEOUT_SEC = 120  # a 3B JSON response on M2 can take ~20-60s; 120s is slack


class OllamaError(RuntimeError):
    """Raised on transport failure or non-2xx. Keeps the status + body so
    callers can distinguish "not running" from "model not pulled" from a
    real inference error."""

    def __init__(self, msg: str, status: int | None = None,
                 body: str | None = None):
        super().__init__(msg)
        self.status = status
        self.body = body


@dataclass(frozen=True)
class OllamaResponse:
    text: str
    model: str
    total_duration_ms: int | None
    eval_count: int | None


def generate(
    prompt: str,
    *,
    model: str | None = None,
    system: str | None = None,
    want_json: bool = False,
    temperature: float = 0.2,
    num_predict: int = 512,
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> OllamaResponse:
    """Send a prompt, return the completion.

    `want_json=True` sets Ollama's `format: "json"` — the server will keep
    sampling until the output parses as JSON (or hits num_predict). Returned
    text is still a string; call `parse_json()` on it to get a dict.

    Low `temperature` (0.2) by default because every caller here is doing
    classification / summarization / query rewriting, not creative writing.
    """
    model = model or settings.ollama_model
    url = settings.ollama_url.rstrip("/") + "/api/generate"

    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": num_predict,
        },
    }
    if system:
        payload["system"] = system
    if want_json:
        payload["format"] = "json"

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise OllamaError(
            f"HTTP {e.code} from {url}", status=e.code, body=body
        ) from e
    except urllib.error.URLError as e:
        raise OllamaError(
            f"could not reach Ollama at {url}: {e.reason}. "
            f"Is `ollama serve` running?"
        ) from e

    text = raw.get("response", "")
    return OllamaResponse(
        text=text,
        model=raw.get("model", model),
        total_duration_ms=(raw.get("total_duration") or 0) // 1_000_000 or None,
        eval_count=raw.get("eval_count"),
    )


def parse_json(text: str) -> Any:
    """Best-effort JSON parse of a model output. Even with `format: "json"`,
    a confused model can emit near-JSON with a trailing newline or wrap it
    in a code fence — strip those before parsing. Raises OllamaError on
    unrecoverable output so callers can log the raw text."""
    s = text.strip()
    # Strip ```json ... ``` fences the model sometimes adds despite format=json
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError as e:
        raise OllamaError(
            f"model returned non-JSON (parse error at pos {e.pos}): "
            f"{text[:300]!r}"
        ) from e


def health() -> dict:
    """GET /api/tags — list installed models. Also doubles as a liveness
    probe (if this works, Ollama is up). Raises OllamaError if not."""
    url = settings.ollama_url.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.load(r)
    except urllib.error.URLError as e:
        raise OllamaError(
            f"could not reach Ollama at {url}: {e.reason}. "
            f"Is `ollama serve` running?"
        ) from e
