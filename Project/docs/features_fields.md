# Case Features — Field Reference

Describes every field in `packets/<CASE_ID>_features.json`, the output of
`src/extract_features.py`. This is Claude's structured, traceable read of a
case bundle — a DRAFT for a human underwriter, not a decision. It uses generic
underwriting notions, not any carrier's rules.

## Top level

| Field | Meaning |
|---|---|
| `case_id` | The case this feature set belongs to (taken authoritatively from the bundle). |
| `key_fields` | The underwriting facts found in the documents. |
| `missing_information` | Evidence a reviewer would expect but that isn't present. |
| `risk_flags` | Generic risk signals worth a human's attention. |
| `conflicts` | Places where two or more documents disagree. |
| `_meta` | Run metadata — see below. |

## Citations (`sources`)

Every entry in `key_fields`, `risk_flags`, and `conflicts` carries a `sources`
array. Each source is an object:

| Field | Meaning |
|---|---|
| `chunk_id` | The exact chunk the evidence came from (matches a `chunk_id` in the case bundle). |
| `quote` | A short verbatim snippet copied from that chunk — the words that support the entry. |

Cited chunk_ids are validated against the bundle; any that don't exist are
listed in `_meta.unknown_sources` (a hallucinated-citation guard).

## `key_fields[]`

| Field | Meaning |
|---|---|
| `field` | Snake_case field name (e.g. `applicant_name`, `date_of_birth`, `face_amount`, `tobacco_use`, `diagnosis`). |
| `value` | The value as supported by the documents. |
| `basis` | `stated` (appears directly in a document) or `inferred` (derived from indirect evidence). |
| `confidence` | `high` (explicit in an authoritative doc), `medium` (weaker source or partial), `low` (inferred). |
| `sources` | Evidence — see Citations above. |

## `missing_information[]`

| Field | Meaning |
|---|---|
| `item` | What is missing (e.g. "Tobacco use confirmation", "Recent EKG"). |
| `why_it_matters` | One-sentence generic underwriting reasoning. |
| `typically_found_in` | The document type this evidence would normally come from. |

## `risk_flags[]`

| Field | Meaning |
|---|---|
| `flag` | Short label (e.g. "Chronic condition: hypertension"). |
| `category` | `medical`, `lifestyle`, `financial`, `driving`, or `other`. |
| `severity` | `low` (common/well-controlled/minor), `moderate` (closer review / more evidence), `high` (significant concern; likely escalation). |
| `rationale` | One-sentence generic reasoning. |
| `sources` | Evidence — see Citations above. |

## `conflicts[]`

| Field | Meaning |
|---|---|
| `description` | One sentence describing the disagreement between documents. |
| `sources` | Every side of the conflict, each with its `chunk_id` + `quote`. |

## `_meta`

| Field | Meaning |
|---|---|
| `model` | The model that produced this output. |
| `generated_at` | Timestamp of the extraction run. |
| `unknown_sources` | Cited chunk_ids not found in the bundle (should be empty; non-empty = hallucinated citation to review). |
| `usage` | Prompt/completion token counts for the call. |

## Notes

- Output is generative and therefore non-deterministic — exact wording and the
  number of flags/conflicts can vary run to run. Human review is the control.
- Nothing here is an approval, decline, or rate decision. It is structured
  input for the underwriter and for later packet-assembly stages.
