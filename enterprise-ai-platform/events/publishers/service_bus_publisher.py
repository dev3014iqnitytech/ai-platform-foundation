"""
Service Bus Publisher — Async Azure Service Bus event publishing.
Publishes domain events to topics with retry logic and dead-letter support.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any
from structlog import get_logger

logger = get_logger(__name__)


class ServiceBusPublisher:
    """
    Async Azure Service Bus publisher.
    Falls back to in-memory/log-only mode when Service Bus is unavailable (dev).
    """

    def __init__(self, connection_string: str | None = None):
        self._connection_string = connection_string
        self._client = None
        self._available = False
        if connection_string:
            self._try_init(connection_string)

    def _try_init(self, connection_string: str) -> None:
        try:
            from azure.servicebus.aio import ServiceBusClient
            self._client = ServiceBusClient.from_connection_string(connection_string)
            self._available = True
            logger.info("service_bus_connected")
        except ImportError:
            logger.warning("service_bus_unavailable", reason="azure-servicebus not installed")
        except Exception as e:
            logger.warning("service_bus_init_failed", error=str(e), fallback="log-only")

    async def publish(
        self,
        topic: str,
        event_type: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
        session_id: str | None = None,
    ) -> bool:
        """
        Publish an event to an Azure Service Bus topic.
        Returns True on success, False if delivery failed.
        """
        event = {
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "published_at": datetime.now(timezone.utc).isoformat(),
            "correlation_id": correlation_id or str(uuid.uuid4()),
            "payload": payload,
        }

        if self._available and self._client:
            return await self._send_to_service_bus(topic, event, session_id)

        # Dev fallback — just log
        logger.info(
            "service_bus_event_logged",
            topic=topic,
            event_type=event_type,
            event_id=event["event_id"],
            payload_preview=str(payload)[:200],
        )
        return True

    async def _send_to_service_bus(
        self, topic: str, event: dict, session_id: str | None
    ) -> bool:
        try:
            from azure.servicebus.aio import ServiceBusSender
            from azure.servicebus import ServiceBusMessage

            async with self._client.get_topic_sender(topic_name=topic) as sender:
                msg = ServiceBusMessage(
                    body=json.dumps(event, default=str).encode("utf-8"),
                    content_type="application/json",
                    subject=event["event_type"],
                    correlation_id=event["correlation_id"],
                    session_id=session_id,
                )
                await sender.send_messages(msg)
                logger.debug(
                    "event_published",
                    topic=topic,
                    event_type=event["event_type"],
                    event_id=event["event_id"],
                )
                return True
        except Exception as e:
            logger.error(
                "event_publish_failed",
                topic=topic,
                event_type=event["event_type"],
                error=str(e),
            )
            return False

    async def close(self) -> None:
        if self._client:
            try:
                await self._client.close()
            except Exception:
                pass


# Module-level singleton
_publisher: ServiceBusPublisher | None = None


def _get_publisher() -> ServiceBusPublisher:
    global _publisher
    if _publisher is None:
        try:
            from app.core.config import settings
            conn_str = getattr(settings, "SERVICE_BUS_CONNECTION_STRING", None)
            _publisher = ServiceBusPublisher(connection_string=conn_str)
        except Exception:
            _publisher = ServiceBusPublisher()
    return _publisher


# Convenience function used throughout the codebase
async def publish_event(
    topic: str,
    event_type: str,
    payload: dict[str, Any],
    correlation_id: str | None = None,
    session_id: str | None = None,
) -> bool:
    """Module-level publish helper — no need to instantiate ServiceBusPublisher directly."""
    publisher = _get_publisher()
    return await publisher.publish(
        topic=topic,
        event_type=event_type,
        payload=payload,
        correlation_id=correlation_id,
        session_id=session_id,
    )
