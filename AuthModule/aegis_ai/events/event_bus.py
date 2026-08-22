"""
aegis_ai.events.event_bus
==========================
SecurityEventBus — Observer pattern implementation for security events.

Design Pattern: Observer
  - Subject  : SecurityEventBus (maintains subscriber list, publishes events)
  - Observer : EventHandler (callable — any audit sink, metric collector, alerter)

The bus supports two delivery modes:
  1. ``fire_and_forget=True``  (default): handlers are scheduled as background
     asyncio Tasks. The pipeline is not blocked by slow audit sinks.
  2. ``fire_and_forget=False``: handlers are awaited sequentially.
     Use for critical sinks where you need guaranteed delivery before returning.

Usage::

    bus = SecurityEventBus()

    # Subscribe audit logger
    @bus.on(EventCategory.AUTH_FAILURE, EventCategory.GUARDRAIL_BLOCK)
    async def send_to_siem(event: SecurityEvent) -> None:
        await siem_client.ingest(event)

    # Subscribe metrics
    bus.subscribe(metrics_handler)  # receives all events

    # Publish from pipeline
    await bus.publish(SecurityEvent.auth_failure(reason="invalid_token"))

OWASP: A09:2021 (Security Logging and Monitoring Failures)
"""

from __future__ import annotations

import asyncio
import functools
from typing import Callable, Coroutine, List, Optional, Set

import structlog

from aegis_ai.events.security_event import EventCategory, SecurityEvent

logger = structlog.get_logger(__name__)

# Type alias: async callable that accepts a SecurityEvent
EventHandler = Callable[[SecurityEvent], Coroutine[None, None, None]]


class _Subscription:
    """Internal subscription record linking a handler to its category filter."""

    __slots__ = ("handler", "categories", "handler_name")

    def __init__(
        self,
        handler: EventHandler,
        categories: Optional[Set[EventCategory]] = None,
    ) -> None:
        self.handler = handler
        self.categories = categories  # None = all categories
        self.handler_name = getattr(handler, "__name__", repr(handler))

    def matches(self, event: SecurityEvent) -> bool:
        """True if this subscription should receive the given event."""
        return self.categories is None or event.category in self.categories


class SecurityEventBus:
    """
    Async event bus for security events.

    Decouples the pipeline from audit/metrics consumers.
    Handlers receive events asynchronously; failures in one handler
    do not affect others or the pipeline.

    Args:
        fire_and_forget: If True (default), handlers run as background tasks.
                         If False, handlers are awaited before ``publish`` returns.
        max_queue_size:  Max buffered events before backpressure kicks in
                         (0 = unlimited). Only used in fire_and_forget mode.
    """

    def __init__(
        self,
        fire_and_forget: bool = True,
        max_queue_size: int = 0,
    ) -> None:
        self._subscriptions: List[_Subscription] = []
        self._fire_and_forget = fire_and_forget
        self._max_queue_size = max_queue_size
        self._background_tasks: Set[asyncio.Task] = set()  # type: ignore[type-arg]

    # ── Subscription API ─────────────────────────────────────────────────────

    def subscribe(
        self,
        handler: EventHandler,
        *,
        categories: Optional[List[EventCategory]] = None,
    ) -> None:
        """
        Register an event handler.

        Args:
            handler:    Async callable ``(SecurityEvent) -> None``.
            categories: Optional list of categories to filter on.
                        If None, handler receives ALL events.
        """
        sub = _Subscription(
            handler=handler,
            categories=set(categories) if categories else None,
        )
        self._subscriptions.append(sub)
        logger.debug(
            "event_handler_subscribed",
            handler=sub.handler_name,
            categories=[c.value for c in (sub.categories or set())],
        )

    def unsubscribe(self, handler: EventHandler) -> None:
        """Remove a previously registered handler."""
        before = len(self._subscriptions)
        self._subscriptions = [s for s in self._subscriptions if s.handler is not handler]
        removed = before - len(self._subscriptions)
        logger.debug("event_handler_unsubscribed", handler=repr(handler), removed=removed)

    def on(self, *categories: EventCategory) -> Callable[[EventHandler], EventHandler]:
        """
        Decorator for subscribing a handler to specific event categories.

        Usage::

            @bus.on(EventCategory.AUTH_FAILURE, EventCategory.GUARDRAIL_BLOCK)
            async def alert_soc(event: SecurityEvent) -> None:
                await send_pagerduty_alert(event)
        """
        def decorator(fn: EventHandler) -> EventHandler:
            self.subscribe(fn, categories=list(categories) if categories else None)
            return fn

        return decorator

    # ── Publishing ───────────────────────────────────────────────────────────

    async def publish(self, event: SecurityEvent) -> None:
        """
        Publish an event to all matching subscribers.

        In fire_and_forget mode, handlers run as background asyncio Tasks.
        In sequential mode, handlers are awaited one-by-one.

        A handler raising an exception is logged but does NOT propagate —
        the pipeline is never blocked by a failing audit sink.

        Args:
            event: The SecurityEvent to publish.
        """
        matching = [s for s in self._subscriptions if s.matches(event)]

        if not matching:
            logger.debug("event_published_no_subscribers", category=event.category.value)
            return

        if self._fire_and_forget:
            for sub in matching:
                task = asyncio.ensure_future(self._safe_call(sub, event))
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
        else:
            for sub in matching:
                await self._safe_call(sub, event)

    async def _safe_call(self, sub: _Subscription, event: SecurityEvent) -> None:
        """Invoke a handler, swallowing and logging any exceptions."""
        try:
            await sub.handler(event)
        except Exception as exc:
            logger.error(
                "event_handler_error",
                handler=sub.handler_name,
                event_id=event.event_id,
                category=event.category.value,
                error=str(exc),
                exc_info=True,
            )

    async def drain(self) -> None:
        """
        Wait for all in-flight background tasks to complete.

        Call during graceful shutdown to ensure no events are lost.
        """
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)

    # ── Introspection ─────────────────────────────────────────────────────────

    @property
    def subscriber_count(self) -> int:
        """Number of registered handlers."""
        return len(self._subscriptions)

    def __repr__(self) -> str:
        return (
            f"SecurityEventBus("
            f"subscribers={self.subscriber_count}, "
            f"fire_and_forget={self._fire_and_forget}, "
            f"pending_tasks={len(self._background_tasks)})"
        )
