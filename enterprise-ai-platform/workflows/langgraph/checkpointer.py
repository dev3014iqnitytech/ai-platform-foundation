"""
LangGraph Checkpointer — PostgreSQL-backed state persistence for the workflow graph.
Ensures interrupted workflows can be resumed after human review or failures.
"""
from __future__ import annotations

from structlog import get_logger

logger = get_logger(__name__)


async def get_postgres_checkpointer():
    """
    Returns an AsyncPostgresSaver connected to the application database.
    Creates the required checkpointing tables on first use.

    Usage:
        async with await get_postgres_checkpointer() as checkpointer:
            graph = build_platform_graph(checkpointer)
    """
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from app.core.config import settings

        checkpointer = await AsyncPostgresSaver.from_conn_string(
            settings.database_url_str
        )
        await checkpointer.setup()
        logger.info("postgres_checkpointer_ready")
        return checkpointer
    except ImportError as e:
        logger.warning("checkpointer_unavailable", error=str(e), fallback="in-memory")
        return _get_memory_checkpointer()
    except Exception as e:
        logger.warning("postgres_checkpointer_failed", error=str(e), fallback="in-memory")
        return _get_memory_checkpointer()


def _get_memory_checkpointer():
    """In-memory checkpointer for local development (no persistence across restarts)."""
    try:
        from langgraph.checkpoint.memory import MemorySaver
        logger.info("memory_checkpointer_active")
        return MemorySaver()
    except ImportError:
        return None
