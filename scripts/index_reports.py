"""Chunk REPORT facts and upsert them into Pinecone for the narrative
vector-search fallback.

Streams `facts_by_patient/*.jsonl` -- never loads the whole corpus. This is
the one script in the repo that costs real time and real money to run
against a live Pinecone account (roughly 58,000 REPORT facts, each split
into a handful of section chunks, each chunk embedded server-side via
Pinecone's hosted model). **Confirm with the user, and consider --limit for
a smoke test first, before running this unattended against a real account.**

Requires PINECONE_API_KEY (see .env.example). If the named index doesn't
exist yet, this creates it with integrated (server-side) embedding, so no
local embedding model download is needed.

    pwsh> python scripts/index_reports.py --limit 20      # smoke test
    pwsh> python scripts/index_reports.py                 # full corpus
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.vector_store import (  # noqa: E402
    DEFAULT_INDEX_NAME,
    VectorStore,
    iter_report_chunks,
)

EMBED_MODEL = "llama-text-embed-v2"


def _ensure_index_exists(api_key: str, index_name: str) -> None:
    import pinecone
    from pinecone.models.indexes.specs import EmbedConfig, IntegratedSpec

    pc = pinecone.Pinecone(api_key=api_key)
    if pc.indexes.exists(index_name):
        return
    print(f"creating index {index_name!r} (model={EMBED_MODEL!r}) ...", file=sys.stderr)
    pc.indexes.create(
        name=index_name,
        spec=IntegratedSpec(
            cloud="aws",
            region="us-east-1",
            embed=EmbedConfig(model=EMBED_MODEL, field_map={"text": "chunk_text"}),
        ),
    )


def run(facts_dir: Path, index_name: str, api_key: str, limit: int | None, batch_size: int) -> None:
    _ensure_index_exists(api_key, index_name)
    store = VectorStore(api_key=api_key, index_name=index_name)

    patient_files = sorted(facts_dir.glob("*.jsonl"))
    if limit:
        patient_files = patient_files[:limit]

    total_chunks = 0
    total_reports = 0
    for i, path in enumerate(patient_files, 1):
        batch = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                if record.get("category") != "REPORT":
                    continue
                total_reports += 1
                for chunk in iter_report_chunks(record):
                    batch.append(chunk)
                    if len(batch) >= batch_size:
                        store.upsert_chunks(batch)
                        total_chunks += len(batch)
                        batch = []
        if batch:
            store.upsert_chunks(batch)
            total_chunks += len(batch)
        if i % 100 == 0 or i == len(patient_files):
            print(
                f"  {i:,}/{len(patient_files):,} patients -> {total_chunks:,} chunks",
                file=sys.stderr,
            )

    print(
        f"{total_reports:,} REPORT facts -> {total_chunks:,} chunks upserted to {index_name!r}",
        file=sys.stderr,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facts-dir", type=Path, default=REPO_ROOT / "facts_by_patient")
    parser.add_argument("--index-name", default=None)
    parser.add_argument(
        "--limit", type=int, default=None, help="only process the first N patient files"
    )
    parser.add_argument("--batch-size", type=int, default=96)
    args = parser.parse_args()

    api_key = os.environ.get("PINECONE_API_KEY", "")
    if not api_key:
        parser.error("PINECONE_API_KEY is not set (see .env.example)")

    if not args.facts_dir.exists():
        parser.error(
            f"{args.facts_dir} not found -- run scripts/split_facts.py first"
        )

    index_name = args.index_name or os.environ.get("PINECONE_INDEX_NAME", DEFAULT_INDEX_NAME)
    run(args.facts_dir, index_name, api_key, args.limit, args.batch_size)


if __name__ == "__main__":
    main()
