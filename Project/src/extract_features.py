"""Turn a case bundle into structured underwriting features via Claude.

Scope: Term Life Insurance only (config.settings.PRODUCT_LINE) — pure
protection, no cash value/investment component.

Takes the case bundle produced by extract_text.py (tagged text chunks) and
asks Claude to return JSON with four things:

  * key_fields          - the underwriting facts present in the documents
  * missing_information - evidence a reviewer would expect but can't find
  * risk_flags          - generic risk signals worth a human's attention
  * conflicts           - places where two or more documents disagree

This is a DRAFT for a human underwriter, not a decision. The prompt uses
generic, public underwriting notions (chronic conditions, lab abnormalities,
missing evidence) rather than any specific carrier's rules. Every extracted
item cites its evidence as {chunk_id, quote} pairs so findings are traceable.

Public function:
    extract_case_features(case_bundle: dict) -> dict

CLI:
    python src/extract_features.py --case CASE-0001
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

logger = logging.getLogger("extract_features")


# Built with a plain-string template + .replace(), not an f-string: the prompt
# body below legitimately contains literal "{chunk_id, quote}" braces (as
# citation-notation prose), which an f-string would try to evaluate as code.
_SYSTEM_PROMPT_TEMPLATE = """\
You are an underwriting assistant for a life insurer's __PRODUCT_LINE__ line
of business. Your job is to read the documents in a single case file and
produce a structured, traceable summary of the facts — NOT a decision. A
human underwriter reviews and edits everything you produce.

__PRODUCT_LINE__ is a pure-protection product with a level term period
and no cash value or investment component. Reason about mortality risk and
standard term-life underwriting evidence only. Do NOT reason about investment
performance, cash surrender value, policy loans, or annuity/longevity
considerations — those don't apply to this product.

Core rules:
- Use only information present in the provided document chunks. Never invent
  facts, values, or dates. If something is not stated, treat it as missing.
- Use generic, widely understood term-life underwriting notions (e.g. chronic
  conditions, abnormal lab values, tobacco use, missing standard evidence).
  Do NOT apply any specific carrier's rate tables or eligibility rules.
- You are flagging things for human attention, not approving or declining.
- Output ONLY a single JSON object matching the requested schema. No prose,
  no markdown, no code fences.

Citations (applies to key_fields, risk_flags, conflicts):
- Every entry cites its evidence as a list of {chunk_id, quote} objects.
- chunk_id must be one of the exact chunk_id strings provided below.
- quote must be a SHORT VERBATIM snippet copied from that chunk's text — the
  exact words that support the entry. Do not paraphrase inside quote.

Key fields — for each, also report:
- basis: "stated" if the value appears directly in a document, or "inferred"
  if you derived it from indirect evidence.
- confidence: "high" (explicit in an authoritative document such as an
  application, APS, or lab), "medium" (present but from a weaker source such
  as an advisor note, or only partially specified), or "low" (inferred).

Risk flags — for each, also report:
- category: one of "medical", "lifestyle", "financial", "driving", "other".
- severity, using this rubric:
  * "low"      = common / well-controlled / minor; routine review only.
  * "moderate" = warrants closer review or additional evidence.
  * "high"     = significant concern; likely escalation or major impact on the
                 risk picture.

Conflicts:
- Report any place where two or more documents disagree or are inconsistent
  (e.g. application states non-smoker but a lab indicates tobacco use; two
  different dates of birth). Cite every side of the conflict.
"""

SYSTEM_PROMPT = _SYSTEM_PROMPT_TEMPLATE.replace("__PRODUCT_LINE__", settings.PRODUCT_LINE)

# The schema we ask Claude to fill. Kept generic and structural on purpose.
OUTPUT_SCHEMA = """\
{
  "case_id": "<the case id>",
  "key_fields": [
    {
      "field": "<snake_case field name, e.g. applicant_name, date_of_birth, face_amount, product, term_length, tobacco_use, diagnosis, lab_result>",
      "value": "<the value exactly as supported by the documents>",
      "basis": "stated | inferred",
      "confidence": "high | medium | low",
      "sources": [{"chunk_id": "<chunk_id>", "quote": "<verbatim snippet>"}]
    }
  ],
  "missing_information": [
    {
      "item": "<what is missing, e.g. 'Tobacco use confirmation', 'Recent EKG'>",
      "why_it_matters": "<one sentence, generic underwriting reasoning>",
      "typically_found_in": "<document type it would usually come from>"
    }
  ],
  "risk_flags": [
    {
      "flag": "<short label, e.g. 'Chronic condition: hypertension'>",
      "category": "medical | lifestyle | financial | driving | other",
      "severity": "low | moderate | high",
      "rationale": "<one sentence, generic reasoning>",
      "sources": [{"chunk_id": "<chunk_id>", "quote": "<verbatim snippet>"}]
    }
  ],
  "conflicts": [
    {
      "description": "<one sentence describing the disagreement between documents>",
      "sources": [{"chunk_id": "<chunk_id>", "quote": "<verbatim snippet>"}]
    }
  ]
}
"""


def _render_chunks(case_bundle: dict) -> str:
    """Render the bundle's chunks into a compact, source-labeled block."""
    lines = []
    for chunk in case_bundle.get("chunks", []):
        lines.append(
            f"[chunk_id: {chunk['chunk_id']}] "
            f"(document_type: {chunk['document_type']} | document: {chunk['document']})"
        )
        lines.append(chunk["text"])
        lines.append("")  # blank line between chunks
    return "\n".join(lines).strip()


def build_user_prompt(case_bundle: dict) -> str:
    case_id = case_bundle.get("case_id", "UNKNOWN")
    chunks_block = _render_chunks(case_bundle)
    return (
        f"Case ID: {case_id}\n\n"
        "Extract underwriting features from the document chunks below and return "
        "them as a single JSON object with EXACTLY this structure:\n\n"
        f"{OUTPUT_SCHEMA}\n"
        "Notes:\n"
        "- Return empty arrays where there is nothing to report.\n"
        "- Every 'sources' entry is a {chunk_id, quote} object; chunk_id must "
        "be taken from the chunks below and quote must be verbatim from that "
        "chunk's text.\n\n"
        "=== DOCUMENT CHUNKS ===\n"
        f"{chunks_block}\n"
        "=== END DOCUMENT CHUNKS ==="
    )


def _source_chunk_id(src) -> str | None:
    """Get the chunk_id from a source, tolerating both the {chunk_id, quote}
    object shape and a bare chunk_id string."""
    if isinstance(src, dict):
        return src.get("chunk_id")
    if isinstance(src, str):
        return src
    return None


def _validate_sources(features: dict, case_bundle: dict) -> set[str]:
    """Return the set of cited chunk_ids that don't exist in the bundle."""
    valid = {c["chunk_id"] for c in case_bundle.get("chunks", [])}
    cited: set[str] = set()
    for section in ("key_fields", "risk_flags", "conflicts"):
        for item in features.get(section, []):
            for src in item.get("sources", []) or []:
                cid = _source_chunk_id(src)
                if cid:
                    cited.add(cid)
    return cited - valid


def _client() -> openai.OpenAI:
    if not settings.OPENROUTER_API_KEY:
        raise RuntimeError(
            "No OPENROUTER_API_KEY found. Add it to .env (see .env.example)."
        )
    return openai.OpenAI(
        base_url=settings.OPENROUTER_BASE_URL,
        api_key=settings.OPENROUTER_API_KEY,
    )


def extract_case_features(
    case_bundle: dict,
    client: openai.OpenAI | None = None,
    model: str | None = None,
) -> dict:
    """Call Claude on a case bundle and return parsed underwriting features.

    Returns a dict with keys: case_id, key_fields, missing_information,
    risk_flags, plus a `_meta` block (model used, timestamp). Raises on API
    or JSON-parse failure so callers can handle it explicitly.
    """
    client = client or _client()
    model = model or settings.OPENROUTER_MODEL
    case_id = case_bundle.get("case_id", "UNKNOWN")

    if not case_bundle.get("chunks"):
        logger.warning("%s has no chunks; returning empty feature set", case_id)
        return {
            "case_id": case_id,
            "key_fields": [],
            "missing_information": [],
            "risk_flags": [],
            "conflicts": [],
            "_meta": {"model": model, "note": "no chunks in bundle"},
        }

    features, response = llm_json.create_and_parse(
        client,
        model=model,
        max_tokens=8000,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(case_bundle)},
        ],
    )

    # Ensure the case_id is authoritative from the bundle, not the model.
    features["case_id"] = case_id
    features.setdefault("key_fields", [])
    features.setdefault("missing_information", [])
    features.setdefault("risk_flags", [])
    features.setdefault("conflicts", [])

    # Audit guard: verify every cited source is a real chunk_id from the bundle.
    # A cited id that isn't in the bundle is a hallucinated citation — record it
    # so the human reviewer (and later audit trail) can see it, don't silently
    # trust it.
    unknown_sources = _validate_sources(features, case_bundle)
    if unknown_sources:
        logger.warning(
            "%s: %d citation(s) reference unknown chunk_ids: %s",
            case_id, len(unknown_sources), ", ".join(sorted(unknown_sources)),
        )

    features["_meta"] = {
        "model": response.model,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "unknown_sources": sorted(unknown_sources),
        "usage": {
            "prompt_tokens": getattr(response.usage, "prompt_tokens", None),
            "completion_tokens": getattr(response.usage, "completion_tokens", None),
        }
        if response.usage
        else None,
    }
    return features


def _load_bundle(case_id: str) -> dict:
    path = settings.PROCESSED_TEXT_DIR / f"{case_id}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No bundle at {path}. Run extract_text.py --case {case_id} first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, help="Case ID (e.g. CASE-0001).")
    parser.add_argument("--model", help="Override the OpenRouter model slug.")
    parser.add_argument(
        "--out",
        type=Path,
        default=settings.PACKETS_DIR,
        help="Directory to write <CASE>_features.json (default: packets/).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"
    )

    try:
        bundle = _load_bundle(args.case)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 1

    try:
        features = extract_case_features(bundle, model=args.model)
    except Exception as exc:  # noqa: BLE001 - surface any API/parse failure cleanly
        logger.error("Feature extraction failed: %s", exc)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    out_path = args.out / f"{args.case}_features.json"
    out_path.write_text(json.dumps(features, indent=2), encoding="utf-8")

    logger.info(
        "%s: %d key field(s), %d missing item(s), %d risk flag(s), %d conflict(s) -> %s",
        features["case_id"],
        len(features["key_fields"]),
        len(features["missing_information"]),
        len(features["risk_flags"]),
        len(features.get("conflicts", [])),
        out_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
