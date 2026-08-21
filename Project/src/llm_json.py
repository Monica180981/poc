"""Shared helper for pipeline stages that ask Claude for a single JSON object
(extract_features.py, generate_narrative.py, score_case.py).

Handles three failure modes seen in practice:

  - The response is wrapped in a ```json ... ``` fence, or has stray prose
    around the JSON object.
  - Claude occasionally emits a literal, unescaped newline/tab inside a JSON
    string value (e.g. mid-sentence in a "rationale" field) instead of the
    escaped \\n. That is invalid per the JSON spec, and json.loads rejects it
    with "Expecting ',' delimiter" at the point the raw line break appears —
    exactly the error seen during rehearsal. We repair this by escaping
    control characters found strictly inside string literals before
    re-parsing (never touching whitespace between tokens).
  - The call intermittently comes back malformed and simply retrying the
    request once resolves it (confirmed by reproducing the failure live —
    the very next call to the same prompt parsed cleanly).
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger("llm_json")


def _escape_bad_control_chars(text: str) -> str:
    """Escape bare control characters (newline, CR, tab) that appear inside
    JSON string literals, without touching whitespace between tokens."""
    out = []
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            if escaped:
                out.append(ch)
                escaped = False
            elif ch == "\\":
                out.append(ch)
                escaped = True
            elif ch == '"':
                out.append(ch)
                in_string = False
            elif ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                out.append("\\r")
            elif ch == "\t":
                out.append("\\t")
            else:
                out.append(ch)
        else:
            out.append(ch)
            if ch == '"':
                in_string = True
    return "".join(out)


def parse_json_response(text: str) -> dict:
    """Parse a Claude JSON response, tolerating code fences, stray prose
    around the object, and unescaped control characters inside strings.
    Raises the underlying json.JSONDecodeError if nothing works.
    """
    cleaned = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()

    candidates = [cleaned]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(cleaned[start : end + 1])

    last_error: json.JSONDecodeError | None = None
    for candidate in candidates:
        for variant in (candidate, _escape_bad_control_chars(candidate)):
            try:
                return json.loads(variant)
            except json.JSONDecodeError as exc:
                last_error = exc
    raise last_error


def create_and_parse(
    client,
    *,
    model: str,
    messages: list[dict],
    max_tokens: int,
    retries: int = 1,
):
    """Call chat.completions.create, then parse its JSON content.

    Retries the whole request up to `retries` extra times if the response was
    truncated (finish_reason == "length") or its content failed to parse as
    JSON even after repair — both are intermittent model-output issues, not
    code bugs, and a fresh attempt usually succeeds.

    Returns (parsed_dict, response) from the attempt that succeeded. Raises
    the last error if every attempt fails.
    """
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        response = client.chat.completions.create(
            model=model, max_tokens=max_tokens, messages=messages,
        )
        choice = response.choices[0]

        if getattr(choice, "finish_reason", None) == "length":
            last_exc = ValueError(
                "Response was truncated (hit max_tokens) before the JSON was "
                "complete. Increase max_tokens."
            )
            logger.warning(
                "Attempt %d/%d truncated (max_tokens=%d) — retrying.",
                attempt + 1, retries + 1, max_tokens,
            )
            continue

        raw = choice.message.content or ""
        try:
            return parse_json_response(raw), response
        except json.JSONDecodeError as exc:
            last_exc = exc
            logger.warning(
                "Attempt %d/%d returned unparseable JSON (%s) — retrying.",
                attempt + 1, retries + 1, exc,
            )
            continue

    raise last_exc
