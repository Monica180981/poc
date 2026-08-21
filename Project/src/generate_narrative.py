"""Draft the human-readable narrative for a case: a short underwriting brief,
a suggested action path, and a draft (non-binding) rationale — built from the
already-cited structured features (extract_features.py output), not the raw
documents.

Scope: Term Life Insurance only (config.settings.PRODUCT_LINE) — pure
protection, no cash value/investment component. This is a DRAFT for a human
underwriter to review and edit; it is never an automated decision.

Public function:
    generate_narrative(features: dict) -> dict

CLI:
    python src/generate_narrative.py --case CASE-0001
    (reads packets/<CASE>_features.json, writes packets/<CASE>_narrative.json)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # for `config`
sys.path.insert(0, str(Path(__file__).resolve().parent))          # for `llm_json`

import openai  # noqa: E402

from config import settings  # noqa: E402
import llm_json  # noqa: E402

logger = logging.getLogger("generate_narrative")


SYSTEM_PROMPT = f"""\
You are drafting the underwriting brief for a {settings.PRODUCT_LINE} case. A
prior analysis step already extracted structured findings from the case
documents — key fields, missing information, risk flags, and conflicts, each
with its own source citations. Your job is to turn those structured findings
into a short, readable brief for a human underwriter — NOT a decision.

{settings.PRODUCT_LINE} is a pure-protection product with a level term period
and no cash value or investment component. Reason about mortality risk and
standard term-life underwriting evidence only — do not reason about
investment performance, cash surrender value, or policy loans.

Rules:
- Base everything ONLY on the structured findings provided below. Do not
  introduce new facts that aren't already present there.
- Every suggested action and every part of the draft rationale must be
  traceable to specific items already in the findings (reference risk flags,
  missing-information items, or conflicts by name/description — the
  structured findings already carry their own chunk-level citations, so you
  do not need to re-cite raw documents here).
- This is a DRAFT ONLY. A human underwriter must review, edit, and approve
  before anything is final. Do not present the recommendation as settled.
- Output ONLY a single JSON object matching the schema below. No prose
  outside the JSON, no markdown, no code fences.
"""

OUTPUT_SCHEMA = """\
{
  "summary": "<3-6 sentence plain-prose underwriting brief a reviewer can read first>",
  "suggested_action_path": [
    {
      "action": "<concrete next step, e.g. 'Request updated cardiac workup'>",
      "priority": "high | medium | low",
      "rationale": "<one sentence, ties back to a specific flag/missing item>"
    }
  ],
  "draft_rationale": {
    "recommendation": "approve | decline | postpone | refer_to_senior_underwriter",
    "rationale": "<prose explaining the draft reasoning, referencing specific risk flags, missing information, and/or conflicts>"
  }
}
"""


def _render_features_for_prompt(features: dict) -> str:
    """Compact, readable rendering of the structured findings. No raw chunk
    text or citations here — the narrative reasons over the already-derived
    findings, not the source documents."""
    lines = [f"Case ID: {features.get('case_id', 'UNKNOWN')}", ""]

    lines.append("KEY FIELDS:")
    key_fields = features.get("key_fields", [])
    for f in key_fields:
        lines.append(
            f"- {f.get('field')}: {f.get('value')} "
            f"(basis={f.get('basis')}, confidence={f.get('confidence')})"
        )
    if not key_fields:
        lines.append("- (none extracted)")
    lines.append("")

    lines.append("MISSING INFORMATION:")
    missing = features.get("missing_information", [])
    for m in missing:
        lines.append(f"- {m.get('item')}: {m.get('why_it_matters')}")
    if not missing:
        lines.append("- (none)")
    lines.append("")

    lines.append("RISK FLAGS:")
    risk_flags = features.get("risk_flags", [])
    for r in risk_flags:
        lines.append(
            f"- [{r.get('severity')}/{r.get('category')}] {r.get('flag')}: {r.get('rationale')}"
        )
    if not risk_flags:
        lines.append("- (none)")
    lines.append("")

    lines.append("CONFLICTS:")
    conflicts = features.get("conflicts", [])
    for c in conflicts:
        lines.append(f"- {c.get('description')}")
    if not conflicts:
        lines.append("- (none)")

    return "\n".join(lines)


def build_user_prompt(features: dict) -> str:
    findings_block = _render_features_for_prompt(features)
    return (
        "Draft the narrative brief for this case using EXACTLY this JSON "
        f"structure:\n\n{OUTPUT_SCHEMA}\n"
        "=== STRUCTURED FINDINGS ===\n"
        f"{findings_block}\n"
        "=== END STRUCTURED FINDINGS ==="
    )


def _client() -> openai.OpenAI:
    if not settings.OPENROUTER_API_KEY:
        raise RuntimeError(
            "No OPENROUTER_API_KEY found. Add it to .env (see .env.example)."
        )
    return openai.OpenAI(
        base_url=settings.OPENROUTER_BASE_URL,
        api_key=settings.OPENROUTER_API_KEY,
    )


def generate_narrative(
    features: dict,
    client: openai.OpenAI | None = None,
    model: str | None = None,
) -> dict:
    """Call Claude to draft the narrative brief, suggested action path, and
    draft rationale from already-extracted structured features.

    Returns a dict with keys: summary, suggested_action_path, draft_rationale,
    plus a `_meta` block. Raises on API or JSON-parse failure so callers can
    handle it explicitly.
    """
    client = client or _client()
    model = model or settings.OPENROUTER_MODEL

    narrative, response = llm_json.create_and_parse(
        client,
        model=model,
        max_tokens=3000,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(features)},
        ],
    )

    narrative.setdefault("summary", "")
    narrative.setdefault("suggested_action_path", [])
    narrative.setdefault(
        "draft_rationale",
        {"recommendation": "postpone", "rationale": "No draft rationale returned."},
    )
    narrative["_meta"] = {
        "model": response.model,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "usage": {
            "prompt_tokens": getattr(response.usage, "prompt_tokens", None),
            "completion_tokens": getattr(response.usage, "completion_tokens", None),
        }
        if response.usage
        else None,
    }
    return narrative


def _load_features(case_id: str) -> dict:
    path = settings.PACKETS_DIR / f"{case_id}_features.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No features at {path}. Run extract_features.py --case {case_id} first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, help="Case ID (e.g. CASE-0001).")
    parser.add_argument("--model", help="Override the OpenRouter model slug.")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"
    )

    try:
        features = _load_features(args.case)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 1

    try:
        narrative = generate_narrative(features, model=args.model)
    except Exception as exc:  # noqa: BLE001 - surface any API/parse failure cleanly
        logger.error("Narrative generation failed: %s", exc)
        return 1

    out_path = settings.PACKETS_DIR / f"{args.case}_narrative.json"
    out_path.write_text(json.dumps(narrative, indent=2), encoding="utf-8")
    logger.info(
        "%s: recommendation=%s, %d action(s) -> %s",
        args.case,
        narrative["draft_rationale"].get("recommendation"),
        len(narrative["suggested_action_path"]),
        out_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
