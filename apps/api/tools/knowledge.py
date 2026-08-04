"""`lookup_knowledge` — pgvector top-3 cosine over the client's KB.

The 0.35 similarity floor is the anti-hallucination control. Below it the tool
returns `not_found` with a hint telling the agent to say it will check, which is
a far better answer than a confident paraphrase of an unrelated chunk. Verified
against deliberately out-of-scope questions in the test suite.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db.models import KBChunk
from apps.api.observability.logging import get_logger
from apps.api.tools.registry import ToolResult, ToolSpec, failure, ok

log = get_logger(__name__)

MIN_SIMILARITY = 0.35
TOP_K = 3

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "description": "The caller's question, in their own words.",
        }
    },
    "required": ["question"],
    "additionalProperties": False,
}

NOT_FOUND_HINT = (
    "There is nothing on this in the knowledge base. Say you will check with the team and "
    "follow up. Do not guess an answer."
)
UNAVAILABLE_HINT = (
    "The knowledge base is not reachable. Say you will check and follow up. Do not guess."
)


async def search_chunks(
    session: AsyncSession,
    *,
    client_id: str,
    embedding: list[float],
    top_k: int = TOP_K,
) -> list[tuple[str, str | None, float]]:
    """Top-k by cosine similarity. Returns (content, source, similarity)."""
    # pgvector's `<=>` is cosine *distance*; similarity is 1 - distance.
    distance = KBChunk.embedding.cosine_distance(embedding)
    result = await session.execute(
        select(KBChunk.content, KBChunk.source, distance.label("distance"))
        .where(KBChunk.client_id == client_id)
        .order_by(distance)
        .limit(top_k)
    )
    return [(content, source, 1.0 - float(dist)) for content, source, dist in result.all()]


async def lookup_knowledge(
    *,
    session: AsyncSession,
    client_id: str,
    embed: Any,
    question: str,
    **_: Any,
) -> ToolResult:
    if not question.strip():
        return failure("not_found", NOT_FOUND_HINT, {"reason": "empty question"})

    embedding = await embed(question)
    if embedding is None:
        return failure("unavailable", UNAVAILABLE_HINT, {"reason": "embedding failed"})

    hits = await search_chunks(session, client_id=client_id, embedding=embedding, top_k=TOP_K)
    passing = [hit for hit in hits if hit[2] >= MIN_SIMILARITY]

    if not passing:
        best = max((score for _, _, score in hits), default=0.0)
        log.info("kb_below_threshold", best_score=round(best, 3), question=question[:80])
        return failure("not_found", NOT_FOUND_HINT, {"best_score": round(best, 4)})

    return ok(
        {
            "matches": [
                {"content": content, "source": source, "score": round(score, 4)}
                for content, source, score in passing
            ]
        }
    )


def spec(session_factory: Any, client_id: str, embed: Any) -> ToolSpec:
    async def handler(**kwargs: Any) -> ToolResult:
        async with session_factory() as session:
            return await lookup_knowledge(
                session=session, client_id=client_id, embed=embed, **kwargs
            )

    return ToolSpec(
        name="lookup_knowledge",
        description=(
            "Look up a factual answer about the business: pricing, brands serviced, warranty "
            "policy, what is covered. Use this instead of answering from memory."
        ),
        json_schema=SCHEMA,
        handler=handler,
        timeout_ms=600,
        on_failure="degrade",
        filler_phrase="Let me check on that.",
    )
