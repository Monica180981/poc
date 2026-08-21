"""Score a case against the team's underwriting scoring framework
(Underwriting_POC.xlsx -> Framework + Bands tabs, loaded via
scoring_framework.py).

Claude's job is deliberately narrow and bounded: for each of the framework's
~19 factors, pick exactly one of that factor's four predefined bands (raw
score 0-3) based on the case's already-extracted findings — never invent a
new band, score, or factor. ALL arithmetic (weighting, totaling) and the
risk-band lookup are done afterward in plain Python, not by the model — a
scoring rubric this consequential must be exactly reproducible, not
LLM-computed.

Scope: Term Life Insurance only (config.settings.PRODUCT_LINE).

Public function:
    score_case(features: dict) -> dict

CLI:
    python src/score_case.py --case CASE-0001
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

import openai  # noqa: E402

from config import settings  # noqa: E402
import scoring_framework  # noqa: E402
import llm_json  # noqa: E402

logger = logging.getLogger("score_case")


_SYSTEM_PROMPT_TEMPLATE = """\
You are scoring a __PRODUCT_LINE__ underwriting case against a fixed scoring
framework. The framework defines a set of factors; each factor has EXACTLY
four possible bands (raw scores 0, 1, 2, or 3), described in plain language
below.

Your job: for EVERY factor listed, choose EXACTLY ONE of its bands based on
the case's already-extracted findings (key fields, missing information, risk
flags, conflicts). Do not invent a band, a score, or a factor that isn't in
the list — copy the band_label text verbatim from the list.

Rules:
- Use only the case findings provided. Never invent facts.
- If the findings don't address a factor at all (no evidence either way),
  choose that factor's most favorable band (raw_score 0) AND set
  evidence_status to "not_addressed" — do not guess adverse information that
  isn't there. Set evidence_status to "assessed" whenever the findings
  actually support a real judgment (favorable or not).
- basis is a short phrase pointing at which finding(s) drove the choice (by
  field/flag name or short description) — not a new citation system; the
  underlying findings already carry their own citations elsewhere.
- Output ONLY a single JSON object matching the schema below. No prose, no
  markdown, no code fences.
"""

OUTPUT_SCHEMA = """\
{
  "factor_scores": [
    {
      "factor": "<exact factor name from the list>",
      "raw_score": 0,
      "band_label": "<exact band label chosen, copied verbatim from the list>",
      "evidence_status": "assessed | not_addressed",
      "basis": "<short phrase: what evidence drove this, or 'no evidence found'>"
    }
  ]
}
"""


def _render_framework_for_prompt(factors: dict[str, list[dict]]) -> str:
    lines = []
    for factor, bands in factors.items():
        lines.append(f"FACTOR: {factor}")
        for b in bands:
            lines.append(f"  [{b['raw_score']}] {b['band_label']}  ({b['interpretation']})")
        lines.append("")
    return "\n".join(lines)


def _render_features_for_prompt(features: dict) -> str:
    """Same compact rendering style as generate_narrative.py, so scoring
    reasons over the already-cited findings rather than raw documents."""
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


def build_user_prompt(features: dict, factors: dict[str, list[dict]]) -> str:
    framework_block = _render_framework_for_prompt(factors)
    findings_block = _render_features_for_prompt(features)
    today = dt.date.today().isoformat()
    return (
        f"Today's date: {today} (use this for any age calculation from a date of birth).\n\n"
        "Score this case against EVERY factor below using EXACTLY this JSON "
        f"structure:\n\n{OUTPUT_SCHEMA}\n"
        "=== SCORING FRAMEWORK (factors and their bands) ===\n"
        f"{framework_block}\n"
        "=== END SCORING FRAMEWORK ===\n\n"
        "=== CASE FINDINGS ===\n"
        f"{findings_block}\n"
        "=== END CASE FINDINGS ==="
    )


def _client() -> openai.OpenAI:
    if not settings.OPENROUTER_API_KEY:
        raise RuntimeError(
            "No OPENROUTER_API_KEY found. Add it to .env (see .env.example)."
        )
    return openai.OpenAI(
        base_url=settings.OPENROUTER_BASE_URL, api_key=settings.OPENROUTER_API_KEY
    )


def _validate_and_normalize(
    factor_scores: list[dict], factors: dict[str, list[dict]]
) -> list[dict]:
    """Guard rails on the model's output — this number drives a real risk
    decision, so we never trust it blindly:
    - An unknown factor name is dropped (logged).
    - An invalid raw_score is clamped to the nearest valid band (logged).
    - weight / weighted_contribution / band_label always come from the
      framework file, never from the model, even if the model echoed them.
    - Any factor the model omitted defaults to its most favorable band,
      marked not_addressed, so the total always covers every factor.
    """
    normalized = []
    seen_factors = set()

    for entry in factor_scores:
        factor = entry.get("factor")
        bands = factors.get(factor)
        if not bands:
            logger.warning("Model returned unknown factor '%s' — dropped.", factor)
            continue

        raw_score = entry.get("raw_score")
        band = next((b for b in bands if b["raw_score"] == raw_score), None)
        if band is None:
            logger.warning(
                "Factor '%s': invalid raw_score %r from model — clamped to nearest valid band.",
                factor, raw_score,
            )
            band = min(bands, key=lambda b: abs(b["raw_score"] - (raw_score or 0)))

        seen_factors.add(factor)
        normalized.append(
            {
                "factor": factor,
                "raw_score": band["raw_score"],
                "band_label": band["band_label"],
                "weight": band["weight"],
                "weighted_contribution": band["raw_score"] * band["weight"],
                "evidence_status": entry.get("evidence_status", "assessed"),
                "basis": entry.get("basis", ""),
            }
        )

    for factor, bands in factors.items():
        if factor in seen_factors:
            continue
        favorable = min(bands, key=lambda b: b["raw_score"])
        normalized.append(
            {
                "factor": factor,
                "raw_score": favorable["raw_score"],
                "band_label": favorable["band_label"],
                "weight": favorable["weight"],
                "weighted_contribution": favorable["raw_score"] * favorable["weight"],
                "evidence_status": "not_addressed",
                "basis": "Model did not return a score for this factor.",
            }
        )
        logger.warning(
            "Factor '%s' missing from model output — defaulted to favorable band.", factor
        )

    return normalized


def score_case(
    features: dict,
    client: openai.OpenAI | None = None,
    model: str | None = None,
    framework_path: Path | None = None,
) -> dict:
    """Score a case's features against the shared scoring framework.

    Returns {case_id, factor_scores, total_score, risk_band,
    suggested_action, _meta}. Only each factor's chosen band comes from
    Claude — total_score and the risk_band/suggested_action lookup are
    deterministic Python, reproducible for the same factor_scores every time.
    """
    client = client or _client()
    model = model or settings.OPENROUTER_MODEL

    framework_entries = scoring_framework.load_framework(framework_path)
    bands_table = scoring_framework.load_bands(framework_path)
    factors = scoring_framework.factors_grouped(framework_entries)

    parsed, response = llm_json.create_and_parse(
        client,
        model=model,
        max_tokens=6000,
        messages=[
            {
                "role": "system",
                "content": _SYSTEM_PROMPT_TEMPLATE.replace(
                    "__PRODUCT_LINE__", settings.PRODUCT_LINE
                ),
            },
            {"role": "user", "content": build_user_prompt(features, factors)},
        ],
    )
    factor_scores = _validate_and_normalize(parsed.get("factor_scores", []), factors)

    total_score = sum(f["weighted_contribution"] for f in factor_scores)
    band = scoring_framework.lookup_band(total_score, bands_table)

    return {
        "case_id": features.get("case_id", "UNKNOWN"),
        "factor_scores": factor_scores,
        "total_score": total_score,
        "risk_band": band["band"],
        "suggested_action": band["suggested_action"],
        "_meta": {
            "model": response.model,
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "framework_source": str(framework_path or settings.SCORING_FRAMEWORK_PATH),
            "usage": {
                "prompt_tokens": getattr(response.usage, "prompt_tokens", None),
                "completion_tokens": getattr(response.usage, "completion_tokens", None),
            }
            if response.usage
            else None,
        },
    }


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
        result = score_case(features, model=args.model)
    except Exception as exc:  # noqa: BLE001 - surface any API/parse failure cleanly
        logger.error("Scoring failed: %s", exc)
        return 1

    out_path = settings.PACKETS_DIR / f"{args.case}_score.json"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    logger.info(
        "%s: total_score=%d, risk_band=%s -> %s",
        args.case, result["total_score"], result["risk_band"], out_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
