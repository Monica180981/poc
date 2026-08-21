# Pipeline Overview

End-to-end flow for the Term Life Insurance underwriting prototype. Each
stage's output file is the next stage's input file.

```
raw_docs/<CASE>/   processed_text/<CASE>.json   packets/<CASE>_features.json
   (source   ──►         (case bundle)      ──►      (case features)
   docs)          extract_text.py            extract_features.py
                                                            │
                                        ┌───────────────────┴───────────────────┐
                                        ▼                                       ▼
                          packets/<CASE>_narrative.json          packets/<CASE>_score.json
                              (narrative brief)                    (risk score + band)
                          generate_narrative.py                       score_case.py
                                        └───────────────────┬───────────────────┘
                                                             ▼
                                                   packets/<CASE>.json
                                                 (decision-ready packet)

generate_packet.py runs all four stages above in sequence for one case_id.
Narrative and scoring both depend only on the features file, not on each
other — they're logically independent, both folded into the final packet.
```

## Stage 1 — Ingestion

| | |
|---|---|
| **Input** | `raw_docs/<CASE_ID>/` — the raw source documents for a case (PDF, Word, TXT, CSV). One folder per case. |
| **Script** | `src/extract_text.py` (function: `process_case`) |
| **What it does** | Opens each document, extracts its text, splits it into chunks, and tags every chunk with `case_id`, source document, and document type (Application, APS, Labs, MVR, …). Skips editor lock/hidden files. Flags unreadable or unsupported files (e.g. scanned images — OCR not yet implemented) instead of crashing. No underwriting logic — pure technical ingestion. |
| **Output** | `processed_text/<CASE_ID>.json` — the **case bundle**. Field reference: [case_bundle_fields.md](case_bundle_fields.md). |

## Stage 2 — Claude feature extraction

| | |
|---|---|
| **Input** | `processed_text/<CASE_ID>.json` (the case bundle from Stage 1). |
| **Script** | `src/extract_features.py` (function: `extract_case_features`) |
| **What it does** | Sends the tagged chunks to Claude and gets back structured JSON: key underwriting fields (with confidence/basis), missing information, risk flags (with category/severity), and cross-document conflicts. Every finding cites its evidence as `{chunk_id, quote}` pairs; citations are validated against the bundle (hallucinated ones surface in `_meta.unknown_sources`). Uses generic term-life underwriting notions — no carrier rules. This is a draft input for a human, not a decision. |
| **Output** | `packets/<CASE_ID>_features.json` — the **case features**. Field reference: [features_fields.md](features_fields.md). |

## Stage 3 — Narrative generation

| | |
|---|---|
| **Input** | `packets/<CASE_ID>_features.json` (the features from Stage 2). |
| **Script** | `src/generate_narrative.py` (function: `generate_narrative`) |
| **What it does** | Turns the structured findings into a short prose brief, a suggested action path (concrete next steps with priority + rationale), and a draft (non-binding) recommendation — approve / decline / postpone / refer to senior underwriter — each traceable back to specific findings from Stage 2. |
| **Output** | `packets/<CASE_ID>_narrative.json` — the **narrative**. |

## Stage 4 — Risk scoring

| | |
|---|---|
| **Input** | `packets/<CASE_ID>_features.json` (the features from Stage 2). Also reads `Underwriting_POC.xlsx` (Framework + Bands tabs) via `scoring_framework.py`. |
| **Script** | `src/score_case.py` (function: `score_case`) |
| **What it does** | For each of the framework's ~19 factors, Claude picks exactly one of that factor's four predefined bands based on the case findings — bounded choice, never a freeform score. Then plain Python (no LLM) multiplies each factor's `raw_score * weight`, sums them into a `total_score`, and looks up the `risk_band` + `suggested_action` from the Bands sheet by range. A number this consequential is computed deterministically, not by the model. |
| **Output** | `packets/<CASE_ID>_score.json` — `total_score`, `risk_band`, `suggested_action`, and the full `factor_scores` breakdown. |

## Stage 5 — Packet assembly (orchestrator)

| | |
|---|---|
| **Input** | A `case_id`; internally re-runs Stages 1–4 so the packet always reflects the current `raw_docs/` state. |
| **Script** | `src/generate_packet.py` (function: `generate_packet(case_id)`) |
| **What it does** | Runs ingestion → feature extraction → narrative generation → risk scoring, then assembles everything into one packet object matching the underwriting output template: summary, extracted facts with citations, missing-item list, risk flags, conflicts, risk score/band, suggested action, suggested action path, draft rationale, and a governance/audit-trail block. |
| **Output** | `packets/<CASE_ID>.json` — the **decision-ready packet**. Field reference: [packet_fields.md](packet_fields.md). |

## Viewing a packet

| | |
|---|---|
| **Script** | `src/webapp.py` |
| **What it does** | A minimal read-only Flask viewer. `/` lists every case with a packet — case ID, risk band (color-coded), risk score, and suggested action, for a quick glance across cases. `/case/<CASE_ID>` renders the full packet with a prominent score banner plus section-heading navigation (including a full risk-score factor breakdown). No editing, no complex interactivity. Binds to `127.0.0.1` only (this machine is shared by the team). |
| **Run** | `python src/webapp.py` → open `http://127.0.0.1:5000` |

## Supporting scripts (not in the main chain)

| Script | Role |
|---|---|
| `src/scan_cases.py` | Visibility only — inventories/logs what documents each case has. Doesn't feed the pipeline. |
| `src/test_claude.py` | One-off connectivity smoke test to OpenRouter/Claude. |
| `src/_generate_sample_docs.py` | Dev-only — creates sample PDF/DOCX for testing. |
| `src/scoring_framework.py` | Loads `Underwriting_POC.xlsx` (Framework + Bands tabs). No LLM involved — just an Excel reader. Single source of truth for scoring rules. |
| `config/settings.py` | Shared config all scripts import — paths, model, `PRODUCT_LINE`, `SCORING_FRAMEWORK_PATH`, doc-type hints, chunk size. |

## One-line version

> Read raw case files from **`raw_docs/`** → **`extract_text.py`** tags them into a **case bundle** → **`extract_features.py`** turns that into cited **features** → **`generate_narrative.py`** turns those into a **brief + action path + draft rationale**, while **`score_case.py`** independently turns them into a **risk score + band** against the team's Excel framework → **`generate_packet.py`** combines all of it into the final **decision-ready packet**, viewable via **`webapp.py`**.

## Human-in-the-loop, always

Every packet carries a `disclaimer` and a `governance` block whose `status`
is `"draft - pending underwriter review"`. Nothing produced by this pipeline
approves, declines, or binds anything — a human underwriter must review, edit,
and sign off before any action is taken.
