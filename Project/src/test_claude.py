"""Minimal Claude-via-OpenRouter connectivity check.

Sends one tiny request through the OpenAI SDK pointed at OpenRouter and prints
the reply. Its only job is to confirm the environment is wired up: SDK
installed, key resolvable from .env, and the API reachable. No underwriting
logic here.

The key is read from the environment (loaded from .env by config/settings.py)
— never passed as an argument or hard-coded.

Usage:
    python src/test_claude.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openai  # noqa: E402

from config import settings  # noqa: E402


def main() -> int:
    if not settings.OPENROUTER_API_KEY:
        print(
            "No OPENROUTER_API_KEY found. Add it to .env (see .env.example) and retry.",
            file=sys.stderr,
        )
        return 1

    client = openai.OpenAI(
        base_url=settings.OPENROUTER_BASE_URL,
        api_key=settings.OPENROUTER_API_KEY,
    )

    try:
        response = client.chat.completions.create(
            model=settings.OPENROUTER_MODEL,
            max_tokens=64,
            messages=[{"role": "user", "content": "Reply with exactly: connectivity OK"}],
        )
    except openai.AuthenticationError:
        print(
            "Auth failed. Check OPENROUTER_API_KEY in .env (rotate if it was shared).",
            file=sys.stderr,
        )
        return 1
    except openai.APIConnectionError as exc:
        print(f"Could not reach OpenRouter: {exc}", file=sys.stderr)
        return 1
    except openai.APIStatusError as exc:
        print(f"API error {exc.status_code}: {exc.message}", file=sys.stderr)
        return 1

    reply = (response.choices[0].message.content or "").strip()
    print(f"Model:  {response.model}")
    print(f"Reply:  {reply}")
    if response.usage:
        print(
            f"Tokens: in={response.usage.prompt_tokens} "
            f"out={response.usage.completion_tokens}"
        )
    print("Claude (via OpenRouter) connectivity confirmed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
