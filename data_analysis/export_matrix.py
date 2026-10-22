"""Write the code x interview workbook to a local file.

The same workbook the app uploads to SharePoint (``state/export/``) on every
flush: one sheet per source (human ground truth, majority vote, each model),
interviews as rows, codes as columns. See ``pipeline.export``.

    python export_matrix.py                         # from the local results DB
    python export_matrix.py --db <coding.sqlite> --out <file.xlsx>
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import config
from pipeline import export


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(config.DB_PATH),
                    help="results database to read (default: the local one)")
    ap.add_argument("--out", default=str(config.OUTPUT_DIR / export.WORKBOOK_NAME),
                    help="workbook to write")
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        data = export.workbook_bytes(conn)
    finally:
        conn.close()
    Path(args.out).write_bytes(data)
    print(f"Wrote {args.out} ({len(data):,} bytes)")


if __name__ == "__main__":
    main()
