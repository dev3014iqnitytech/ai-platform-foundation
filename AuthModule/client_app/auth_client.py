"""
client_app.auth_client
=======================
Production Authentication Client — Username / Password → Token Exchange.

This client authenticates a user against the Aegis AI Security Gateway at
``AEGIS_GATEWAY_URL`` using their username and password.  The server now owns
the auth endpoints, so the same base URL is used for all three pipeline steps:

  Step 1: POST {GATEWAY}/api/auth/token   — password grant → RS256 JWT
  Step 2: GET  {GATEWAY}/api/auth/me      — validate token + return profile
  Step 3: GET  {GATEWAY}/v1/auth/token/verify — full Aegis pipeline introspect

Why was https://myCompany.com used originally?
----------------------------------------------
The earlier version pointed at an external IdP that would host /api/auth/token.
But those endpoints did not exist in THIS server.  The fix was to add them to
aegis_ai/server.py so the gateway is now a complete auth + pipeline server.
You can still override AUTH_API_BASE_URL to point at an external IdP if you
replace the mock _USER_STORE with a real database and real password hashing.

Loop-engineering pattern:
  Every network call is wrapped in a resilient retry loop with:
    - Exponential back-off with full jitter (prevents thundering herds)
    - Per-error-class retry policy (auth errors = no retry; network = retry)
    - Maximum attempt cap + total elapsed-time circuit breaker
    - Structured log on every attempt so failures are observable

Usage (standalone script):
    python auth_client.py
    python auth_client.py --username TestUser@gmail.com --password test@123
    python auth_client.py --json          # machine-readable output
    python auth_client.py --dry-run       # print request without sending

Usage (library):
    from client_app.auth_client import AuthClient
    client = AuthClient()
    result = asyncio.run(client.authenticate())
    print(result.access_token)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Load .env from the same directory (dev convenience)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=False)
except ImportError:
    pass

import httpx
import structlog

# ─────────────────────────────────────────────────────────────────────────────
# Logging (structured — consistent with Aegis SDK)
# ─────────────────────────────────────────────────────────────────────────────

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ]
)
logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

# These defaults match the user's specification.
# Override via environment variables or CLI args.
#
# AUTH_API_BASE_URL now defaults to the same Aegis Gateway URL because the
# gateway itself owns /api/auth/token and /api/auth/me (added to server.py).
# You can override AUTH_API_BASE_URL to point at an external IdP if needed.
_DEFAULT_GATEWAY_URL = os.environ.get(
    "AEGIS_GATEWAY_URL", "http://localhost:8080"
)
_DEFAULT_AUTH_BASE_URL = os.environ.get(
    "AUTH_API_BASE_URL", _DEFAULT_GATEWAY_URL   # ← same server by default
)
_DEFAULT_USERNAME = os.environ.get("AUTH_USERNAME", "TestUser@gmail.com")
_DEFAULT_PASSWORD = os.environ.get("AUTH_PASSWORD", "test@123")

# Retry loop configuration
_MAX_ATTEMPTS = int(os.environ.get("AUTH_MAX_ATTEMPTS", "10"))
_MAX_ELAPSED_SECONDS = float(os.environ.get("AUTH_MAX_ELAPSED_SECONDS", "120"))
_BACKOFF_BASE = float(os.environ.get("AUTH_BACKOFF_BASE", "1.0"))
_BACKOFF_CAP = float(os.environ.get("AUTH_BACKOFF_CAP", "30.0"))
_REQUEST_TIMEOUT = float(os.environ.get("AUTH_REQUEST_TIMEOUT", "15.0"))


# ─────────────────────────────────────────────────────────────────────────────
# Data Models
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TokenResponse:
    """Structured response from the authentication API."""
    access_token: str
    token_type: str = "Bearer"
    expires_in: int = 3600
    refresh_token: Optional[str] = None
    scope: Optional[str] = None
    # Enriched fields
    issued_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    username: str = ""
    gateway_verified: bool = False
    gateway_identity_id: Optional[str] = None
    gateway_tenant_id: Optional[str] = None
    gateway_roles: List[str] = field(default_factory=list)
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Redact the access token in summaries (show only first/last 8 chars)
        d["access_token_preview"] = (
            f"{self.access_token[:8]}...{self.access_token[-8:]}"
            if len(self.access_token) > 20
            else "***"
        )
        return d

    def to_safe_dict(self) -> Dict[str, Any]:
        """Same as to_dict but without the raw access_token."""
        d = self.to_dict()
        del d["access_token"]
        return d


@dataclass
class AuthAttemptResult:
    """Tracks every attempt in the retry loop."""
    attempt: int
    outcome: str          # "success" | "retry" | "abort"
    error_class: str = ""
    error_message: str = ""
    status_code: Optional[int] = None
    elapsed_ms: float = 0.0
    next_delay_seconds: float = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Retry Policy
# ─────────────────────────────────────────────────────────────────────────────

class RetryPolicy:
    """
    Determines whether an error should be retried and how long to wait.

    Non-retryable (abort immediately):
      - 400 Bad Request      — malformed credentials, bad payload
      - 401 Unauthorized     — wrong username/password (no point retrying)
      - 403 Forbidden        — account locked or insufficient permissions
      - 422 Unprocessable    — validation error in request

    Retryable (back off and retry):
      - 429 Too Many Requests — rate limited (honour Retry-After header)
      - 500, 502, 503, 504   — server-side errors (transient)
      - Network timeouts      — transient connectivity
      - DNS failures          — transient DNS
    """

    NON_RETRYABLE_STATUS_CODES = {400, 401, 403, 422}

    @classmethod
    def should_retry(
        cls,
        attempt: int,
        max_attempts: int,
        elapsed: float,
        max_elapsed: float,
        status_code: Optional[int],
        exc: Optional[Exception],
    ) -> bool:
        """Return True if this error warrants another attempt."""
        if attempt >= max_attempts:
            return False
        if elapsed >= max_elapsed:
            return False
        if status_code in cls.NON_RETRYABLE_STATUS_CODES:
            return False
        return True

    @classmethod
    def compute_delay(
        cls,
        attempt: int,
        base: float = _BACKOFF_BASE,
        cap: float = _BACKOFF_CAP,
        retry_after_hint: Optional[float] = None,
    ) -> float:
        """
        Full-jitter exponential back-off (Caron et al. 2015).

        delay = random(0, min(cap, base * 2^attempt))

        Using full jitter over equal jitter because it significantly reduces
        thundering-herd load on the server under load.
        """
        if retry_after_hint and retry_after_hint > 0:
            return min(retry_after_hint, cap)
        ceiling = min(cap, base * (2 ** attempt))
        return random.uniform(0, ceiling)


# ─────────────────────────────────────────────────────────────────────────────
# Auth Client
# ─────────────────────────────────────────────────────────────────────────────

class AuthClient:
    """
    Production Authentication Client.

    Authenticates a user against the Authentication SDK API at
    ``auth_base_url`` using username/password credentials, validates the
    resulting token via the Aegis Security Gateway, and returns a fully
    populated ``TokenResponse``.

    The entire sequence is wrapped in a loop-engineering retry mechanism that:
      - Retries on transient network and server errors
      - Aborts immediately on auth / validation failures (no point retrying)
      - Emits a structured log line on every attempt
      - Respects a total elapsed-time budget (not just attempt count)

    Example::

        client = AuthClient(
            username="TestUser@gmail.com",
            password="test@123",
            auth_base_url="https://myCompany.com",
        )
        result = asyncio.run(client.authenticate())
        print(result.access_token)
    """

    def __init__(
        self,
        username: str = _DEFAULT_USERNAME,
        password: str = _DEFAULT_PASSWORD,
        auth_base_url: str = _DEFAULT_AUTH_BASE_URL,
        gateway_url: str = _DEFAULT_GATEWAY_URL,
        max_attempts: int = _MAX_ATTEMPTS,
        max_elapsed_seconds: float = _MAX_ELAPSED_SECONDS,
        backoff_base: float = _BACKOFF_BASE,
        backoff_cap: float = _BACKOFF_CAP,
        request_timeout: float = _REQUEST_TIMEOUT,
        verify_with_gateway: bool = True,
    ) -> None:
        self.username = username
        self.password = password
        self.auth_base_url = auth_base_url.rstrip("/")
        self.gateway_url = gateway_url.rstrip("/")
        self.max_attempts = max_attempts
        self.max_elapsed_seconds = max_elapsed_seconds
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self.request_timeout = request_timeout
        self.verify_with_gateway = verify_with_gateway
        self.correlation_id = str(uuid.uuid4())

        self._log = logger.bind(
            username=self.username,
            auth_base_url=self.auth_base_url,
            correlation_id=self.correlation_id,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    async def authenticate(self) -> TokenResponse:
        """
        Authenticate and return a validated ``TokenResponse``.

        Runs the full loop-engineering retry sequence:
          1. POST /api/auth/token  (password grant)
          2. GET  /api/auth/me     (profile / token sanity check)
          3. POST gateway/v1/auth/token/verify  (Aegis Gateway introspect)

        Returns:
            ``TokenResponse`` with all fields populated.

        Raises:
            AuthenticationFailed: Credentials were wrong (non-retryable).
            MaxRetriesExceeded: Retry budget exhausted without success.
            RuntimeError: Unexpected failure.
        """
        self._log.info(
            "auth_client_start",
            max_attempts=self.max_attempts,
            max_elapsed_seconds=self.max_elapsed_seconds,
        )

        attempt_log: List[AuthAttemptResult] = []
        loop_start = time.monotonic()

        for attempt in range(1, self.max_attempts + 1):
            elapsed = time.monotonic() - loop_start
            attempt_start = time.monotonic()

            if elapsed >= self.max_elapsed_seconds:
                self._log.error(
                    "auth_elapsed_budget_exceeded",
                    elapsed_s=round(elapsed, 1),
                    budget_s=self.max_elapsed_seconds,
                )
                raise MaxRetriesExceeded(
                    f"Elapsed {elapsed:.1f}s exceeds budget {self.max_elapsed_seconds}s "
                    f"after {attempt - 1} attempts.",
                    attempt_log=attempt_log,
                )

            self._log.info(
                "auth_attempt_start",
                attempt=attempt,
                of=self.max_attempts,
                elapsed_s=round(elapsed, 1),
            )

            status_code: Optional[int] = None
            retry_after_hint: Optional[float] = None

            try:
                # ── Step 1: Token acquisition ──────────────────────────────
                raw_token_response = await self._step1_acquire_token()

                # ── Step 2: Profile validation ─────────────────────────────
                profile = await self._step2_validate_with_profile(
                    raw_token_response["access_token"]
                )

                # ── Step 3: Gateway introspection ──────────────────────────
                gateway_claims: Optional[Dict[str, Any]] = None
                if self.verify_with_gateway:
                    gateway_claims = await self._step3_gateway_verify(
                        raw_token_response["access_token"]
                    )

                # ── Build result ───────────────────────────────────────────
                result = self._build_token_response(
                    raw_token_response, profile, gateway_claims
                )

                attempt_log.append(AuthAttemptResult(
                    attempt=attempt,
                    outcome="success",
                    elapsed_ms=round((time.monotonic() - attempt_start) * 1000, 2),
                ))

                total_elapsed = round(time.monotonic() - loop_start, 2)
                self._log.info(
                    "auth_success",
                    attempt=attempt,
                    total_elapsed_s=total_elapsed,
                    gateway_verified=result.gateway_verified,
                    identity_id=result.gateway_identity_id,
                )
                return result

            # ── Non-retryable errors — abort immediately ───────────────────
            except AuthenticationFailed as exc:
                attempt_log.append(AuthAttemptResult(
                    attempt=attempt,
                    outcome="abort",
                    error_class="AuthenticationFailed",
                    error_message=str(exc),
                    status_code=exc.status_code,
                    elapsed_ms=round((time.monotonic() - attempt_start) * 1000, 2),
                ))
                self._log.error(
                    "auth_credentials_rejected",
                    attempt=attempt,
                    status_code=exc.status_code,
                    error=str(exc),
                )
                raise

            # ── Retryable errors ───────────────────────────────────────────
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                retry_after_hint = self._parse_retry_after(exc.response)

                if not RetryPolicy.should_retry(
                    attempt, self.max_attempts,
                    time.monotonic() - loop_start, self.max_elapsed_seconds,
                    status_code, exc,
                ):
                    attempt_log.append(AuthAttemptResult(
                        attempt=attempt, outcome="abort",
                        error_class="HTTPStatusError",
                        error_message=f"HTTP {status_code}",
                        status_code=status_code,
                        elapsed_ms=round((time.monotonic() - attempt_start) * 1000, 2),
                    ))
                    raise MaxRetriesExceeded(
                        f"Non-retryable HTTP {status_code} on attempt {attempt}",
                        attempt_log=attempt_log,
                    ) from exc

                delay = RetryPolicy.compute_delay(attempt, self.backoff_base, self.backoff_cap, retry_after_hint)
                attempt_log.append(AuthAttemptResult(
                    attempt=attempt, outcome="retry",
                    error_class="HTTPStatusError",
                    error_message=f"HTTP {status_code}",
                    status_code=status_code,
                    elapsed_ms=round((time.monotonic() - attempt_start) * 1000, 2),
                    next_delay_seconds=round(delay, 2),
                ))
                self._log.warning(
                    "auth_attempt_failed_retrying",
                    attempt=attempt,
                    status_code=status_code,
                    next_delay_s=round(delay, 2),
                )
                await asyncio.sleep(delay)

            except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
                if not RetryPolicy.should_retry(
                    attempt, self.max_attempts,
                    time.monotonic() - loop_start, self.max_elapsed_seconds,
                    None, exc,
                ):
                    raise MaxRetriesExceeded(
                        f"Network error on attempt {attempt}: {exc}",
                        attempt_log=attempt_log,
                    ) from exc

                delay = RetryPolicy.compute_delay(attempt, self.backoff_base, self.backoff_cap)
                attempt_log.append(AuthAttemptResult(
                    attempt=attempt, outcome="retry",
                    error_class=type(exc).__name__,
                    error_message=str(exc),
                    elapsed_ms=round((time.monotonic() - attempt_start) * 1000, 2),
                    next_delay_seconds=round(delay, 2),
                ))
                self._log.warning(
                    "auth_network_error_retrying",
                    attempt=attempt,
                    error_class=type(exc).__name__,
                    error=str(exc)[:120],
                    next_delay_s=round(delay, 2),
                )
                await asyncio.sleep(delay)

            except Exception as exc:
                # Unexpected — log and abort (do not silently retry unknown errors)
                attempt_log.append(AuthAttemptResult(
                    attempt=attempt, outcome="abort",
                    error_class=type(exc).__name__,
                    error_message=str(exc),
                    elapsed_ms=round((time.monotonic() - attempt_start) * 1000, 2),
                ))
                self._log.exception("auth_unexpected_error", attempt=attempt, error=str(exc))
                raise

        # Fell through all attempts
        raise MaxRetriesExceeded(
            f"Authentication failed after {self.max_attempts} attempts.",
            attempt_log=attempt_log,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Pipeline Steps
    # ─────────────────────────────────────────────────────────────────────────

    async def _step1_acquire_token(self) -> Dict[str, Any]:
        """
        POST /api/auth/token — Resource Owner Password Credentials grant.

        Request body:
            {
              "grant_type": "password",
              "username": "TestUser@gmail.com",
              "password": "test@123",
              "scope": "openid profile email"
            }

        Expected response (200 OK):
            {
              "access_token": "eyJ...",
              "token_type": "Bearer",
              "expires_in": 3600,
              "refresh_token": "...",
              "scope": "openid profile email"
            }
        """
        url = f"{self.auth_base_url}/api/auth/token"

        body = {
            "grant_type": "password",
            "username": self.username,
            "password": self.password,
            "scope": "openid profile email agents.call",
        }

        self._log.debug("step1_token_request", url=url, grant_type="password")

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.request_timeout, connect=5.0),
            verify=True,                        # TLS certificate verification
            follow_redirects=False,
            headers={
                "X-Correlation-ID": self.correlation_id,
                "X-Client-ID": "aegis-auth-client/1.0",
            },
        ) as client:
            resp = await client.post(
                url,
                json=body,
                headers={"Content-Type": "application/json"},
            )

        if resp.status_code in (400, 401):
            body_json = self._safe_json(resp)
            error_desc = (
                body_json.get("error_description")
                or body_json.get("message")
                or f"HTTP {resp.status_code}"
            )
            raise AuthenticationFailed(
                f"Credentials rejected by {self.auth_base_url}: {error_desc}",
                status_code=resp.status_code,
                response_body=body_json,
            )

        if resp.status_code == 403:
            raise AuthenticationFailed(
                f"Account forbidden (locked or insufficient permissions): {self.auth_base_url}",
                status_code=403,
                response_body=self._safe_json(resp),
            )

        resp.raise_for_status()  # Raises HTTPStatusError for 4xx/5xx not handled above

        data = resp.json()
        if "access_token" not in data:
            raise ValueError(
                f"Auth API response missing 'access_token' field. Got: {list(data.keys())}"
            )

        self._log.info(
            "step1_token_acquired",
            token_type=data.get("token_type", "Bearer"),
            expires_in=data.get("expires_in"),
            has_refresh=bool(data.get("refresh_token")),
        )
        return data

    async def _step2_validate_with_profile(self, access_token: str) -> Dict[str, Any]:
        """
        GET /api/auth/me — validate the token and return user profile.

        This call serves as a client-side token sanity check.
        If the API returns 401 here, the token was issued but is not accepted
        by the same server — a signal of a server-side bug worth logging.

        Expected response (200 OK):
            {
              "sub": "user-123",
              "email": "TestUser@gmail.com",
              "name": "Test User",
              "roles": ["user"],
              "tenant_id": "enterprise-tenant-01"
            }
        """
        url = f"{self.auth_base_url}/api/auth/me"

        self._log.debug("step2_profile_request", url=url)

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.request_timeout, connect=5.0),
            verify=True,
            follow_redirects=False,
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-Correlation-ID": self.correlation_id,
            },
        ) as client:
            resp = await client.get(url)

        if resp.status_code == 404:
            # /api/auth/me may not exist on all servers — skip gracefully
            self._log.warning(
                "step2_profile_endpoint_not_found",
                url=url,
                note="Token accepted; profile endpoint optional.",
            )
            return {
                "sub": self.username,
                "email": self.username,
                "_profile_skipped": True,
            }

        if resp.status_code == 401:
            raise AuthenticationFailed(
                "Token was acquired but rejected by /api/auth/me — "
                "server-side token issue. Aborting.",
                status_code=401,
                response_body=self._safe_json(resp),
            )

        resp.raise_for_status()
        profile = resp.json()

        self._log.info(
            "step2_profile_validated",
            sub=profile.get("sub"),
            email=profile.get("email"),
            roles=profile.get("roles", []),
        )
        return profile

    async def _step3_gateway_verify(self, access_token: str) -> Optional[Dict[str, Any]]:
        """
        GET {GATEWAY}/v1/auth/token/verify — Aegis Gateway token introspection.

        If the Gateway is unreachable or returns an error, this step is
        non-fatal — the token is still returned (gateway_verified=False).

        Expected response (200 OK):
            {
              "identity_id": "agent-a-client-id",
              "agent_id": null,
              "tenant_id": "enterprise-tenant-01",
              "auth_method": "sso",
              "roles": ["AGENT_CALLER"],
              "expires_at": "2026-08-20T15:00:00Z"
            }
        """
        url = f"{self.gateway_url}/v1/auth/token/verify"

        self._log.debug("step3_gateway_verify", url=url)

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=3.0),
                verify=True,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "X-Correlation-ID": self.correlation_id,
                },
            ) as client:
                resp = await client.get(url)

            if resp.status_code == 200:
                claims = resp.json()
                self._log.info(
                    "step3_gateway_verified",
                    identity_id=claims.get("identity_id"),
                    tenant_id=claims.get("tenant_id"),
                    roles=claims.get("roles", []),
                )
                return claims
            else:
                self._log.warning(
                    "step3_gateway_verify_rejected",
                    status_code=resp.status_code,
                    detail=self._safe_json(resp),
                    note="Continuing with token — gateway verification optional.",
                )
                return None

        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            self._log.warning(
                "step3_gateway_unreachable",
                gateway_url=self.gateway_url,
                error=str(exc)[:120],
                note="Gateway is optional at this stage. Token still returned.",
            )
            return None

    # ─────────────────────────────────────────────────────────────────────────
    # Result Builder
    # ─────────────────────────────────────────────────────────────────────────

    def _build_token_response(
        self,
        raw: Dict[str, Any],
        profile: Dict[str, Any],
        gateway_claims: Optional[Dict[str, Any]],
    ) -> TokenResponse:
        """Assemble the final TokenResponse from all three pipeline steps."""
        return TokenResponse(
            access_token=raw["access_token"],
            token_type=raw.get("token_type", "Bearer"),
            expires_in=raw.get("expires_in", 3600),
            refresh_token=raw.get("refresh_token"),
            scope=raw.get("scope"),
            issued_at=datetime.now(timezone.utc).isoformat(),
            username=profile.get("email") or profile.get("sub") or self.username,
            gateway_verified=gateway_claims is not None,
            gateway_identity_id=gateway_claims.get("identity_id") if gateway_claims else None,
            gateway_tenant_id=gateway_claims.get("tenant_id") if gateway_claims else None,
            gateway_roles=gateway_claims.get("roles", []) if gateway_claims else [],
            correlation_id=self.correlation_id,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _safe_json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except Exception:
            return {"raw": response.text[:500]}

    @staticmethod
    def _parse_retry_after(response: httpx.Response) -> Optional[float]:
        """Parse Retry-After header (seconds or HTTP-date)."""
        header = response.headers.get("Retry-After", "")
        if header.isdigit():
            return float(header)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────────────────────

class AuthenticationFailed(Exception):
    """Raised when the server explicitly rejects credentials (non-retryable)."""
    def __init__(
        self,
        message: str,
        status_code: int = 401,
        response_body: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class MaxRetriesExceeded(Exception):
    """Raised when the retry loop exhausts all attempts or the time budget."""
    def __init__(self, message: str, attempt_log: Optional[List[AuthAttemptResult]] = None) -> None:
        super().__init__(message)
        self.attempt_log = attempt_log or []


# ─────────────────────────────────────────────────────────────────────────────
# Output Formatting
# ─────────────────────────────────────────────────────────────────────────────

def _print_success(result: TokenResponse, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result.to_safe_dict(), indent=2, default=str))
        return

    token_preview = (
        f"{result.access_token[:12]}...{result.access_token[-8:]}"
        if len(result.access_token) > 24 else "***"
    )
    expires_at = datetime.fromtimestamp(
        time.time() + result.expires_in, tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S UTC")

    print("\n" + "═" * 64)
    print("✅  AUTHENTICATION SUCCESSFUL")
    print("═" * 64)
    print(f"  Username           : {result.username}")
    print(f"  Token Type         : {result.token_type}")
    print(f"  Access Token       : {token_preview}")
    print(f"  Expires In         : {result.expires_in}s  ({expires_at})")
    print(f"  Has Refresh Token  : {'Yes' if result.refresh_token else 'No'}")
    print(f"  Scope              : {result.scope or '(not set)'}")
    print(f"  Issued At          : {result.issued_at}")
    print(f"  Correlation ID     : {result.correlation_id}")
    print()
    if result.gateway_verified:
        print(f"  ✅ Gateway Verified")
        print(f"     Identity ID    : {result.gateway_identity_id}")
        print(f"     Tenant ID      : {result.gateway_tenant_id}")
        print(f"     Roles          : {result.gateway_roles}")
    else:
        print("  ⚠️  Gateway verification skipped (gateway unreachable or disabled)")
    print("═" * 64)


def _print_error(message: str, json_output: bool, details: Optional[Any] = None) -> None:
    if json_output:
        print(json.dumps({
            "error": "AUTHENTICATION_FAILED",
            "message": message,
            "details": details or {},
        }, indent=2, default=str))
        return
    print(f"\n❌  {message}")
    if details:
        print(f"    Details: {json.dumps(details, default=str, indent=2)}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auth_client",
        description=(
            "Production Authentication Client — Username/Password → Token Exchange.\n"
            "Connects to https://myCompany.com, authenticates the user, validates\n"
            "the token via the Aegis Gateway, and returns the token response.\n\n"
            "Loop-engineering: retries transient errors up to --max-attempts times."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Credentials
    creds = parser.add_argument_group("Credentials")
    creds.add_argument(
        "--username",
        default=_DEFAULT_USERNAME,
        help=f"Username / email (default: {_DEFAULT_USERNAME})",
    )
    creds.add_argument(
        "--password",
        default=_DEFAULT_PASSWORD,
        help="Password (default: test@123)",
    )

    # Endpoints
    endpoints = parser.add_argument_group("Endpoints")
    endpoints.add_argument(
        "--auth-url",
        default=_DEFAULT_AUTH_BASE_URL,
        dest="auth_url",
        metavar="URL",
        help=f"Authentication API base URL (default: {_DEFAULT_AUTH_BASE_URL})",
    )
    endpoints.add_argument(
        "--gateway-url",
        default=_DEFAULT_GATEWAY_URL,
        dest="gateway_url",
        metavar="URL",
        help=f"Aegis Gateway base URL (default: {_DEFAULT_GATEWAY_URL})",
    )
    endpoints.add_argument(
        "--no-gateway",
        action="store_true",
        help="Skip Aegis Gateway token verification step",
    )

    # Retry loop
    retry = parser.add_argument_group("Retry Loop")
    retry.add_argument(
        "--max-attempts",
        type=int,
        default=_MAX_ATTEMPTS,
        metavar="N",
        help=f"Maximum number of retry attempts (default: {_MAX_ATTEMPTS})",
    )
    retry.add_argument(
        "--max-elapsed",
        type=float,
        default=_MAX_ELAPSED_SECONDS,
        dest="max_elapsed",
        metavar="SECONDS",
        help=f"Total time budget for all retries in seconds (default: {_MAX_ELAPSED_SECONDS})",
    )
    retry.add_argument(
        "--backoff-base",
        type=float,
        default=_BACKOFF_BASE,
        dest="backoff_base",
        metavar="SECONDS",
        help=f"Exponential backoff base delay in seconds (default: {_BACKOFF_BASE})",
    )

    # Output
    output = parser.add_argument_group("Output")
    output.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Output machine-readable JSON (no decorations or log lines)",
    )
    output.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be sent without making any network calls",
    )

    return parser


async def _main_async(args: argparse.Namespace) -> int:
    """Async main — returns process exit code."""

    if args.dry_run:
        payload = {
            "dry_run": True,
            "step1": {
                "url": f"{args.auth_url}/api/auth/token",
                "method": "POST",
                "body": {
                    "grant_type": "password",
                    "username": args.username,
                    "password": "***REDACTED***",
                    "scope": "openid profile email agents.call",
                },
            },
            "step2": {
                "url": f"{args.auth_url}/api/auth/me",
                "method": "GET",
                "headers": {"Authorization": "Bearer <token>"},
            },
            "step3_gateway": {
                "url": f"{args.gateway_url}/v1/auth/token/verify",
                "method": "GET",
                "enabled": not args.no_gateway,
            },
            "retry_policy": {
                "max_attempts": args.max_attempts,
                "max_elapsed_seconds": args.max_elapsed,
                "backoff_base": args.backoff_base,
                "strategy": "full-jitter exponential backoff (Caron et al. 2015)",
            },
        }
        if args.json_output:
            print(json.dumps(payload, indent=2))
        else:
            print("\n🧪 DRY RUN — would execute:")
            print(json.dumps(payload, indent=2))
        return 0

    client = AuthClient(
        username=args.username,
        password=args.password,
        auth_base_url=args.auth_url,
        gateway_url=args.gateway_url,
        max_attempts=args.max_attempts,
        max_elapsed_seconds=args.max_elapsed,
        backoff_base=args.backoff_base,
        verify_with_gateway=not args.no_gateway,
    )

    if not args.json_output:
        print(f"\n🔐 Authenticating {args.username} → {args.auth_url}")
        print(f"   Max attempts  : {args.max_attempts}")
        print(f"   Time budget   : {args.max_elapsed}s")
        print(f"   Gateway verify: {'enabled' if not args.no_gateway else 'disabled'}")

    try:
        result = await client.authenticate()
        _print_success(result, args.json_output)
        return 0

    except AuthenticationFailed as exc:
        _print_error(
            str(exc),
            args.json_output,
            details={"status_code": exc.status_code, "response": exc.response_body},
        )
        return 1

    except MaxRetriesExceeded as exc:
        _print_error(
            str(exc),
            args.json_output,
            details={
                "attempts": [
                    {"attempt": a.attempt, "outcome": a.outcome,
                     "error": a.error_message, "status": a.status_code}
                    for a in exc.attempt_log
                ]
            },
        )
        return 2


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(asyncio.run(_main_async(args)))


if __name__ == "__main__":
    main()
