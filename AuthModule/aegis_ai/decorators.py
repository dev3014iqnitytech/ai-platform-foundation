"""
aegis_ai.decorators
=====================
Production-grade decorator utilities for cross-cutting security concerns.

Design Pattern: Decorator (Wrapper)
  These function decorators add behaviour (retry, permission enforcement,
  audit logging) to async callables without modifying their implementation.

Available decorators
---------------------
- ``@retry_on_transient``     : Exponential backoff for transient errors
- ``@require_permission``     : Enforce IAM permission on handler entry
- ``@audit_action``           : Auto-log entry/exit of sensitive operations
- ``@circuit_breaker``        : Prevent cascade failures via circuit breaker

SOLID: OCP — decorators extend behaviour without modifying wrapped functions.
OWASP: LLM04 (DoS), A01 (Broken Access Control), A09 (Logging Failures)
"""

from __future__ import annotations

import asyncio
import functools
import time
from enum import Enum
from typing import Any, Callable, List, Optional, Tuple, Type

import structlog

log = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Retry Decorator
# ─────────────────────────────────────────────────────────────────────────────


def retry_on_transient(
    max_retries: int = 3,
    initial_backoff: float = 0.5,
    max_backoff: float = 8.0,
    backoff_multiplier: float = 2.0,
    retriable_exceptions: Tuple[Type[Exception], ...] = (Exception,),
    non_retriable_exceptions: Tuple[Type[Exception], ...] = (),
) -> Callable:
    """
    Async decorator: retry on transient errors with exponential backoff.

    Args:
        max_retries:              Maximum number of retry attempts.
        initial_backoff:          Initial delay in seconds before first retry.
        max_backoff:              Maximum delay cap in seconds.
        backoff_multiplier:       Multiplier applied after each retry.
        retriable_exceptions:     Exception types eligible for retry.
        non_retriable_exceptions: Exception types that bypass retry immediately.

    Usage::

        @retry_on_transient(max_retries=3, initial_backoff=0.5)
        async def call_gcp_api():
            ...

        # Only retry httpx transport errors, not 4xx:
        @retry_on_transient(
            retriable_exceptions=(httpx.TransportError,),
            non_retriable_exceptions=(httpx.HTTPStatusError,),
        )
        async def fetch_token():
            ...
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            backoff = initial_backoff
            last_exc: Optional[Exception] = None

            for attempt in range(max_retries + 1):
                try:
                    return await fn(*args, **kwargs)
                except non_retriable_exceptions as exc:
                    # Surface non-retriable immediately
                    log.debug(
                        "retry_non_retriable",
                        fn=fn.__name__,
                        attempt=attempt,
                        error=str(exc),
                    )
                    raise
                except retriable_exceptions as exc:
                    last_exc = exc
                    if attempt == max_retries:
                        break

                    wait = min(backoff, max_backoff)
                    log.warning(
                        "retry_transient_error",
                        fn=fn.__name__,
                        attempt=attempt,
                        backoff_seconds=round(wait, 2),
                        error=str(exc),
                    )
                    await asyncio.sleep(wait)
                    backoff = min(backoff * backoff_multiplier, max_backoff)

            log.error(
                "retry_exhausted",
                fn=fn.__name__,
                max_retries=max_retries,
                error=str(last_exc),
            )
            raise last_exc  # type: ignore[misc]

        return wrapper
    return decorator


# ─────────────────────────────────────────────────────────────────────────────
# Permission Enforcement Decorator
# ─────────────────────────────────────────────────────────────────────────────


def require_permission(permission: str, resource_arg: str = "resource") -> Callable:
    """
    Async decorator: enforce an IAM permission before calling the wrapped function.

    Expects the wrapped function to receive an ``identity`` keyword argument
    (``IdentityContext``) and optionally a ``resource`` keyword argument.

    Args:
        permission:   IAM permission string (e.g. ``'agents.call'``).
        resource_arg: Name of the kwarg holding the resource path. Defaults
                      to ``'resource'``.

    Usage::

        @require_permission("agents.invoke")
        async def invoke_agent(identity: IdentityContext, resource: str, ...):
            ...

    Raises:
        AuthorizationError: If the identity lacks the required permission.
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            identity = kwargs.get("identity") or (args[1] if len(args) > 1 else None)
            resource = kwargs.get(resource_arg, "*")

            if identity is None:
                from aegis_ai.exceptions import AuthorizationError
                raise AuthorizationError(
                    message=f"@require_permission: no 'identity' argument found on {fn.__name__}",
                    error_code="MISSING_IDENTITY",
                )

            log.debug(
                "permission_check",
                fn=fn.__name__,
                permission=permission,
                identity_id=getattr(identity, "identity_id", "unknown"),
                resource=resource,
            )

            # Check via RBAC (fast, synchronous) — IAM check happens in pipeline
            rbac_roles: List[str] = getattr(identity, "roles", [])
            if not any(permission in r for r in rbac_roles):
                log.warning(
                    "permission_denied_decorator",
                    fn=fn.__name__,
                    permission=permission,
                    identity_id=getattr(identity, "identity_id", "unknown"),
                )
                from aegis_ai.exceptions import AuthorizationError
                raise AuthorizationError(
                    message=f"Permission denied: '{permission}' required to call '{fn.__name__}'",
                    error_code="PERMISSION_DENIED",
                    details={"permission": permission, "resource": resource},
                )

            return await fn(*args, **kwargs)

        return wrapper
    return decorator


# ─────────────────────────────────────────────────────────────────────────────
# Audit Action Decorator
# ─────────────────────────────────────────────────────────────────────────────


def audit_action(
    action_name: str,
    *,
    severity: str = "INFO",
    log_args: bool = False,
) -> Callable:
    """
    Async decorator: emit a structured audit log entry on entry and exit.

    Captures latency, success/failure outcome, and (optionally) sanitised
    argument names for traceability.

    Args:
        action_name: Human-readable action label for the audit log.
        severity:    Log severity for the audit entry.
        log_args:    If True, log kwarg names (NOT values) for traceability.
                     Never log values — they may contain secrets.

    Usage::

        @audit_action("token_rotation", severity="HIGH")
        async def rotate_jwt_key(settings: AegisSettings) -> None:
            ...
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.monotonic()
            fn_log = log.bind(action=action_name, fn=fn.__name__, severity=severity)

            context = {"arg_names": list(kwargs.keys())} if log_args else {}
            fn_log.info("action_start", **context)

            try:
                result = await fn(*args, **kwargs)
                latency = (time.monotonic() - start) * 1000
                fn_log.info(
                    "action_success",
                    latency_ms=round(latency, 2),
                )
                return result
            except Exception as exc:
                latency = (time.monotonic() - start) * 1000
                fn_log.error(
                    "action_failure",
                    latency_ms=round(latency, 2),
                    error=type(exc).__name__,
                    error_msg=str(exc),
                )
                raise

        return wrapper
    return decorator


# ─────────────────────────────────────────────────────────────────────────────
# Circuit Breaker Decorator
# ─────────────────────────────────────────────────────────────────────────────


class _CircuitState(str, Enum):
    CLOSED = "CLOSED"        # Normal operation — calls pass through
    OPEN = "OPEN"            # Failing — calls immediately rejected
    HALF_OPEN = "HALF_OPEN"  # Probing — one call allowed to test recovery


class CircuitBreaker:
    """
    Async circuit breaker for protecting against cascade failures.

    State machine:
      CLOSED → OPEN     : After ``failure_threshold`` consecutive failures
      OPEN → HALF_OPEN  : After ``recovery_timeout`` seconds
      HALF_OPEN → CLOSED: If the probe call succeeds
      HALF_OPEN → OPEN  : If the probe call fails

    Usage::

        breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30)

        @breaker
        async def call_llm_provider():
            ...
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        name: str = "circuit_breaker",
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._name = name
        self._state = _CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: Optional[float] = None
        self._lock = asyncio.Lock()

    def __call__(self, fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            await self._before_call()
            try:
                result = await fn(*args, **kwargs)
                await self._on_success()
                return result
            except Exception as exc:
                await self._on_failure()
                raise

        return wrapper

    async def _before_call(self) -> None:
        async with self._lock:
            if self._state == _CircuitState.OPEN:
                elapsed = time.monotonic() - (self._last_failure_time or 0)
                if elapsed >= self._recovery_timeout:
                    self._state = _CircuitState.HALF_OPEN
                    log.info("circuit_half_open", name=self._name)
                else:
                    from aegis_ai.exceptions import LLMGatewayError
                    raise LLMGatewayError(
                        f"Circuit breaker '{self._name}' is OPEN. "
                        f"Retry in {self._recovery_timeout - elapsed:.1f}s.",
                        details={"state": "OPEN", "breaker": self._name},
                    )

    async def _on_success(self) -> None:
        async with self._lock:
            prev = self._state
            self._failure_count = 0
            self._state = _CircuitState.CLOSED
            if prev != _CircuitState.CLOSED:
                log.info("circuit_closed", name=self._name)

    async def _on_failure(self) -> None:
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if (
                self._state != _CircuitState.OPEN
                and self._failure_count >= self._failure_threshold
            ):
                self._state = _CircuitState.OPEN
                log.warning(
                    "circuit_opened",
                    name=self._name,
                    failures=self._failure_count,
                )

    @property
    def state(self) -> _CircuitState:
        return self._state
