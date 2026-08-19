"""
Service Bus Consumer — Async event consumers for all platform topics.
Each consumer subscribes to a Service Bus topic and dispatches to handlers.
"""
from __future__ import annotations

import asyncio
import json
from typing import Callable, Awaitable
from structlog import get_logger

logger = get_logger(__name__)

EventHandler = Callable[[dict], Awaitable[None]]


class ServiceBusConsumer:
    """
    Async Azure Service Bus consumer with:
    - Automatic message acknowledgement on success
    - Dead-letter on repeated failure (max_retries)
    - Graceful shutdown support
    """

    def __init__(
        self,
        connection_string: str,
        topic: str,
        subscription: str,
        handler: EventHandler,
        max_retries: int = 3,
    ):
        self.connection_string = connection_string
        self.topic = topic
        self.subscription = subscription
        self.handler = handler
        self.max_retries = max_retries
        self._running = False

    async def start(self) -> None:
        """Start consuming messages in a background loop."""
        self._running = True
        logger.info("consumer_started", topic=self.topic, subscription=self.subscription)

        try:
            from azure.servicebus.aio import ServiceBusClient

            async with ServiceBusClient.from_connection_string(self.connection_string) as client:
                async with client.get_subscription_receiver(
                    topic_name=self.topic,
                    subscription_name=self.subscription,
                    max_wait_time=5,
                ) as receiver:
                    while self._running:
                        messages = await receiver.receive_messages(max_message_count=10, max_wait_time=5)
                        for msg in messages:
                            await self._process(receiver, msg)

        except ImportError:
            logger.warning("consumer_unavailable", reason="azure-servicebus not installed")
        except Exception as e:
            logger.error("consumer_crashed", topic=self.topic, error=str(e))

    async def _process(self, receiver, msg) -> None:
        try:
            body = b"".join(msg.body).decode("utf-8")
            event = json.loads(body)
            await self.handler(event)
            await receiver.complete_message(msg)
            logger.debug("message_processed", topic=self.topic, event_type=event.get("event_type"))
        except Exception as e:
            delivery_count = msg.delivery_count or 0
            logger.warning("message_processing_failed", error=str(e), delivery_count=delivery_count)
            if delivery_count >= self.max_retries:
                await receiver.dead_letter_message(msg, reason=str(e)[:512])
            else:
                await receiver.abandon_message(msg)

    def stop(self) -> None:
        self._running = False
        logger.info("consumer_stopped", topic=self.topic)


# ──────────────────────────────────────────────────────────────
# Notification helper
# ──────────────────────────────────────────────────────────────

async def _notify_teams(
    title: str,
    message: str,
    session_id: str | None = None,
) -> None:
    """Post an Adaptive Card to the configured Teams webhook, silently no-ops if unset."""
    try:
        from app.core.config import settings
        webhook_url = getattr(settings, "TEAMS_WEBHOOK_URL", None)
        if not webhook_url:
            logger.debug("teams_notify_skipped", reason="TEAMS_WEBHOOK_URL not configured")
            return

        import httpx
        frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:5173")
        review_url = f"{frontend_url}/review/{session_id}" if session_id else frontend_url

        # Teams Adaptive Card payload
        card = {
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium"},
                        {"type": "TextBlock", "text": message, "wrap": True},
                    ],
                    "actions": [
                        {
                            "type": "Action.OpenUrl",
                            "title": "Open Review",
                            "url": review_url,
                        }
                    ],
                },
            }],
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(str(webhook_url), json=card)
            if resp.status_code not in (200, 202):
                logger.warning("teams_notify_failed", status=resp.status_code)
            else:
                logger.info("teams_notify_sent", title=title)
    except Exception as e:
        logger.warning("teams_notify_error", error=str(e))


# ──────────────────────────────────────────────────────────────
# Event Handlers
# ──────────────────────────────────────────────────────────────

async def handle_review_requested(event: dict) -> None:
    """Sends Teams webhook notification to reviewers when a session enters IN_REVIEW."""
    session_id = event.get("payload", {}).get("session_id")
    story_id = event.get("payload", {}).get("story_id")
    logger.info("review_requested_received", session_id=session_id, story_id=story_id)
    await _notify_teams(
        title="🔍 Test Cases Ready for Review",
        message=(
            f"AI-generated test cases for **{story_id}** are ready for your review.\n"
            f"Session: `{session_id}`"
        ),
        session_id=session_id,
    )


async def handle_testcases_approved(event: dict) -> None:
    """Logs audit event when test cases are approved."""
    payload = event.get("payload", {})
    logger.info("testcases_approved", session_id=payload.get("session_id"))
    try:
        from app.infrastructure.database.session import async_session_factory
        from app.infrastructure.database.repositories import AuditLogRepository
        import uuid
        actor_id = uuid.UUID(payload["actor_id"]) if payload.get("actor_id") else None
        async with async_session_factory() as db:
            repo = AuditLogRepository(db)
            await repo.create(
                actor_id=actor_id,
                action="testcases.approved",
                entity_type="session",
                entity_id=uuid.UUID(payload["session_id"]) if payload.get("session_id") else None,
                payload=payload,
            )
            await db.commit()
    except Exception as e:
        logger.warning("audit_log_failed", error=str(e))


async def handle_ado_updated(event: dict) -> None:
    """Logs audit entry when ADO is updated with new test cases."""
    payload = event.get("payload", {})
    session_id = payload.get("session_id")
    ado_plan_id = payload.get("test_plan_id")
    test_cases_count = payload.get("test_cases_count", 0)
    logger.info(
        "ado_updated_event",
        session_id=session_id,
        test_plan_id=ado_plan_id,
        test_cases_count=test_cases_count,
    )
    try:
        from app.infrastructure.database.session import async_session_factory
        from app.infrastructure.database.repositories import AuditLogRepository, SessionRepository
        import uuid
        async with async_session_factory() as db:
            audit_repo = AuditLogRepository(db)
            await audit_repo.create(
                actor_id=None,  # system event — no human actor
                action="ado.test_cases_published",
                entity_type="session",
                entity_id=uuid.UUID(session_id) if session_id else None,
                payload={
                    "test_plan_id": ado_plan_id,
                    "test_cases_count": test_cases_count,
                    "story_id": payload.get("story_id"),
                },
            )
            # Update session status to PUBLISHED
            if session_id:
                session_repo = SessionRepository(db)
                await session_repo.update_status(uuid.UUID(session_id), "PUBLISHED")
            await db.commit()
            logger.info("ado_update_audit_logged", session_id=session_id)
    except Exception as e:
        logger.warning("ado_update_audit_failed", error=str(e), session_id=session_id)


async def handle_document_ingested(event: dict) -> None:
    """Triggers cache invalidation when a new KB document is ingested."""
    payload = event.get("payload", {})
    category = payload.get("category", "")
    logger.info("document_ingested_event", category=category)
    try:
        from rag.retrieval.cache import get_rag_cache
        cache = get_rag_cache()
        await cache.invalidate_for_category(category)
    except Exception as e:
        logger.warning("cache_invalidation_failed", error=str(e))


# ──────────────────────────────────────────────────────────────
# Consumer Registry — Start all consumers
# ──────────────────────────────────────────────────────────────

async def start_all_consumers(connection_string: str) -> list[asyncio.Task]:
    """Launch all Service Bus consumers as background tasks."""
    configs = [
        ("review-events", "notification-service", handle_review_requested),
        ("approval-events", "audit-service", handle_testcases_approved),
        ("ado-events", "audit-service", handle_ado_updated),
        ("kb-events", "cache-invalidation-service", handle_document_ingested),
    ]

    tasks = []
    for topic, subscription, handler in configs:
        consumer = ServiceBusConsumer(
            connection_string=connection_string,
            topic=topic,
            subscription=subscription,
            handler=handler,
        )
        task = asyncio.create_task(consumer.start(), name=f"consumer-{topic}")
        tasks.append(task)
        logger.info("consumer_task_created", topic=topic, subscription=subscription)

    return tasks
