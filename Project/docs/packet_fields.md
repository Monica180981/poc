# Decision Packet — Field Reference

Describes every field in `packets/<CASE_ID>.json`, the output of
`src/generate_packet.py` — the final, decision-ready packet that combines the
case features (`extract_features.py`), the narrative (`generate_narrative.py`),
and the risk score (`score_case.py`).

This matches the underwriting output template: summary, extracted facts with
citations, missing-item list, risk flags, conflicts, risk score/band,
suggested action, suggested action path, draft rationale, and a
governance/audit trail. **Every packet is a DRAFT** — a human underwriter must
review, edit, and sign off before anything is final.

## Three different "suggested action" fields — don't confuse them

| Field | Shape | Where it comes from | What it is |
|---|---|---|---|
| `suggested_action` | single string | Deterministic lookup from the scoring framework's Bands sheet, keyed by `risk_score` | A generic, band-level action tied purely to the numeric score (e.g. "Review needed, may require more evidence or clarification.") |
| `suggested_action_path[]` | list of objects | Claude (Stage 3, `generate_narrative.py`) | Concrete, case-specific next steps (e.g. "Order nicotine/cotinine lab test") |
| `draft_rationale.recommendation` | single string | Claude (Stage 3, `generate_narrative.py`) | The holistic draft approve/decline/postpone/refer recommendation, reasoning over all qualitative findings |

## Top level

| Field | Meaning |
|---|---|
| `case_id` | The case this packet belongs to. |
| `product_line` | The product scope this packet was generated under (`config.settings.PRODUCT_LINE`, currently "Term Life Insurance"). |
| `generated_at` | Timestamp of this packet-assembly run. Re-running `generate_packet` fully regenerates the packet (and the underlying bundle/features/narrative) from the current `raw_docs/` state — nothing is merged. |
| `disclaimer` | Fixed reminder that this is an AI-assisted draft, not an approval/decline/binding decision. |
| `summary` | 3–6 sentence plain-prose underwriting brief — read this first. |
| `key_fields` | The underwriting facts found in the documents (from Stage 2). See [features_fields.md](features_fields.md) for the per-field shape (`field`, `value`, `basis`, `confidence`, `sources`). |
| `missing_information` | Evidence a reviewer would expect but can't find (from Stage 2). |
| `risk_flags` | Generic risk signals worth attention, each with `category` + `severity` (from Stage 2). |
| `conflicts` | Places where two or more documents disagree (from Stage 2). |
| `risk_score` | Total weighted score from the scoring framework (Stage 4) — sum of `raw_score * weight` across all factors. Deterministic Python arithmetic, not model output. |
| `risk_band` | `Low`, `Medium`, `High`, or `Very high` — looked up from `risk_score` against the Bands sheet in `Underwriting_POC.xlsx`. |
| `suggested_action` | The band-level action from the Bands sheet (see the table above for how this differs from `suggested_action_path`). |
| `factor_scores` | Full per-factor breakdown that produced `risk_score` — see below. |
| `suggested_action_path` | Concrete next steps for the underwriter — see below. |
| `draft_rationale` | The draft recommendation and its reasoning — see below. |
| `governance` | Audit-trail / review-status block — see below. |
| `_meta` | Run metadata — documents processed, token usage for all three Claude calls. |

## `factor_scores[]`

One entry per factor in the scoring framework (currently 19 — Age, Tobacco
use, BMI/build, Blood pressure, Diabetes, Heart disease, Cancer history,
Respiratory disease, Kidney/liver disease, Mental health/substance use,
Medications, Lab abnormalities, Recent hospitalization, Family history,
Occupation/avocation, Driving record, Financial/application consistency, APS
completeness, Multiple comorbidities). Claude picks the band per factor
(bounded to the framework's exact predefined options); everything else here
is deterministic:

| Field | Meaning |
|---|---|
| `factor` | The framework factor name (e.g. "Blood pressure"). |
| `raw_score` | 0–3, the band Claude chose for this factor. |
| `band_label` | The exact band description from the framework (e.g. "Mildly elevated but controlled"). Always copied from the framework file — never trusted from the model even if it echoed one. |
| `weight` | This factor's weight, from the framework file. |
| `weighted_contribution` | `raw_score * weight` — computed in Python. Contributions sum to `risk_score`. |
| `evidence_status` | `assessed` (the case findings actually support this judgment) or `not_addressed` (no evidence either way — defaults to the most favorable band rather than guessing an adverse one). |
| `basis` | Short phrase naming which finding(s) drove the choice. |

## `suggested_action_path[]`

| Field | Meaning |
|---|---|
| `action` | A concrete next step (e.g. "Request updated cardiac workup"). |
| `priority` | `high`, `medium`, or `low`. |
| `rationale` | One sentence tying the action back to a specific risk flag or missing-information item. |

## `draft_rationale`

| Field | Meaning |
|---|---|
| `recommendation` | `approve`, `decline`, `postpone`, or `refer_to_senior_underwriter`. **A draft only** — not a final decision. |
| `rationale` | Prose explaining the reasoning, referencing specific risk flags, missing information, and/or conflicts. |

## `governance`

| Field | Meaning |
|---|---|
| `status` | Always starts as `"draft - pending underwriter review"`. Intended to be updated by a future review step. |
| `reviewed_by` | Who reviewed/edited this packet. `null` until a human review step is built. |
| `reviewed_at` | When it was reviewed. `null` until a human review step is built. |
| `edit_history` | Placeholder for a future audit log of underwriter edits. Empty until that exists. |
| `unresolved_citation_issues` | Cited `chunk_id`s from the features stage that didn't exist in the bundle (hallucinated citations) — should normally be empty. Carried over from `features._meta.unknown_sources`. |

## `_meta`

| Field | Meaning |
|---|---|
| `documents` | The document manifest from the case bundle (name, document type, extraction status, chunk count) — part of the audit trail: what was actually processed to produce this packet. |
| `chunk_count` | Total chunks across all documents. |
| `feature_extraction` | The `_meta` block from Stage 2 (model, timestamp, token usage, `unknown_sources`). |
| `narrative_generation` | The `_meta` block from Stage 3 (model, timestamp, token usage). |
| `scoring` | The `_meta` block from Stage 4 (model, timestamp, token usage, `framework_source` — the path to the Excel file this score was computed from). |

## Notes

- Output is generative and therefore non-deterministic — exact wording, and
  sometimes the exact count of flags/actions, can vary run to run. Human
  review is the control, not exact reproducibility.
- `key_fields`, `risk_flags`, and `conflicts` retain their full source
  citations from Stage 2 (chunk_id + verbatim quote) — nothing is stripped
  when assembled into the packet, so the audit trail is complete end to end.
- Nothing in this packet is an approval, decline, or rate decision.
