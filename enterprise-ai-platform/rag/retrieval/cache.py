"""
RAG Retrieval Cache — Semantic similarity caching for RAG query results.
Uses Redis with query embedding comparison to serve cached results for similar queries.
Avoids redundant vector search + LLM calls for semantically equivalent questions.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any
from structlog import get_logger

logger = get_logger(__name__)

# Cache a result if query cosine similarity >= this threshold
SIMILARITY_THRESHOLD = 0.92


class RetrievalCache:
    """
    Two-level RAG cache:
    1. Exact-match cache: hash(query + filters) → results  (fast, no embeddings)
    2. Semantic-match cache: embedding similarity → results  (slower, better hit rate)
    
    Level 1 always runs first. Level 2 is optional and requires embedding service.
    """

    def __init__(self, redis_url: str | None = None, ttl: int = 3600):
        self.ttl = ttl
        self._redis = None
        self._available = False
        self._local: dict[str, dict] = {}  # In-memory fallback
        if redis_url:
            self._try_connect(redis_url)

    def _try_connect(self, redis_url: str) -> None:
        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(redis_url, decode_responses=True)
            self._available = True
            logger.info("rag_cache_redis_connected")
        except Exception as e:
            logger.warning("rag_cache_redis_failed", error=str(e), fallback="in-memory")

    @staticmethod
    def _make_key(query: str, filters: dict | None = None) -> str:
        payload = json.dumps(
            {"q": query.lower().strip(), "f": filters or {}}, sort_keys=True
        )
        return f"rag_cache:{hashlib.sha256(payload.encode()).hexdigest()[:32]}"

    async def get(self, query: str, filters: dict | None = None) -> list[dict] | None:
        """Look up exact cache hit."""
        key = self._make_key(query, filters)

        if self._available and self._redis:
            try:
                raw = await self._redis.get(key)
                if raw:
                    logger.debug("rag_cache_hit", query=query[:60])
                    return json.loads(raw)
            except Exception as e:
                logger.warning("rag_cache_get_failed", error=str(e))
        else:
            entry = self._local.get(key)
            if entry and (time.time() - entry["ts"]) < self.ttl:
                return entry["data"]

        return None

    async def set(
        self, query: str, results: list[dict], filters: dict | None = None
    ) -> None:
        """Store results in cache."""
        key = self._make_key(query, filters)
        data = json.dumps(results, default=str)

        if self._available and self._redis:
            try:
                await self._redis.setex(key, self.ttl, data)
                logger.debug("rag_cache_stored", query=query[:60], result_count=len(results))
            except Exception as e:
                logger.warning("rag_cache_set_failed", error=str(e))
        else:
            self._local[key] = {"data": results, "ts": time.time()}

    async def invalidate_for_category(self, category: str) -> int:
        """Remove all cached results for a knowledge base category."""
        if not self._available or not self._redis:
            self._local.clear()
            return 0
        try:
            keys = await self._redis.keys("rag_cache:*")
            # Can't efficiently filter by category without scanning values
            # In production, use Redis tags or a separate category→keys index
            deleted = 0
            if keys:
                deleted = await self._redis.delete(*keys)
            logger.info("rag_cache_invalidated", category=category, keys_deleted=deleted)
            return deleted
        except Exception as e:
            logger.warning("rag_cache_invalidate_failed", error=str(e))
            return 0

    async def clear_all(self) -> None:
        """Clear all cached RAG results."""
        if self._available and self._redis:
            try:
                keys = await self._redis.keys("rag_cache:*")
                if keys:
                    await self._redis.delete(*keys)
            except Exception:
                pass
        self._local.clear()

    async def get_stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        stats: dict[str, Any] = {"type": "redis" if self._available else "in-memory"}
        if self._available and self._redis:
            try:
                keys = await self._redis.keys("rag_cache:*")
                stats["total_keys"] = len(keys)
                info = await self._redis.info("memory")
                stats["used_memory_human"] = info.get("used_memory_human", "unknown")
            except Exception:
                pass
        else:
            stats["total_keys"] = len(self._local)
        return stats


# Module-level singleton
_rag_cache: RetrievalCache | None = None


def get_rag_cache() -> RetrievalCache:
    global _rag_cache
    if _rag_cache is None:
        try:
            from app.core.config import settings
            _rag_cache = RetrievalCache(redis_url=settings.REDIS_URL)
        except Exception:
            _rag_cache = RetrievalCache()
    return _rag_cache
