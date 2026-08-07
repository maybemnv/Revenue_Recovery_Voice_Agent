"""OpenAI embeddings for the knowledge base.

Split out from `knowledge.py` so the retrieval path can be tested with a stub
embedder and no network.
"""

from __future__ import annotations

import httpx

from apps.api.observability.logging import get_logger
from apps.api.resilience import IN_CALL, RetryPolicy, request_with_retry
from apps.api.settings import get_settings

log = get_logger(__name__)

EMBEDDING_DIMENSIONS = 1536


async def embed_text(text: str, *, client: httpx.AsyncClient | None = None) -> list[float] | None:
    """Single-string embedding. None on failure — the tool layer turns that into a hint."""
    vectors = await embed_batch([text], client=client)
    return vectors[0] if vectors else None


async def embed_batch(
    texts: list[str],
    *,
    client: httpx.AsyncClient | None = None,
    policy: RetryPolicy = IN_CALL,
) -> list[list[float]]:
    """Embed a batch. Retryable without qualification — it has no side effects.

    The default is the in-call profile because the live knowledge lookup is the
    latency-sensitive caller; the offline ingest passes `BACKGROUND`, where a
    rate limit is worth actually waiting out.
    """
    if not texts:
        return []
    settings = get_settings()
    payload: dict[str, object] = {"model": settings.openai_embedding_model, "input": texts}
    headers = {"Authorization": f"Bearer {settings.openai_api_key}"}

    async def send() -> httpx.Response:
        if client is not None:
            return await _post(client, payload, headers)
        async with httpx.AsyncClient() as owned:
            return await _post(owned, payload, headers)

    try:
        response = await request_with_retry(send, label="openai embeddings", policy=policy)
    except httpx.HTTPError as exc:
        log.warning("embedding_request_failed", error=type(exc).__name__)
        return []

    if response.status_code >= 400:
        log.warning("embedding_http_error", status=response.status_code)
        return []
    data = response.json().get("data", [])
    return [item["embedding"] for item in sorted(data, key=lambda d: d.get("index", 0))]


async def _post(
    client: httpx.AsyncClient, payload: dict[str, object], headers: dict[str, str]
) -> httpx.Response:
    return await client.post(
        "https://api.openai.com/v1/embeddings", json=payload, headers=headers, timeout=30.0
    )
