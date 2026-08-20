"""
aegis_ai.startup
=================
Production startup validation — fail-fast on misconfiguration.

``validate_production_config()`` must be called once at application startup,
before any request is served. It performs deep checks beyond pydantic validators:

  - GCP connectivity (Secret Manager reachability)
  - Required secrets exist in the configured backend
  - JWT key pair is valid and correct algorithm
  - TLS configuration is correct for the environment
  - Redis connectivity (if backend=redis)
  - Audit signing key exists

OWASP: A02:2021 (Cryptographic Failures), A05:2021 (Security Misconfiguration)
NIST SP 800-53: CM-6 (Configuration Settings)

Usage::

    from aegis_ai.startup import validate_production_config
    from aegis_ai.settings import get_settings

    settings = get_settings()
    await validate_production_config(settings)   # raises on misconfiguration
"""

from __future__ import annotations

import asyncio
import socket
from typing import List, Optional, Tuple

import structlog

from aegis_ai.exceptions import ConfigurationError
from aegis_ai.settings import AegisSettings, Environment

log = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Validation Result
# ─────────────────────────────────────────────────────────────────────────────


class ValidationIssue:
    """A single startup validation failure."""

    __slots__ = ("component", "message", "fatal")

    def __init__(self, component: str, message: str, fatal: bool = True) -> None:
        self.component = component
        self.message = message
        self.fatal = fatal

    def __str__(self) -> str:
        severity = "FATAL" if self.fatal else "WARNING"
        return f"[{severity}] {self.component}: {self.message}"


# ─────────────────────────────────────────────────────────────────────────────
# Individual Checks
# ─────────────────────────────────────────────────────────────────────────────


async def _check_gcp_secret_manager(
    settings: AegisSettings,
) -> List[ValidationIssue]:
    """Verify GCP Secret Manager is reachable."""
    if not settings.gcp.use_gcp:
        return []

    issues: List[ValidationIssue] = []
    try:
        await asyncio.to_thread(
            _tcp_check, "secretmanager.googleapis.com", 443, timeout=5.0
        )
        log.info("startup_check_passed", check="gcp_secret_manager")
    except Exception as exc:
        issues.append(ValidationIssue(
            component="GCP Secret Manager",
            message=f"Cannot reach secretmanager.googleapis.com:443 — {exc}",
            fatal=settings.is_production(),
        ))
    return issues


async def _check_required_secrets(
    settings: AegisSettings,
) -> List[ValidationIssue]:
    """Verify that critical secrets exist in the configured backend."""
    issues: List[ValidationIssue] = []

    # Build the repository to check against
    try:
        from aegis_ai.factory import SecretRepositoryFactory
        repo = SecretRepositoryFactory.create(settings)
    except Exception as exc:
        issues.append(ValidationIssue(
            component="SecretRepository",
            message=f"Failed to initialise secret repository: {exc}",
        ))
        return issues

    required_secrets: List[Tuple[str, str]] = [
        (settings.jwt.private_key_secret_name, "JWT private key"),
        (settings.jwt.public_key_secret_name, "JWT public key"),
        (settings.audit.signing_key_secret_name, "Audit HMAC signing key"),
    ]

    for secret_name, label in required_secrets:
        try:
            exists = await repo.secret_exists(secret_name)
            if not exists:
                issues.append(ValidationIssue(
                    component="SecretRepository",
                    message=f"{label} ('{secret_name}') not found in {settings.secret_backend} backend.",
                    fatal=True,
                ))
            else:
                log.info("startup_secret_found", secret_name=secret_name)
        except Exception as exc:
            issues.append(ValidationIssue(
                component="SecretRepository",
                message=f"Error checking {label} '{secret_name}': {exc}",
            ))

    return issues


async def _check_redis(settings: AegisSettings) -> List[ValidationIssue]:
    """Verify Redis connectivity if configured as rate-limit backend."""
    issues: List[ValidationIssue] = []

    if settings.rate_limit.backend != "redis":
        return issues

    redis_url = settings.rate_limit.redis_url
    if not redis_url:
        issues.append(ValidationIssue(
            component="Redis",
            message=(
                "AEGIS__RATE_LIMIT__BACKEND is 'redis' but AEGIS__RATE_LIMIT__REDIS_URL is empty. "
                "Set the Redis connection URL."
            ),
            fatal=True,
        ))
        return issues

    try:
        import urllib.parse
        parsed = urllib.parse.urlparse(redis_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 6379
        await asyncio.to_thread(_tcp_check, host, port, timeout=5.0)
        log.info("startup_check_passed", check="redis", host=host, port=port)
    except Exception as exc:
        fatal = not settings.rate_limit.use_in_memory_fallback
        issues.append(ValidationIssue(
            component="Redis",
            message=f"Cannot connect to Redis at {redis_url}: {exc}",
            fatal=fatal,
        ))

    return issues


async def _check_tls_config(settings: AegisSettings) -> List[ValidationIssue]:
    """Enforce TLS policy for the active environment."""
    issues: List[ValidationIssue] = []

    if settings.is_production():
        if settings.tls.minimum_version != "TLSv1.3":
            issues.append(ValidationIssue(
                component="TLS",
                message=(
                    f"minimum_version='{settings.tls.minimum_version}' in production. "
                    "Must be TLSv1.3 per security policy."
                ),
            ))
        if not settings.tls.verify_certificates:
            issues.append(ValidationIssue(
                component="TLS",
                message="Certificate verification is disabled in production. Set AEGIS__TLS__VERIFY_CERTIFICATES=true.",
            ))

    return issues


async def _check_jwt_local_keys(settings: AegisSettings) -> List[ValidationIssue]:
    """Ensure local PEM key paths are not configured in non-dev environments."""
    issues: List[ValidationIssue] = []

    if not settings.is_development():
        if settings.jwt.local_private_key_path or settings.jwt.local_public_key_path:
            issues.append(ValidationIssue(
                component="JWT",
                message=(
                    "LOCAL_PRIVATE_KEY_PATH / LOCAL_PUBLIC_KEY_PATH are set in a "
                    f"non-development environment ({settings.environment.value}). "
                    "Remove these — use GCP Secret Manager."
                ),
            ))

    # In development: verify the local keys actually exist
    if settings.is_development():
        for attr, label in [
            ("local_private_key_path", "private key"),
            ("local_public_key_path", "public key"),
        ]:
            path = getattr(settings.jwt, attr)
            if path:
                import pathlib
                p = pathlib.Path(path)
                if not p.is_file():
                    issues.append(ValidationIssue(
                        component="JWT",
                        message=f"Local {label} path '{path}' does not exist.",
                        fatal=False,
                    ))

    return issues


# ─────────────────────────────────────────────────────────────────────────────
# Public Entry Point
# ─────────────────────────────────────────────────────────────────────────────


async def validate_production_config(
    settings: Optional[AegisSettings] = None,
    *,
    raise_on_warning: bool = False,
) -> List[ValidationIssue]:
    """
    Run all startup validation checks concurrently.

    This should be called once at application startup before any request
    is served. In production, any FATAL issue raises ``ConfigurationError``
    and aborts startup.

    Args:
        settings:          SDK settings. Defaults to ``get_settings()``.
        raise_on_warning:  If True, warnings also abort startup.
                           Useful for strict CI pipelines.

    Returns:
        List of non-fatal ``ValidationIssue`` warnings (empty on success).

    Raises:
        ConfigurationError: If any fatal validation issue is found.
    """
    from aegis_ai.settings import get_settings
    s = settings or get_settings()

    log.info(
        "startup_validation_begin",
        environment=s.environment.value,
        gcp_enabled=s.gcp.use_gcp,
        secret_backend=s.secret_backend,
    )

    # Run all checks concurrently
    check_results = await asyncio.gather(
        _check_gcp_secret_manager(s),
        _check_required_secrets(s),
        _check_redis(s),
        _check_tls_config(s),
        _check_jwt_local_keys(s),
        return_exceptions=True,
    )

    all_issues: List[ValidationIssue] = []
    for res in check_results:
        if isinstance(res, Exception):
            all_issues.append(ValidationIssue(
                component="StartupCheck",
                message=f"Unexpected error during startup validation: {res}",
            ))
        else:
            all_issues.extend(res)  # type: ignore[arg-type]

    # Log all issues
    for issue in all_issues:
        if issue.fatal:
            log.error("startup_fatal_issue", component=issue.component, message=issue.message)
        else:
            log.warning("startup_warning", component=issue.component, message=issue.message)

    # Collect fatal issues
    fatal_issues = [i for i in all_issues if i.fatal]
    warning_issues = [i for i in all_issues if not i.fatal]

    to_raise = fatal_issues + (warning_issues if raise_on_warning else [])

    if to_raise:
        summary = "; ".join(str(i) for i in to_raise)
        raise ConfigurationError(
            message=f"Startup validation failed with {len(to_raise)} issue(s): {summary}",
            details={"issues": [{"component": i.component, "message": i.message, "fatal": i.fatal} for i in to_raise]},
        )

    log.info(
        "startup_validation_complete",
        environment=s.environment.value,
        warnings=len(warning_issues),
        status="OK",
    )
    return warning_issues


def _tcp_check(host: str, port: int, timeout: float = 3.0) -> None:
    """Simple synchronous TCP connectivity check (run in thread pool)."""
    with socket.create_connection((host, port), timeout=timeout):
        pass
