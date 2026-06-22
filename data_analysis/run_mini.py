"""STEP 4 cheap validation: a tiny REAL Azure call before the full run.

Runs the whole pipeline against just 1-2 transcripts and 1-2 codes so we can
inspect response parsing and gauge token cost before committing to the full
50% across all codes. Prints the parsed coding results so they can be reviewed.

    python run_mini.py            # 2 codes x 2 docs (default)
    python run_mini.py --codes 1 --docs 1
"""

from __future__ import annotations

import argparse

import config
from models import available_adapters
from models.base import CodingRequest
from pipeline.codebook import load_codebook
from pipeline.interviews import load_interviews, merge_documents, select_sample


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--codes", type=int, default=2)
    p.add_argument("--docs", type=int, default=2)
    args = p.parse_args()

    adapters = available_adapters()
    print("Available models:", [a.name for a in adapters] or "NONE")
    if not adapters:
        print("No credentials found — cannot run the mini validation.")
        return

    codes = load_codebook(config.CODEBOOK_PATH)[: args.codes]
    interviews = load_interviews(config.INTERVIEWS_DIR)
    sample = select_sample(interviews)[: args.docs]
    titles = [iv.title for iv in sample]
    merged = merge_documents(sample)

    print(f"\nMerged input: {len(sample)} transcript(s), "
          f"{len(merged):,} chars (~{len(merged)//4:,} tokens est.)")
    print(f"Documents: {titles}")
    print(f"Codes: {[c.name for c in codes]}\n")

    adapter = adapters[0]
    for code in codes:
        print("=" * 70)
        print(f"CODE: {code.name}")
        print(f"DEFINITION: {code.definition}")
        req = CodingRequest(
            code_name=code.name,
            code_description=code.definition,
            merged_document=merged,
            document_titles=tuple(titles),
        )
        hits = adapter.code_one(req)
        print(f"-> {len(hits)} occurrence(s) parsed:")
        for h in hits:
            print(f"   • [{h.document_title}]")
            print(f"     QUOTE : {h.quote}")
            print(f"     REASON: {h.reason}")
        print()


if __name__ == "__main__":
    main()
