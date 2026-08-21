"""Scan case folders and log the documents available for each case.

This is a sandbox utility: it inventories what is present under raw_docs/ so we
can see the shape of incoming case files before any extraction, classification,
or decisioning is built. It deliberately contains NO underwriting business
logic — it only enumerates files and reports metadata.

Layout expected:

    raw_docs/
        CASE-0001/
            application.pdf
            aps.pdf
            labs.csv
            ...
        CASE-0002/
            ...

Usage:
    python src/scan_cases.py                 # scan the default raw_docs dir
    python src/scan_cases.py --root some/dir # scan a different location
    python src/scan_cases.py --manifest      # also write a JSON manifest

Run from the project root, or from anywhere — paths resolve via config.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path

# Make the project root importable so `config` resolves regardless of cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402

logger = logging.getLogger("scan_cases")


def _human_size(num_bytes: int) -> str:
    """Return a compact human-readable file size."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


def scan_case(case_dir: Path) -> dict:
    """Inventory a single case folder. Returns a plain dict (no side effects)."""
    documents = []
    for path in sorted(case_dir.rglob("*")):
        if not path.is_file() or settings.is_ignorable_file(path.name):
            continue
        stat = path.stat()
        ext = path.suffix.lower()
        documents.append(
            {
                "name": path.name,
                "relative_path": str(path.relative_to(case_dir)),
                "extension": ext,
                "size_bytes": stat.st_size,
                "size_human": _human_size(stat.st_size),
                "modified": dt.datetime.fromtimestamp(stat.st_mtime).isoformat(
                    timespec="seconds"
                ),
                "type_hint": settings.doc_type_hint(path.name),
                "recognized_extension": ext in settings.KNOWN_DOC_EXTENSIONS,
            }
        )
    return {
        "case_id": case_dir.name,
        "path": str(case_dir),
        "document_count": len(documents),
        "documents": documents,
    }


def scan_all(root: Path) -> list[dict]:
    """Inventory every case folder directly under `root`."""
    if not root.exists():
        logger.error("Raw docs directory does not exist: %s", root)
        return []

    case_dirs = sorted(p for p in root.iterdir() if p.is_dir())
    if not case_dirs:
        logger.warning("No case folders found under %s", root)
        return []

    results = [scan_case(case_dir) for case_dir in case_dirs]
    return results


def log_results(results: list[dict]) -> None:
    """Emit a readable per-case summary to the log."""
    total_docs = sum(r["document_count"] for r in results)
    logger.info("Scanned %d case(s), %d document(s) total", len(results), total_docs)

    for case in results:
        logger.info("-" * 60)
        logger.info("Case %s - %d document(s)", case["case_id"], case["document_count"])
        if not case["documents"]:
            logger.info("  (no documents found)")
            continue
        for doc in case["documents"]:
            flag = "" if doc["recognized_extension"] else "  [unrecognized ext]"
            logger.info(
                "  %-28s %-8s %-30s %s%s",
                doc["relative_path"],
                doc["size_human"],
                doc["type_hint"],
                doc["modified"],
                flag,
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=settings.RAW_DOCS_DIR,
        help="Directory containing per-case sub-folders (default: raw_docs/).",
    )
    parser.add_argument(
        "--manifest",
        action="store_true",
        help="Also write a JSON manifest to processed_text/scan_manifest.json.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    results = scan_all(args.root)
    log_results(results)

    if args.manifest:
        settings.PROCESSED_TEXT_DIR.mkdir(parents=True, exist_ok=True)
        manifest_path = settings.PROCESSED_TEXT_DIR / "scan_manifest.json"
        manifest = {
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "root": str(args.root),
            "cases": results,
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        logger.info("Wrote manifest: %s", manifest_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
