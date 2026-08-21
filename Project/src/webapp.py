"""Minimal Flask viewer for underwriting packets.

Read-only, no login, no editing — navigation by case_id and section headings
only, no complex interactivity, as scoped. This is a viewer for the packets
generate_packet.py produces; it does not run any pipeline stage itself.

Security note: this runs on a machine shared by the team, so it binds to
127.0.0.1 (localhost) only and keeps Flask's debug mode OFF — the Werkzeug
debugger allows arbitrary code execution from the browser and must never run
on a shared box.

Usage:
    python src/webapp.py                 # serves http://127.0.0.1:5000
    python src/webapp.py --port 8000
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, abort, render_template  # noqa: E402

from config import settings  # noqa: E402

app = Flask(__name__)


_INTERMEDIATE_SUFFIXES = ("_features", "_narrative", "_score")


def _list_case_summaries() -> list[dict]:
    """One summary per case with a full packet, i.e. packets/<CASE_ID>.json —
    excludes the intermediate `_features.json` / `_narrative.json` /
    `_score.json` artifacts. Used for the quick-glance index page: case_id,
    risk_band, risk_score, suggested_action."""
    if not settings.PACKETS_DIR.exists():
        return []
    summaries = []
    for path in sorted(settings.PACKETS_DIR.glob("*.json")):
        if path.stem.endswith(_INTERMEDIATE_SUFFIXES):
            continue
        packet = json.loads(path.read_text(encoding="utf-8"))
        summaries.append(
            {
                "case_id": packet.get("case_id", path.stem),
                "risk_score": packet.get("risk_score"),
                "risk_band": packet.get("risk_band"),
                "suggested_action": packet.get("suggested_action"),
            }
        )
    return summaries


def _load_packet(case_id: str) -> dict:
    path = settings.PACKETS_DIR / f"{case_id}.json"
    if not path.exists():
        abort(404, description=f"No packet found for case '{case_id}'.")
    return json.loads(path.read_text(encoding="utf-8"))


@app.route("/")
def index():
    return render_template(
        "index.html", cases=_list_case_summaries(), product_line=settings.PRODUCT_LINE
    )


@app.route("/case/<case_id>")
def view_case(case_id: str):
    packet = _load_packet(case_id)
    return render_template("packet.html", packet=packet)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args(argv)

    # host=127.0.0.1 (not 0.0.0.0): shared machine, keep this local-only.
    # debug=False: the Werkzeug debugger allows code execution from the
    # browser and must not run on a shared box.
    app.run(host="127.0.0.1", port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
