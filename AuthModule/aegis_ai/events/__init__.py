"""
aegis_ai.events
================
Security Event Bus — Observer pattern for decoupled audit/metrics fan-out.

Usage::

    from aegis_ai.events import SecurityEventBus, SecurityEvent, EventCategory

    bus = SecurityEventBus()
    bus.subscribe(my_audit_handler)
    bus.subscribe(my_metrics_handler)

    await bus.publish(SecurityEvent(
        category=EventCategory.AUTH_SUCCESS,
        ...
    ))
"""

from aegis_ai.events.security_event import EventCategory, EventSeverity, SecurityEvent
from aegis_ai.events.event_bus import EventHandler, SecurityEventBus

__all__ = [
    "SecurityEvent",
    "EventCategory",
    "EventSeverity",
    "EventHandler",
    "SecurityEventBus",
]
