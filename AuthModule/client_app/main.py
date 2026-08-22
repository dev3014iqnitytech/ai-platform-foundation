"""
client_app.main
================
Client API (Agent A) — Production FastAPI Application.

This microservice demonstrates how an enterprise client application communicates
securely with the Aegis AI Security Gateway.  It acts as "Agent A" in the
Agent-to-Agent (A2A) pattern.

Endpoints
---------
GET  /                          Service info (no auth required)
GET  /api/public/status         Health / readiness check (no auth)
POST /api/secure/analyze        Secure AI analysis (requires end-user Bearer token)
GET  /api/secure/token-info     Decode and return the caller's token claims (debug)
GET  /health/live               Kubernetes liveness probe
GET  /health/ready              Kubernetes readiness probe

Authentication Flow
-------------------
1. End-user calls POST /api/secure/analyze with their Bearer token.
2. This service verifies the end-user token (via verify_end_user_auth).
3. It then fetches *its own* M2M SSO token from MockSSOTokenManager (cached, stable key).
4. It forwards the request to the Aegis Gateway with the M2M token.
5. The Gateway performs the full 9-step security pipeline and returns the result.

Configuration
-------------
All settings are loaded from environment variables / .env (see config.py).

Run locally:
    uvicorn main:app --port 8001 --reload

Environment:
    AEGIS_GATEWAY_URL=http://localhost:8080
    CLIENT_USER_JWT_SECRET=dev-secret-change-me
    (see config.py for full reference)
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import httpx
import jwt
import structlog
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

try:
    from client_app.config import ClientSettings, get_client_settings
    from client_app.token_manager import MockSSOTokenManager, get_token_manager
except ImportError:
    from config import ClientSettings, get_client_settings  # type: ignore[no-redef]
    from token_manager import MockSSOTokenManager, get_token_manager  # type: ignore[no-redef]

# ─────────────────────────────────────────────────────────────────────────────
# Structured logging (consistent with Aegis SDK)
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
# Application State (module-level singletons set during lifespan)
# ─────────────────────────────────────────────────────────────────────────────

_settings: Optional[ClientSettings] = None
_token_manager: Optional[MockSSOTokenManager] = None
_startup_time: Optional[float] = None


# ─────────────────────────────────────────────────────────────────────────────
# Lifespan
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise singletons on startup; clean up on shutdown."""
    global _settings, _token_manager, _startup_time

    _settings = get_client_settings()
    _token_manager = get_token_manager(_settings)
    _startup_time = time.monotonic()

    # Warm up the token cache so the first request isn't slow
    try:
        await _token_manager.get_token()
        logger.info(
            "client_startup_complete",
            gateway_url=_settings.aegis_gateway_url,
            environment=_settings.environment,
        )
    except Exception as exc:
        # Warm-up failure is non-fatal in development; fail fast in production
        if _settings.is_production():
            logger.critical("client_startup_token_warmup_failed", error=str(exc))
            raise
        logger.warning("client_startup_token_warmup_failed", error=str(exc))

    yield

    logger.info("client_shutdown")


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI Application
# ─────────────────────────────────────────────────────────────────────────────

_cfg = get_client_settings()

app = FastAPI(
    title="Client API (Agent A)",
    description=(
        "Enterprise Agent A — demonstrates secure Agent-to-Agent communication "
        "via the Aegis AI Security Gateway using SSO / OIDC M2M tokens.\n\n"
        "**Auth flow:** End-user token → verified locally → M2M token fetched "
        "from token cache → forwarded to Aegis Gateway."
    ),
    version="1.0.0",
    contact={"name": "Aegis AI SDK", "url": "https://github.com/your-org/aegis-ai"},
    license_info={"name": "MIT"},
    docs_url="/docs" if _cfg.is_development() else None,
    redoc_url="/redoc" if _cfg.is_development() else None,
    openapi_url="/openapi.json" if _cfg.is_development() else None,
    lifespan=lifespan,
)

# CORS — wide open in development, lock down in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _cfg.is_development() else [],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-Correlation-ID"],
)


# ─────────────────────────────────────────────────────────────────────────────
# Request Timing + Correlation ID Middleware
# ─────────────────────────────────────────────────────────────────────────────

@app.middleware("http")
async def request_middleware(request: Request, call_next):
    """Add X-Response-Time-Ms and propagate X-Correlation-ID."""
    correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
    start = time.monotonic()

    response = await call_next(request)

    latency_ms = round((time.monotonic() - start) * 1000, 2)
    response.headers["X-Response-Time-Ms"] = str(latency_ms)
    response.headers["X-Correlation-ID"] = correlation_id

    logger.info(
        "http_request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        latency_ms=latency_ms,
        correlation_id=correlation_id,
    )
    return response


# ─────────────────────────────────────────────────────────────────────────────
# Request / Response Models
# ─────────────────────────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    """Request body for POST /api/secure/analyze."""

    prompt: str = Field(
        ...,
        min_length=1,
        max_length=32_000,
        description="The user prompt to send to the AI model via the Aegis Gateway.",
    )
    agent_id: str = Field(
        "agent-b",
        description="Target agent identifier on the Gateway.",
    )
    provider: str = Field(
        "openai",
        pattern=r"^(openai|anthropic|google)$",
        description="LLM provider.",
    )
    model: str = Field(
        "gpt-4o",
        description="LLM model name.",
    )
    correlation_id: Optional[str] = Field(
        None,
        description="Optional caller-supplied correlation ID for end-to-end tracing.",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "prompt": "Summarise the OWASP LLM Top 10 in 3 bullet points.",
                "agent_id": "agent-b",
                "provider": "openai",
                "model": "gpt-4o",
            }
        }
    }


class AnalyzeResponse(BaseModel):
    """Response body for POST /api/secure/analyze."""

    correlation_id: str
    client_message: str
    gateway_response: Dict[str, Any]
    latency_ms: float


class TokenInfoResponse(BaseModel):
    """Response body for GET /api/secure/token-info."""

    subject: str
    email: Optional[str]
    issued_at: Optional[int]
    expires_at: Optional[int]
    roles: List[str]
    permissions: List[str]


# ─────────────────────────────────────────────────────────────────────────────
# Auth Dependency — End-User Token Verification
# ─────────────────────────────────────────────────────────────────────────────

async def verify_end_user_auth(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> Dict[str, Any]:
    """
    Dependency: verify the end-user's Bearer token.

    In development (CLIENT_USER_JWT_SECRET set):
        Validates a HS256 / RS256 JWT signed with the configured secret.

    With no secret configured (CLIENT_USER_JWT_SECRET empty):
        Passes through — useful during early integration testing.
        NOT suitable for production.

    Returns:
        Decoded JWT claims dict.

    Raises:
        HTTPException 401: Missing or invalid token.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header. Expected: 'Bearer <token>'",
            headers={"WWW-Authenticate": "Bearer"},
        )

    raw_token = authorization.removeprefix("Bearer ").strip()
    cfg = _settings or get_client_settings()

    # If no secret is configured, skip verification (dev/testing only)
    if not cfg.user_auth_enabled():
        logger.warning(
            "user_token_verification_skipped",
            reason="CLIENT_USER_JWT_SECRET not set",
        )
        # Return minimal claims from unverified payload (never trust for authz)
        try:
            unverified = jwt.decode(
                raw_token,
                options={"verify_signature": False},
                algorithms=["HS256", "RS256", "ES256"],
            )
            return unverified
        except Exception:
            # If even decoding fails, return a placeholder
            return {"sub": "anonymous", "note": "unverified"}

    # Verify the token
    decode_options: Dict[str, Any] = {
        "verify_exp": True,
        "verify_iat": True,
    }
    decode_kwargs: Dict[str, Any] = {
        "key": cfg.user_jwt_secret,
        "algorithms": [cfg.user_jwt_algorithm],
        "options": decode_options,
    }
    if cfg.user_jwt_issuer:
        decode_kwargs["issuer"] = cfg.user_jwt_issuer
    if cfg.user_jwt_audience:
        decode_kwargs["audience"] = cfg.user_jwt_audience

    try:
        claims = jwt.decode(raw_token, **decode_kwargs)
        logger.info("user_token_verified", sub=claims.get("sub"))
        return claims

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your token has expired. Please log in again.",
            headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
        )
    except jwt.InvalidTokenError as exc:
        logger.warning("user_token_invalid", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
        )


# ─────────────────────────────────────────────────────────────────────────────
# Helper — call Aegis Gateway with retry
# ─────────────────────────────────────────────────────────────────────────────

async def _call_gateway(
    body: Dict[str, Any],
    correlation_id: str,
) -> Dict[str, Any]:
    """
    POST to the Aegis Gateway with the Agent A M2M token.

    Retries on 429 / 503 with a 1-second delay (max 2 attempts).
    """
    cfg = _settings or get_client_settings()
    manager = _token_manager or get_token_manager(cfg)

    m2m_token = await manager.get_token()
    headers = {
        "Authorization": f"Bearer {m2m_token}",
        "Content-Type": "application/json",
        "X-Correlation-ID": correlation_id,
    }

    last_exc: Optional[Exception] = None

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        for attempt in range(1, 3):  # max 2 attempts
            try:
                logger.info(
                    "gateway_request",
                    url=cfg.invoke_url,
                    attempt=attempt,
                    correlation_id=correlation_id,
                )
                resp = await client.post(cfg.invoke_url, json=body, headers=headers)

                if resp.status_code == status.HTTP_401_UNAUTHORIZED:
                    raise HTTPException(
                        status_code=401,
                        detail={
                            "error": "GATEWAY_AUTH_FAILED",
                            "message": "Aegis Gateway rejected the Agent A M2M token.",
                            "hint": "Ensure the Gateway's SSO provider is configured to accept "
                                    "'https://mock-sso.local' as a trusted issuer.",
                        },
                    )
                if resp.status_code == status.HTTP_403_FORBIDDEN:
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": "GATEWAY_AUTHZ_FAILED",
                            "message": "Aegis Gateway: Agent A lacks the required permission.",
                            "required_permission": body.get("required_permission"),
                        },
                    )
                if resp.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
                    retry_after = int(resp.headers.get("Retry-After", "5"))
                    raise HTTPException(
                        status_code=429,
                        detail={
                            "error": "GATEWAY_RATE_LIMITED",
                            "message": "Aegis Gateway is rate limiting Agent A.",
                            "retry_after_seconds": retry_after,
                        },
                        headers={"Retry-After": str(retry_after)},
                    )
                if resp.status_code >= 500:
                    if attempt < 2:
                        import asyncio
                        await asyncio.sleep(1.0)
                        last_exc = Exception(f"Gateway 5xx: {resp.status_code}")
                        continue
                    raise HTTPException(
                        status_code=resp.status_code,
                        detail={
                            "error": "GATEWAY_ERROR",
                            "message": "Aegis Gateway returned a server error.",
                            "gateway_detail": _safe_json(resp),
                        },
                    )

                return resp.json()

            except HTTPException:
                raise
            except httpx.TimeoutException:
                raise HTTPException(
                    status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                    detail={
                        "error": "GATEWAY_TIMEOUT",
                        "message": "The Aegis Gateway did not respond within 30 seconds.",
                    },
                )
            except httpx.ConnectError:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "error": "GATEWAY_UNREACHABLE",
                        "message": (
                            f"Could not connect to Aegis Gateway at {cfg.aegis_gateway_url}. "
                            "Ensure 'docker-compose up -d' is running."
                        ),
                    },
                )
            except Exception as exc:
                last_exc = exc

    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail={"error": "GATEWAY_CALL_FAILED", "message": str(last_exc)},
    )


def _safe_json(response: httpx.Response) -> Any:
    """Return parsed JSON or raw text — never raises."""
    try:
        return response.json()
    except Exception:
        return response.text


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

# ── Root ──────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root():
    cfg = _settings or get_client_settings()
    return {
        "service": "Client API (Agent A)",
        "version": "1.0.0",
        "environment": cfg.environment,
        "docs": "/docs" if cfg.is_development() else "disabled",
        "gateway": cfg.aegis_gateway_url,
    }


# ── Public ────────────────────────────────────────────────────────────────────

@app.get(
    "/api/public/status",
    summary="Service status (no auth required)",
    tags=["Public"],
)
async def public_status():
    """
    Public health / readiness endpoint.

    Returns basic service information without requiring authentication.
    Safe to call from load balancers and monitoring tools.
    """
    cfg = _settings or get_client_settings()
    uptime = round(time.monotonic() - (_startup_time or 0), 1)
    return {
        "status": "ok",
        "service": "Client API (Agent A)",
        "version": "1.0.0",
        "environment": cfg.environment,
        "uptime_seconds": uptime,
        "gateway_url": cfg.aegis_gateway_url,
        "auth_required": False,
    }


# ── Secure ────────────────────────────────────────────────────────────────────

@app.post(
    "/api/secure/analyze",
    response_model=AnalyzeResponse,
    summary="Secure AI analysis via Aegis Gateway",
    tags=["Secure"],
    responses={
        200: {"description": "Successfully analyzed via the Aegis Security Gateway"},
        401: {"description": "End-user token missing or invalid"},
        403: {"description": "Agent A lacks the required IAM permission"},
        422: {"description": "Request validation error"},
        429: {"description": "Rate limited by the Aegis Gateway"},
        503: {"description": "Aegis Gateway unreachable"},
        504: {"description": "Aegis Gateway timed out"},
    },
)
async def secure_analyze(
    body: AnalyzeRequest,
    request: Request,
    user_claims: Dict[str, Any] = Depends(verify_end_user_auth),
):
    """
    Secure AI analysis endpoint — full Agent-to-Agent authentication flow.

    **Step 1:** Verify the end-user's Bearer token (``verify_end_user_auth``).
    **Step 2:** Fetch Agent A's M2M SSO token from the token cache.
    **Step 3:** Forward the request to the Aegis Security Gateway.
    **Step 4:** Return the gateway's response to the caller.

    The Aegis Gateway performs: Auth → AuthZ → Rate Limit → GuardRails →
    PII Masking → LLM Call → Response Validation → Audit.

    Requires: ``Authorization: Bearer <user-token>``
    """
    start = time.monotonic()

    # Resolve correlation ID: prefer caller-supplied, then header, then generate
    correlation_id = (
        body.correlation_id
        or request.headers.get("X-Correlation-ID")
        or str(uuid.uuid4())
    )

    logger.info(
        "secure_analyze_start",
        user_sub=user_claims.get("sub"),
        agent_id=body.agent_id,
        provider=body.provider,
        model=body.model,
        correlation_id=correlation_id,
    )

    gateway_body = {
        "agent_id": body.agent_id,
        "provider": body.provider,
        "model": body.model,
        "required_permission": "agents.call",
        "correlation_id": correlation_id,
        "messages": [
            {"role": "user", "content": body.prompt}
        ],
    }

    gateway_response = await _call_gateway(gateway_body, correlation_id)
    latency_ms = round((time.monotonic() - start) * 1000, 2)

    logger.info(
        "secure_analyze_complete",
        correlation_id=correlation_id,
        latency_ms=latency_ms,
    )

    return AnalyzeResponse(
        correlation_id=correlation_id,
        client_message="Successfully analyzed securely via Aegis Security Gateway",
        gateway_response=gateway_response,
        latency_ms=latency_ms,
    )


@app.get(
    "/api/secure/token-info",
    response_model=TokenInfoResponse,
    summary="Inspect your end-user token claims",
    tags=["Secure"],
)
async def token_info(
    user_claims: Dict[str, Any] = Depends(verify_end_user_auth),
):
    """
    Debug endpoint — decode and return the caller's JWT claims.

    Requires: ``Authorization: Bearer <user-token>``

    Useful for verifying that your token contains the correct claims
    before making calls to ``/api/secure/analyze``.
    """
    return TokenInfoResponse(
        subject=user_claims.get("sub", "unknown"),
        email=user_claims.get("email"),
        issued_at=user_claims.get("iat"),
        expires_at=user_claims.get("exp"),
        roles=user_claims.get("roles", []),
        permissions=user_claims.get("permissions", []),
    )


# ── Health Probes ─────────────────────────────────────────────────────────────

@app.get("/health/live", tags=["Health"], summary="Liveness probe")
async def liveness():
    """
    Kubernetes liveness probe.
    Returns 200 as long as the process is alive.
    """
    uptime = round(time.monotonic() - (_startup_time or 0), 1)
    return {"status": "alive", "uptime_seconds": uptime}


@app.get("/health/ready", tags=["Health"], summary="Readiness probe")
async def readiness():
    """
    Kubernetes readiness probe.
    Returns 200 when the token manager is initialised and the gateway URL is set.
    Returns 503 if the service is not yet ready to handle requests.
    """
    if _token_manager is None or _settings is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service not yet initialised",
        )
    return {
        "status": "ready",
        "gateway_url": _settings.aegis_gateway_url,
        "token_manager": "ok",
        "uptime_seconds": round(time.monotonic() - (_startup_time or 0), 1),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Entry point (for direct `python main.py` usage)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    cfg = get_client_settings()
    uvicorn.run(
        "main:app",
        host=cfg.client_host,
        port=cfg.client_port,
        reload=cfg.is_development(),
        log_level=cfg.log_level,
    )
