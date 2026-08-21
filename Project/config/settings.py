"""Central configuration for the underwriting prototype sandbox.

All paths are resolved relative to the project root so the scripts work no
matter what directory they are launched from. Keep environment-specific or
tunable values here rather than scattering them across scripts.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Project root = parent of this config/ directory.
ROOT = Path(__file__).resolve().parent.parent

# Load .env from the project root into the environment. This runs on import,
# so any script that does `from config import settings` automatically picks up
# the current key — no arguments, no hard-coded secrets. Change .env once and
# every script uses the new value on its next run.
load_dotenv(ROOT / ".env")

# --- Pipeline directories -------------------------------------------------
# raw_docs/       one sub-folder per case, holding the source documents
# processed_text/ OCR / text-extraction output (later pipeline stages)
# packets/        assembled decision-ready packets (later pipeline stages)
RAW_DOCS_DIR = ROOT / "raw_docs"
PROCESSED_TEXT_DIR = ROOT / "processed_text"
PACKETS_DIR = ROOT / "packets"

# --- Product scope ----------------------------------------------------------
# This prototype is scoped to Term Life Insurance only — pure protection,
# a level term period, no cash value or investment component. NOT annuities
# (opposite risk direction: annuities price longevity risk, life insurance
# prices mortality risk) and not permanent/whole life (no cash value here).
# Every Claude prompt in the pipeline references this constant so the scope
# stays centralized and consistent if it ever changes.
PRODUCT_LINE = "Term Life Insurance"

# --- Claude via OpenRouter --------------------------------------------------
# We reach Claude through OpenRouter using its OpenAI-compatible API, so the
# scripts use the `openai` SDK pointed at OPENROUTER_BASE_URL.
#
# The key is read from the environment (loaded from .env above) — never
# hard-coded. Rotate it by editing .env only.
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Model slug on OpenRouter (override via OPENROUTER_MODEL in .env).
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "anthropic/claude-sonnet-5")

# --- Document-type hints --------------------------------------------------
# Filename keyword -> human label, used only to annotate the scan output.
# This is a display hint, NOT business logic: the scanner still lists every
# file regardless of whether its name matches anything here.
DOC_TYPE_HINTS: dict[str, str] = {
    "application": "Application",
    "aps": "APS (Attending Physician Statement)",
    "mib": "MIB check",
    "mvr": "MVR (motor vehicle record)",
    "lab": "Lab results",
    "ehr": "EHR / clinical summary",
    "clinical": "EHR / clinical summary",
    "rx": "Prescription history",
    "prescription": "Prescription history",
    "prior": "Prior underwriting history",
    "policy": "Policy history",
    "advisor": "Advisor / broker notes",
    "broker": "Advisor / broker notes",
    "email": "Advisor / broker email",
}

# Extensions the pipeline expects to handle downstream.
KNOWN_DOC_EXTENSIONS = {
    ".pdf", ".txt", ".md", ".csv", ".json",
    ".doc", ".docx", ".rtf",
    ".png", ".jpg", ".jpeg", ".tif", ".tiff",  # scanned attachments (OCR later)
}


def is_ignorable_file(filename: str) -> bool:
    """True for editor lock/temp files that are not real case documents.

    Covers Word's `~$file.docx` lock files, LibreOffice/OpenOffice's
    `.~lock.file.docx#` lock files, and hidden dotfiles in general (e.g.
    `.DS_Store`). Shared by the scanner and the text-extraction stage so
    neither one treats an open-editor artifact as a document.
    """
    return filename.startswith(".") or filename.startswith("~$")


def doc_type_hint(filename: str) -> str:
    """Best-effort document-type label from a filename.

    Shared by the scanner and the text-extraction stage so a document is
    tagged the same way everywhere. This is a display/tagging hint, not
    business logic — an unrecognized filename just gets "Unclassified".
    """
    lower = filename.lower()
    for keyword, label in DOC_TYPE_HINTS.items():
        if keyword in lower:
            return label
    return "Unclassified"


# --- Text extraction / chunking -------------------------------------------
# Max characters per chunk when splitting extracted document text.
CHUNK_MAX_CHARS = 1500

# --- Scoring framework -------------------------------------------------------
# Single source of truth for the deterministic risk-scoring rules: the
# "Framework" tab (per-factor score bands + weights) and "Bands" tab (total
# score range -> risk band + suggested action) of the team's shared Excel
# workbook. Read fresh from this file on every run — if the domain owner
# updates the spreadsheet, the pipeline picks it up automatically, no code
# change needed (unless a factor/column is added or renamed).
SCORING_FRAMEWORK_PATH = ROOT / "Underwriting_POC.xlsx"
