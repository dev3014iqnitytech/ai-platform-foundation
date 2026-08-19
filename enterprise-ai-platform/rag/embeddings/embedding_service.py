"""
RAG Embeddings Service — Azure OpenAI text-embedding-3-large wrapper.
Provides batched embedding generation with Redis caching.
"""
from __future__ import annotations

import hashlib
import json
from typing import Sequence
from structlog import get_logger

logger = get_logger(__name__)

EMBEDDING_MODEL = "text-embedding-3-large"
EMBEDDING_DIM = 3072


class EmbeddingService:
    """
    Async embedding service with:
    - Azure OpenAI text-embedding-3-large
    - Redis caching (avoid re-embedding identical text)
    - Batch processing for efficiency (max 100 per request)
    """

    BATCH_SIZE = 100

    def __init__(self, azure_endpoint: str | None = None, api_key: str | None = None,
                 deployment: str = EMBEDDING_MODEL, redis_url: str | None = None):
        self.deployment = deployment
        self._client = None
        self._cache_client = None
        self._available = False

        if azure_endpoint and api_key:
            self._try_init(azure_endpoint, api_key)
        if redis_url:
            self._try_init_cache(redis_url)

    def _try_init(self, endpoint: str, api_key: str) -> None:
        try:
            from openai import AsyncAzureOpenAI
            from app.core.config import settings
            self._client = AsyncAzureOpenAI(
                azure_endpoint=endpoint,
                api_key=api_key,
                api_version=settings.AZURE_OPENAI_API_VERSION,
            )
            self._available = True
            logger.info("embedding_service_initialized", model=self.deployment)
        except Exception as e:
            logger.warning("embedding_service_init_failed", error=str(e))

    def _try_init_cache(self, redis_url: str) -> None:
        try:
            import redis.asyncio as aioredis
            self._cache_client = aioredis.from_url(redis_url, decode_responses=True)
        except Exception:
            pass

    async def embed(self, text: str) -> list[float]:
        """Embed a single text string."""
        results = await self.embed_batch([text])
        return results[0] if results else []

    async def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed multiple texts with caching and batching."""
        texts = list(texts)
        results: list[list[float] | None] = [None] * len(texts)
        uncached_indices: list[int] = []

        # Check cache
        for i, text in enumerate(texts):
            cached = await self._get_cached(text)
            if cached is not None:
                results[i] = cached
            else:
                uncached_indices.append(i)

        # Batch embed uncached
        if uncached_indices and self._available and self._client:
            uncached_texts = [texts[i] for i in uncached_indices]
            for batch_start in range(0, len(uncached_texts), self.BATCH_SIZE):
                batch = uncached_texts[batch_start:batch_start + self.BATCH_SIZE]
                batch_indices = uncached_indices[batch_start:batch_start + self.BATCH_SIZE]

                try:
                    response = await self._client.embeddings.create(
                        model=self.deployment,
                        input=[t[:8000] for t in batch],  # API token limit
                    )
                    for j, embedding_data in enumerate(response.data):
                        idx = batch_indices[j]
                        embedding = embedding_data.embedding
                        results[idx] = embedding
                        await self._set_cached(texts[idx], embedding)
                except Exception as e:
                    logger.error("embedding_batch_failed", error=str(e))
                    # Fill with zero vectors as fallback
                    for idx in batch_indices:
                        if results[idx] is None:
                            results[idx] = [0.0] * EMBEDDING_DIM

        # Fill any remaining None with zeros
        return [r if r is not None else [0.0] * EMBEDDING_DIM for r in results]

    @staticmethod
    def _cache_key(text: str) -> str:
        return f"emb:{hashlib.sha256(text.encode()).hexdigest()[:32]}"

    async def _get_cached(self, text: str) -> list[float] | None:
        if not self._cache_client:
            return None
        try:
            raw = await self._cache_client.get(self._cache_key(text))
            return json.loads(raw) if raw else None
        except Exception:
            return None

    async def _set_cached(self, text: str, embedding: list[float], ttl: int = 86400) -> None:
        if not self._cache_client:
            return
        try:
            await self._cache_client.setex(
                self._cache_key(text), ttl, json.dumps(embedding)
            )
        except Exception:
            pass


# Module-level singleton
_embedding_service: EmbeddingService | None = None


def get_embedding_service() -> EmbeddingService:
    global _embedding_service
    if _embedding_service is None:
        try:
            from app.core.config import settings
            _embedding_service = EmbeddingService(
                azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
                api_key=settings.AZURE_OPENAI_API_KEY,
                deployment=getattr(settings, "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", EMBEDDING_MODEL),
                redis_url=settings.REDIS_URL,
            )
        except Exception:
            _embedding_service = EmbeddingService()
    return _embedding_service
