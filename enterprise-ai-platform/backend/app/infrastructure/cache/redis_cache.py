"""
Redis Cache — Async Redis client with semantic caching for RAG queries,
session state, and RBAC permissions.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any
from structlog import get_logger

logger = get_logger(__name__)


class RedisCache:
    """
    Async Redis wrapper providing:
    - Generic key-value caching with TTL
    - Semantic similarity caching for RAG results
    - Session state management
    - RBAC permission caching
    """

    def __init__(self, redis_url: str | None = None):
        self._client = None
        self._available = False
        if redis_url:
            self._try_connect(redis_url)

    def _try_connect(self, redis_url: str) -> None:
        try:
            import redis.asyncio as aioredis
            self._client = aioredis.from_url(
                redis_url,
                encoding="utf-8",
                decode_responses=True,
                max_connections=20,
                socket_connect_timeout=3,
                socket_timeout=3,
            )
            self._available = True
            logger.info("redis_connected", url=redis_url.split("@")[-1])  # Hide credentials
        except Exception as e:
            logger.warning("redis_connection_failed", error=str(e), fallback="no-cache mode")

    # ─────────────────────────────────────────────────────────
    # Generic cache operations
    # ─────────────────────────────────────────────────────────

    async def get(self, key: str) -> Any | None:
        if not self._available:
            return None
        try:
            value = await self._client.get(key)
            if value:
                return json.loads(value)
        except Exception as e:
            logger.warning("redis_get_failed", key=key, error=str(e))
        return None

    async def set(self, key: str, value: Any, ttl: int = 3600) -> bool:
        if not self._available:
            return False
        try:
            serialized = json.dumps(value, default=str)
            await self._client.setex(key, ttl, serialized)
            return True
        except Exception as e:
            logger.warning("redis_set_failed", key=key, error=str(e))
            return False

    async def delete(self, key: str) -> bool:
        if not self._available:
            return False
        try:
            await self._client.delete(key)
            return True
        except Exception as e:
            logger.warning("redis_delete_failed", key=key, error=str(e))
            return False

    async def exists(self, key: str) -> bool:
        if not self._available:
            return False
        try:
            return bool(await self._client.exists(key))
        except Exception:
            return False

    # ─────────────────────────────────────────────────────────
    # Session state (LangGraph checkpointing supplement)
    # ─────────────────────────────────────────────────────────

    async def get_session(self, session_id: str) -> dict | None:
        return await self.get(f"session:{session_id}")

    async def set_session(self, session_id: str, data: dict, ttl: int = 86400) -> bool:
        return await self.set(f"session:{session_id}", data, ttl=ttl)

    async def delete_session(self, session_id: str) -> bool:
        return await self.delete(f"session:{session_id}")

    # ─────────────────────────────────────────────────────────
    # RBAC permission caching
    # ─────────────────────────────────────────────────────────

    async def get_permissions(self, user_id: str) -> list[str] | None:
        result = await self.get(f"user:{user_id}:permissions")
        return result if isinstance(result, list) else None

    async def set_permissions(self, user_id: str, permissions: list[str]) -> bool:
        return await self.set(f"user:{user_id}:permissions", permissions, ttl=900)  # 15 min

    async def invalidate_permissions(self, user_id: str) -> bool:
        return await self.delete(f"user:{user_id}:permissions")

    # ─────────────────────────────────────────────────────────
    # Semantic / RAG result caching
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _query_hash(query: str, filters: dict | None = None) -> str:
        payload = json.dumps({"q": query.lower().strip(), "f": filters or {}}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:32]

    async def get_rag_cache(self, query: str, filters: dict | None = None) -> list[dict] | None:
        key = f"semantic_cache:{self._query_hash(query, filters)}"
        return await self.get(key)

    async def set_rag_cache(
        self, query: str, results: list[dict], filters: dict | None = None, ttl: int = 3600
    ) -> bool:
        key = f"semantic_cache:{self._query_hash(query, filters)}"
        return await self.set(key, results, ttl=ttl)

    # ─────────────────────────────────────────────────────────
    # ADO story caching
    # ─────────────────────────────────────────────────────────

    async def get_ado_story(self, story_id: str) -> dict | None:
        return await self.get(f"ado_story:{story_id}")

    async def set_ado_story(self, story_id: str, story: dict) -> bool:
        return await self.set(f"ado_story:{story_id}", story, ttl=300)  # 5 min

    # ─────────────────────────────────────────────────────────
    # Health check
    # ─────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        if not self._available:
            return False
        try:
            return await self._client.ping()
        except Exception:
            return False

    async def close(self) -> None:
        if self._client:
            try:
                await self._client.aclose()
            except Exception:
                pass


# Module-level singleton
_cache: RedisCache | None = None


def get_cache() -> RedisCache:
    global _cache
    if _cache is None:
        try:
            from app.core.config import settings
            _cache = RedisCache(redis_url=settings.REDIS_URL)
        except Exception:
            _cache = RedisCache()
    return _cache
