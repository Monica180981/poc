"""Assemble the full decision-ready packet for one case.

Runs the whole pipeline end-to-end for a single case_id:
  1. Ingestion       — extract_text.process_case()       (raw_docs -> chunks)
  2. Claude features — extract_features.extract_case_features() (chunks ->
     key fields / missing information / risk flags / conflicts, all cited)
  3. Narrative       — generate_narrative.generate_narrative() (features ->
     summary / suggested action path / draft rationale)
  4. Scoring         — score_case.score_case() (features -> per-factor bands
     from the team's scoring framework, then a deterministic total score and
     risk band — see score_case.py for why the arithmetic is Python, not LLM)

...then combines everything into one packet object matching the underwriting
output template: summary, extracted facts with source citations, missing-item
list, risk flags, risk score/band, suggested action path, draft rationale,
and a governance/audit-trail block.

Scope: Term Life Insurance only (config.settings.PRODUCT_LINE).

IMPORTANT: every packet is a DRAFT. Nothing here approves, declines, or binds
anything — a human underwriter must review, edit, and sign off before any
action is taken.

Public function:
    generate_packet(case_id: str, model: str | None = None) -> dict

CLI:
    python src/generate_packet.py --case CASE-0001
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))          # for `config`
sys.path.insert(0, str(_PROJECT_ROOT / "src"))  # for sibling modules below

from config import settings  # noqa: E402
import extract_text  # noqa: E402
import extract_features  # noqa: E402
import generate_narrative  # noqa: E402
import score_case  # noqa: E402

logger = logging.getLogger("generate_packet")

DISCLAIMER = (
    "This packet is an AI-assisted DRAFT for underwriter review. It is not "
    "an approval, decline, or binding decision."
)


def generate_packet(case_id: str, model: str | None = None) -> dict:
    """Run ingestion + Claude feature extraction + narrative generation for
    one case, and return the assembled packet (also written to
    packets/<case_id>.json).
    """
    case_dir = settings.RAW_DOCS_DIR / case_id
    if not case_dir.exists():
        raise FileNotFoundError(f"No raw_docs folder for case '{case_id}': {case_dir}")

    # 1. Ingestion — always re-run so the packet reflects the current
    # raw_docs state, not a stale prior extraction.
    bundle = extract_text.process_case(case_dir, settings.CHUNK_MAX_CHARS)
    settings.PROCESSED_TEXT_DIR.mkdir(parents=True, exist_ok=True)
    (settings.PROCESSED_TEXT_DIR / f"{case_id}.json").write_text(
        json.dumps(bundle, indent=2), encoding="utf-8"
    )
    logger.info(
        "Ingestion: %d document(s), %d chunk(s)",
        len(bundle["documents"]), bundle["chunk_count"],
    )

    # 2. Claude feature extraction
    features = extract_features.extract_case_features(bundle, model=model)
    settings.PACKETS_DIR.mkdir(parents=True, exist_ok=True)
    (settings.PACKETS_DIR / f"{case_id}_features.json").write_text(
        json.dumps(features, indent=2), encoding="utf-8"
    )
    logger.info(
        "Feature extraction: %d key field(s), %d risk flag(s), %d conflict(s)",
        len(features.get("key_fields", [])),
        len(features.get("risk_flags", [])),
        len(features.get("conflicts", [])),
    )

    # 3. Narrative generation
    narrative = generate_narrative.generate_narrative(features, model=model)
    logger.info(
        "Narrative: recommendation=%s, %d suggested action(s)",
        narrative.get("draft_rationale", {}).get("recommendation"),
        len(narrative.get("suggested_action_path", [])),
    )

    # 4. Scoring against the team's framework (Underwriting_POC.xlsx)
    score_result = score_case.score_case(features, model=model)
    settings.PACKETS_DIR.mkdir(parents=True, exist_ok=True)
    (settings.PACKETS_DIR / f"{case_id}_score.json").write_text(
        json.dumps(score_result, indent=2), encoding="utf-8"
    )
    logger.info(
        "Scoring: total_score=%d, risk_band=%s",
        score_result["total_score"], score_result["risk_band"],
    )

    # 5. Assemble the packet — matches the POC's output-packet template:
    # summary, extracted facts + citations, missing-item list, risk flags,
    # risk score/band, suggested action path, draft rationale,
    # governance/audit trail.
    packet = {
        "case_id": case_id,
        "product_line": settings.PRODUCT_LINE,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "disclaimer": DISCLAIMER,
        "summary": narrative.get("summary", ""),
        "key_fields": features.get("key_fields", []),
        "missing_information": features.get("missing_information", []),
        "risk_flags": features.get("risk_flags", []),
        "conflicts": features.get("conflicts", []),
        "risk_score": score_result["total_score"],
        "risk_band": score_result["risk_band"],
        "suggested_action": score_result["suggested_action"],
        "factor_scores": score_result["factor_scores"],
        "suggested_action_path": narrative.get("suggested_action_path", []),
        "draft_rationale": narrative.get(
            "draft_rationale", {"recommendation": "postpone", "rationale": ""}
        ),
        "governance": {
            "status": "draft - pending underwriter review",
            "reviewed_by": None,
            "reviewed_at": None,
            "edit_history": [],
            "unresolved_citation_issues": features.get("_meta", {}).get(
                "unknown_sources", []
            ),
        },
        "_meta": {
            "documents": bundle["documents"],
            "chunk_count": bundle["chunk_count"],
            "feature_extraction": features.get("_meta", {}),
            "narrative_generation": narrative.get("_meta", {}),
            "scoring": score_result.get("_meta", {}),
        },
    }

    out_path = settings.PACKETS_DIR / f"{case_id}.json"
    out_path.write_text(json.dumps(packet, indent=2), encoding="utf-8")
    logger.info("Packet assembled -> %s", out_path)
    return packet


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, help="Case ID (e.g. CASE-0001).")
    parser.add_argument("--model", help="Override the OpenRouter model slug.")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"
    )

    try:
        generate_packet(args.case, model=args.model)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 1
    except Exception as exc:  # noqa: BLE001 - surface any pipeline failure cleanly
        logger.error("Packet generation failed: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
