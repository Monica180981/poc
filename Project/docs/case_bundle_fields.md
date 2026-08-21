# Case Bundle — Field Reference

Describes every field in `processed_text/<CASE_ID>.json`, the output of
`src/extract_text.py`. This is the consolidated, tagged-chunk bundle for one
case — pure technical ingestion, no underwriting logic.

## Top level

| Field | Meaning |
|---|---|
| `case_id` | The case folder's name under `raw_docs/` (e.g. `CASE-0001`). |
| `generated_at` | Timestamp of this extraction run — the bundle is fully regenerated (overwritten) every run, not merged. |
| `documents` | One entry per source file found in the case folder, describing what happened when it was processed. |
| `chunk_count` | Total number of chunks across every document in the case. |
| `chunks` | Every tagged text chunk from every document in the case, flattened into one array. |

## `documents[]` — one entry per source file

| Field | Meaning |
|---|---|
| `name` | The source filename (e.g. `aps_dr_smith.txt`). |
| `document_type` | Best-effort label from the filename (Application, APS, Lab results, etc.) — a display/tagging hint, not a guarantee. |
| `extraction_status` | How extraction went — see the status table below. |
| `chunk_count` | How many chunks this specific document produced. |
| `note` | Present only when `extraction_status` isn't `"ok"` — explains why (error message, or why it was skipped). |

### `extraction_status` values

| Value | Meaning |
|---|---|
| `ok` | Text was extracted and chunked normally. |
| `empty` | The file opened fine but contained no extractable text (e.g. a scanned/image-only document with no text layer). |
| `unsupported` | No extractor exists for this file type yet (e.g. `.png`/`.jpg` — OCR is a later capability). |
| `error` | Extraction raised an exception (e.g. a corrupt or unreadable file) — see `note` for the reason. |

## `chunks[]` — one entry per chunk of text

| Field | Meaning |
|---|---|
| `chunk_id` | Globally unique ID for this chunk: `{case_id}__{document}__{chunk_index}`. |
| `case_id` | Which case this chunk belongs to (repeated here so a chunk is self-describing on its own). |
| `document` | Which source file this chunk came from. |
| `document_type` | Same type label as on the parent document entry. |
| `chunk_index` | Position of this chunk *within its own document*, starting at 0. Only goes above 0 when a document's text exceeds the chunk size limit and gets split into multiple pieces. |
| `char_count` | Number of characters in this chunk's `text`. |
| `text` | The actual extracted text for this chunk. |

## Notes

- Chunking splits on paragraph boundaries, capped at `CHUNK_MAX_CHARS` (default 1500, see `config/settings.py`) — a document under that size always produces exactly one chunk (`chunk_index: 0`).
- Hidden/editor lock files (`.~lock.*#`, `~$*.docx`) are silently excluded — they are never listed as documents.
- Nothing in this file is an underwriting decision or risk judgment — it is the raw material later pipeline stages (summarization, key-field extraction, risk flagging) will consume.
