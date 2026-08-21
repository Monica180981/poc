# Underwriting Prototype — Sandbox

**Term Life Insurance** underwriting prototype. Turns a messy, multi-source
case file (application, APS, labs, MVR, prior underwriting history, advisor
notes, etc.) into a standardized, decision-ready packet for a human
underwriter — a draft, never an automated decision.

Scope: pure-protection term life only (level term, no cash value/investment
component). See `config/settings.py` → `PRODUCT_LINE`.

Risk scoring follows the team's shared scoring framework
(`Underwriting_POC.xlsx` → **Framework** + **Bands** tabs) — the single source
of truth for factor weights and risk-band thresholds. See `config/settings.py`
→ `SCORING_FRAMEWORK_PATH`.

**New to this project or presenting it to the team?** Start with
[docs/team_walkthrough.md](docs/team_walkthrough.md) — a plain-English,
step-by-step tour (source → script → output at each stage) written for a
walkthrough, not a code read.

## Structure

```
raw_docs/          # input — one sub-folder per case, holding source documents
processed_text/    # output — tagged, chunked text per case (JSON bundles)
packets/           # output — features, narrative, and the final decision packet
config/            # central configuration (paths, model, product scope, doc-type hints, chunking)
src/
  scan_cases.py             # inventory documents available per case (no business logic)
  extract_text.py           # extract + chunk + tag text from PDF/Word/Text -> case bundle JSON
  extract_features.py       # Claude: case bundle -> key fields / missing info / risk flags / conflicts
  generate_narrative.py     # Claude: features -> summary / suggested action path / draft rationale
  scoring_framework.py      # loads Underwriting_POC.xlsx (Framework + Bands tabs) — no LLM involved
  score_case.py             # Claude picks a band per factor; Python computes the score + risk band
  generate_packet.py        # orchestrator: runs all four stages -> full decision-ready packet
  webapp.py                 # Flask viewer for packets (read-only, no editing)
  templates/                # HTML templates for the viewer
  test_claude.py            # one-shot Claude (via OpenRouter) connectivity check
  _generate_sample_docs.py  # dev-only: adds sample PDF/DOCX to CASE-0001 for testing
```

## Setup

```bash
python -m pip install -r requirements.txt
```

Claude is reached through **OpenRouter** (OpenAI-compatible API). Copy
`.env.example` to `.env` and fill in your key — never hard-code it:

```bash
cp .env.example .env
# edit .env: OPENROUTER_API_KEY=sk-or-v1-...
```

`.env` is git-ignored. Every script reads the key from the environment at
startup (via `config/settings.py`) — rotate the key by editing `.env` only.

## Quickest path: generate a full packet in one command

```bash
python src/generate_packet.py --case CASE-0001
```

This runs ingestion → Claude feature extraction → narrative generation →
risk scoring and writes `packets/CASE-0001.json` — the full decision-ready
packet. Then view it:

```bash
python src/webapp.py
# open http://127.0.0.1:5000 in a browser
```

The sections below describe each stage individually, if you want to run or
debug them one at a time.

## Pipeline stages

**1. Scan the case folders** — log what documents each case has (no business logic):

```bash
python src/scan_cases.py            # log a per-case document inventory
python src/scan_cases.py --manifest # also write processed_text/scan_manifest.json
```

**2. Extract text** — pull text out of every PDF / Word / plain-text document,
chunk it, tag each chunk with case ID + document type, and write one
consolidated case bundle JSON per case:

```bash
python src/extract_text.py                  # process every case in raw_docs/
python src/extract_text.py --case CASE-0001 # process a single case
python src/extract_text.py --max-chars 2000 # override chunk size (default 1500)
```

Output: `processed_text/<CASE_ID>.json` — see "Case bundle format" below.
Unsupported files (e.g. scanned images — OCR is a later capability) and
unreadable/corrupt files are recorded with a non-"ok" `extraction_status`
rather than stopping the run.

**3. Extract features** — send a case bundle to Claude and get structured
underwriting features back: key fields, missing information, risk flags, and
cross-document conflicts, each cited to the exact chunk + a verbatim quote.
This is a draft for a human underwriter, not a decision, and uses generic
term-life underwriting notions (not carrier rules):

```bash
python src/extract_features.py --case CASE-0001   # requires the bundle from step 2
python src/extract_features.py --case CASE-0001 --model anthropic/claude-opus-4.8
```

Output: `packets/<CASE_ID>_features.json`. Full field reference:
[docs/features_fields.md](docs/features_fields.md). Importable as a function:
`from src.extract_features import extract_case_features`.

**4. Generate narrative** — turn the structured features into a human-readable
brief, a suggested action path, and a draft (non-binding) recommendation:

```bash
python src/generate_narrative.py --case CASE-0001   # requires the features from step 3
```

Output: `packets/<CASE_ID>_narrative.json`. Importable as a function:
`from src.generate_narrative import generate_narrative`.

**5. Score the case** — against the team's scoring framework
(`Underwriting_POC.xlsx`). Claude picks exactly one predefined band (0–3) per
factor from the framework — it never invents a score. All arithmetic (band
weight, total, and the risk-band lookup) is then computed by plain Python, not
the model, since a number this consequential must be exactly reproducible:

```bash
python src/score_case.py --case CASE-0001   # requires the features from step 3
```

Output: `packets/<CASE_ID>_score.json` — `total_score`, `risk_band`,
`suggested_action`, and the full per-factor `factor_scores` breakdown.
Importable as a function: `from src.score_case import score_case`.

**6. Assemble the packet** — run all four stages above end-to-end and
combine everything into the final decision-ready packet:

```bash
python src/generate_packet.py --case CASE-0001
```

Output: `packets/<CASE_ID>.json`. Full field reference:
[docs/packet_fields.md](docs/packet_fields.md). Importable as a function:
`from src.generate_packet import generate_packet`.

**7. View packets** — a minimal read-only Flask viewer, navigation by case ID
and section heading only (no editing, no complex interactivity). The case
list shows risk band + score + suggested action at a glance, color-coded by
band:

```bash
python src/webapp.py            # serves http://127.0.0.1:5000
python src/webapp.py --port 8000
```

Binds to `127.0.0.1` only and runs with debug mode off — this machine is
shared by the team, so the viewer is never exposed beyond localhost.

**8. Confirm Claude connectivity (optional smoke test):**

```bash
python src/test_claude.py
```

## Case bundle format

Each `processed_text/<CASE_ID>.json` looks like:

```json
{
  "case_id": "CASE-0001",
  "generated_at": "...",
  "documents": [
    {"name": "aps_dr_smith.txt", "document_type": "APS (Attending Physician Statement)",
     "extraction_status": "ok", "chunk_count": 1}
  ],
  "chunk_count": 6,
  "chunks": [
    {
      "chunk_id": "CASE-0001__aps_dr_smith.txt__000",
      "case_id": "CASE-0001",
      "document": "aps_dr_smith.txt",
      "document_type": "APS (Attending Physician Statement)",
      "chunk_index": 0,
      "char_count": 66,
      "text": "APS from Dr. Smith. Dx: controlled hypertension (2019). BP 128/82."
    }
  ]
}
```

`extraction_status` is one of: `ok`, `empty` (no extractable text — likely a
scanned/image-only document), `unsupported` (no extractor for that
extension), or `error` (extraction raised — see the document's `note`).

Full field-by-field reference: [docs/case_bundle_fields.md](docs/case_bundle_fields.md).

## Full pipeline flow

See [docs/pipeline.md](docs/pipeline.md) for the end-to-end diagram (input →
script → output at each stage).

## Adding a case

Create a folder under `raw_docs/` named for the case (e.g. `CASE-0002/`) and
drop its documents inside (`.pdf`, `.docx`, `.txt`, `.md`, `.csv`, `.json`
supported). A sample lives in `raw_docs/CASE-0001/`.
