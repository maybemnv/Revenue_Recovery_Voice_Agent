"""`kb-ingest` — chunk a client's knowledge base and embed it into pgvector.

Chunking is paragraph-aware with a hard character ceiling rather than
token-perfect, because a 600-character chunk that stops at a paragraph boundary
retrieves better than a 512-token chunk that stops mid-sentence, and the
retrieval floor in `tools/knowledge.py` is what actually guards quality.

Re-running is idempotent per client: existing chunks are deleted first, so the
KB is a projection of the source directory rather than an append-only pile that
drifts from it.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

from sqlalchemy import delete

from apps.api.config.loader import ClientConfigNotFound, get_registry
from apps.api.db.models import KBChunk
from apps.api.db.repository import insert_kb_chunks
from apps.api.db.session import session_scope
from apps.api.observability.logging import configure_logging, get_logger
from apps.api.resilience import BACKGROUND
from apps.api.tools.embeddings import embed_batch

log = get_logger(__name__)

MAX_CHUNK_CHARS = 900
MIN_CHUNK_CHARS = 60
EMBED_BATCH_SIZE = 64
SUPPORTED_SUFFIXES = (".md", ".txt")

_PARAGRAPH = re.compile(r"\n\s*\n")


def chunk_text(text: str, *, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Group paragraphs up to the ceiling; split anything over it on sentences."""
    chunks: list[str] = []
    buffer = ""
    for paragraph in _PARAGRAPH.split(text):
        para = paragraph.strip()
        if not para:
            continue
        if len(para) > max_chars:
            if buffer:
                chunks.append(buffer)
                buffer = ""
            chunks.extend(_split_long(para, max_chars))
            continue
        if len(buffer) + len(para) + 2 > max_chars:
            chunks.append(buffer)
            buffer = para
        else:
            buffer = f"{buffer}\n\n{para}" if buffer else para
    if buffer:
        chunks.append(buffer)
    return [c for c in chunks if len(c) >= MIN_CHUNK_CHARS]


def _split_long(paragraph: str, max_chars: int) -> list[str]:
    out: list[str] = []
    buffer = ""
    for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
        if len(buffer) + len(sentence) + 1 > max_chars and buffer:
            out.append(buffer)
            buffer = sentence
        else:
            buffer = f"{buffer} {sentence}".strip()
    if buffer:
        out.append(buffer)
    return out


def collect_documents(root: Path) -> list[tuple[str, str]]:
    """(source, content) for every supported file under `root`."""
    if root.is_file():
        return [(root.name, root.read_text(encoding="utf-8"))]
    docs: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
            docs.append((str(path.relative_to(root)), path.read_text(encoding="utf-8")))
    return docs


async def ingest(client_id: str, source_dir: Path, *, replace: bool = True) -> int:
    documents = collect_documents(source_dir)
    if not documents:
        log.warning("kb_no_documents", directory=str(source_dir))
        return 0

    pending: list[tuple[str, str]] = []
    for source, content in documents:
        pending.extend((source, chunk) for chunk in chunk_text(content))
    log.info("kb_chunked", documents=len(documents), chunks=len(pending))

    embedded: list[tuple[str, str, list[float]]] = []
    for start in range(0, len(pending), EMBED_BATCH_SIZE):
        batch = pending[start : start + EMBED_BATCH_SIZE]
        # Nobody is on the line during an ingest, and a rate limit part-way
        # through a large corpus is worth waiting out rather than failing.
        vectors = await embed_batch([content for _, content in batch], policy=BACKGROUND)
        if len(vectors) != len(batch):
            raise RuntimeError(
                f"embedding returned {len(vectors)} vectors for {len(batch)} chunks"
            )
        embedded.extend(
            (source, content, vector)
            for (source, content), vector in zip(batch, vectors, strict=True)
        )
        log.info("kb_embedded_batch", done=len(embedded), total=len(pending))

    async with session_scope() as session:
        if replace:
            # The KB is a projection of the source tree, not an append log.
            await session.execute(delete(KBChunk).where(KBChunk.client_id == client_id))
        written = await insert_kb_chunks(session, client_id=client_id, chunks=embedded)

    log.info("kb_ingested", client_id=client_id, chunks=written)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(prog="kb-ingest", description=__doc__)
    parser.add_argument("client_id", help="client_id from config/clients/*.yaml")
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="Directory or file to ingest. Defaults to the config's knowledge_base path.",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Keep existing chunks instead of replacing them.",
    )
    args = parser.parse_args()

    configure_logging("INFO", json_output=False)

    source = args.source
    if source is None:
        try:
            config = get_registry().get(args.client_id)
        except ClientConfigNotFound as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if not config.knowledge_base:
            print(
                f"error: client {args.client_id!r} has no knowledge_base configured; "
                "pass --source",
                file=sys.stderr,
            )
            return 2
        source = Path(config.knowledge_base)

    if not source.exists():
        print(f"error: {source} does not exist", file=sys.stderr)
        return 2

    written = asyncio.run(ingest(args.client_id, source, replace=not args.append))
    print(f"ingested {written} chunks for {args.client_id}")
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
