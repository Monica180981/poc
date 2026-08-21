# Team Walkthrough — Underwriting Prototype

A plain-English walkthrough of the pipeline: what runs, in what order, where
things are stored, and why. Written for a team demo — see
[pipeline.md](pipeline.md) for the more technical version and
[packet_fields.md](packet_fields.md) / [features_fields.md](features_fields.md)
/ [case_bundle_fields.md](case_bundle_fields.md) for exact field definitions.

**Scope:** Term Life Insurance only (pure protection, no cash value). Every
packet is a **draft** for a human underwriter — nothing here approves,
declines, or binds anything.

---

## The one-sentence version

Raw case documents go in one end → get read, summarized, scored against our
team's risk framework, and turned into a single reviewable packet → an
underwriter looks at that packet in a browser and makes the real decision.

---

## The five steps

| # | Source (input) | Script (what it does) | Output (where it lands) |
|---|---|---|---|
| **1** | `raw_docs/<CASE_ID>/` — the applicant's actual documents (application, APS, labs, MVR, prior underwriting history, advisor notes — PDF/Word/text) | **`extract_text.py`** — opens every document, pulls out the text, splits it into chunks, and tags each chunk with the case ID and what kind of document it came from (Application, APS, Labs, …). No judgment calls here — just reading files. | `processed_text/<CASE_ID>.json` — the **case bundle**: all the raw text, organized and labeled |
| **2** | `processed_text/<CASE_ID>.json` (output of step 1) | **`extract_features.py`** *(Claude prompt #1)* — reads the tagged text and pulls out the actual underwriting facts: applicant details, diagnoses, lab values, driving record, etc. Also flags anything missing that a reviewer would expect, generic risk signals (e.g. a chronic condition), and any place two documents disagree with each other. Every single fact is tied back to the exact sentence it came from. | `packets/<CASE_ID>_features.json` — the **extracted facts**, each with its source |
| **3** | `packets/<CASE_ID>_features.json` (output of step 2) | **`generate_narrative.py`** *(Claude prompt #2)* — turns those facts into a short written brief, a list of concrete next steps (e.g. "request a lab-confirmed tobacco test"), and a draft recommendation (approve / decline / postpone / send to a senior underwriter). This is the "human-readable" layer — reasoning in plain language, not just data. | `packets/<CASE_ID>_narrative.json` — the **narrative brief** |
| **4** | `packets/<CASE_ID>_features.json` (same input as step 3 — steps 3 and 4 don't depend on each other) | **`score_case.py`** *(Claude prompt #3)* + **`Underwriting_POC.xlsx`** — Claude looks at the facts and, for each of our ~19 scoring factors (age, tobacco use, blood pressure, diabetes, etc.), picks which one of that factor's 4 predefined risk levels applies. Then **plain Python does the math** — no AI involved in this part — multiplying each factor's score by its weight, adding everything up, and looking up the resulting risk band (Low / Medium / High / Very High) and its suggested action from our team's spreadsheet. | `packets/<CASE_ID>_score.json` — the **risk score, band, and full factor-by-factor breakdown** |
| **5** | Steps 2–4's outputs (features + narrative + score) | **`generate_packet.py`** — the conductor. Running this one script actually runs steps 1–4 in order automatically, then combines everything into one final file. | `packets/<CASE_ID>.json` — the **final decision-ready packet** |

**Viewing it:** `webapp.py` starts a simple local website (no install needed
beyond what's already set up) that lists every case with its risk band/score/
action at a glance, and lets you click into any case to see the full packet —
summary, facts with citations, missing items, risk flags, the full scoring
breakdown, suggested actions, and the draft recommendation.

---

## "Do we still need the Claude prompts now that we have a scoring spreadsheet?"

**Yes — all three prompts are still doing distinct, necessary jobs.** The
scoring spreadsheet didn't replace anything; it added a new, complementary
layer:

- **Prompt #1** (`extract_features.py`) is the foundation. It's the only
  thing that turns messy documents into structured facts in the first place.
  Nothing downstream — not the narrative, not the score — has anything to
  work with without this step.
- **Prompt #2** (`generate_narrative.py`) gives you the **qualitative**
  picture: a written explanation and specific next steps. Two cases can land
  on the exact same risk score but need completely different follow-up
  actions — that judgment call is what this prompt is for.
- **Prompt #3** (`score_case.py`) gives you the **quantitative** picture: a
  reproducible number. Its only job is matching facts to one of our
  spreadsheet's predefined categories — a judgment call that needs language
  understanding (e.g. deciding "BP 128/82, history of controlled
  hypertension" fits "mildly elevated but controlled," not "normal" or
  "severe"). Once that match is made, Claude's job is done — **all the
  actual math (multiplying, adding, looking up the risk band) is done by
  plain Python, not AI**, so the same facts always produce the same score.

So: the qualitative brief and the quantitative score are two different
answers to two different questions, and both come from Claude reading the
same underlying facts. Neither one makes the other unnecessary.

---

## Quick reference: running it yourself

```bash
cd Project

# One case, start to finish:
python src/generate_packet.py --case CASE-0001

# View the results in a browser:
python src/webapp.py
# then open http://127.0.0.1:5000

set OPENROUTER_API_KEY= #only for cmd users
```

That single `generate_packet.py` command is doing steps 1–5 above for you —
you don't need to run the individual scripts unless you're debugging one
stage specifically.

---

## Where things live, at a glance

```
raw_docs/<CASE_ID>/              ← input: the applicant's actual documents
processed_text/<CASE_ID>.json    ← step 1 output: tagged text
packets/<CASE_ID>_features.json  ← step 2 output: extracted facts
packets/<CASE_ID>_narrative.json ← step 3 output: written brief + actions
packets/<CASE_ID>_score.json     ← step 4 output: risk score + band
packets/<CASE_ID>.json           ← step 5 output: THE final packet
Underwriting_POC.xlsx            ← the scoring rules (owned by the domain team)
```

## Reminder for the walkthrough

Every packet says, in its own words, that it's a draft — not an approval,
decline, or binding decision. The underwriter is always the one who signs
off.
