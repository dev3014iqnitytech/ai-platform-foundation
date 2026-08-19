"""
Infrastructure messaging — Service Bus integration for backend.
Wraps publisher for use within FastAPI lifespan events.
"""
from __future__ import annotations

from structlog import get_logger
from events.publishers.service_bus_publisher import ServiceBusPublisher, publish_event  # noqa: F401

logger = get_logger(__name__)

__all__ = ["ServiceBusPublisher", "publish_event", "get_messaging_client"]


def get_messaging_client() -> ServiceBusPublisher:
    """Return the module-level ServiceBusPublisher singleton."""
    from events.publishers.service_bus_publisher import _get_publisher
    return _get_publisher()
