"""
aegis_ai.server
================
FastAPI ASGI application — the HTTP entry point for the Aegis AI SDK
when deployed as a microservice.

Exposes:
  POST /api/auth/token           — Password grant → RS256 JWT token
  GET  /api/auth/me              — Validate token + return user profile
  POST /v1/agent/invoke          — Full security pipeline (auth → guard → LLM)
  GET  /v1/auth/token/verify     — Token verification only (no LLM call)
  GET  /health/live              — Kubernetes liveness probe
  GET  /health/ready             — Kubernetes readiness probe (deep check)
  GET  /health/startup           — Kubernetes startup probe

Environment:
  AEGIS_ENV=development|staging|production   (default: production)
  HOST=0.0.0.0   PORT=8080   WORKERS=1

OWASP: A05:2021 Security Misconfiguration — startup validator enforced.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import structlog
from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from aegis_ai.exceptions import (
    AegisBaseError,
    AuthenticationError,
    AuthorizationError,
    GuardRailViolationError,
    RateLimitExceededError,
)
from aegis_ai.factory import PipelineFactory
from aegis_ai.observability.health_check import HealthCheck, HealthStatus
from aegis_ai.proxy.llm_gateway import LLMMessage, LLMRequest
from aegis_ai.settings import get_settings
from aegis_ai.startup import validate_production_config

logger = structlog.get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Application State
# ─────────────────────────────────────────────────────────────────────────────

_pipeline = None
_health_check = None
_startup_time = None
_jwt_handler = None  # Shared JWTHandler — used by /api/auth/* endpoints


# ─────────────────────────────────────────────────────────────────────────────
# External IdP Configuration
# ─────────────────────────────────────────────────────────────────────────────
# EXTERNAL_IDP_URL: base URL of the corporate IdP that validates credentials.
#
# Development / Test  — run the mock service:
#   uvicorn mocks.mock_idp_server:app --port 9000
#   EXTERNAL_IDP_URL=http://localhost:9000
#
# Production  — set to the real corporate IdP:
#   EXTERNAL_IDP_URL=https://mycompany.com
#
# The gateway calls: POST {EXTERNAL_IDP_URL}/authenticate
_EXTERNAL_IDP_URL: str = os.environ.get(
    "EXTERNAL_IDP_URL", "http://localhost:9000"
).rstrip("/")



@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Async lifespan context — runs startup/shutdown logic once.
    Replaces deprecated @app.on_event("startup").
    """
    global _pipeline, _health_check, _startup_time, _jwt_handler

    settings = get_settings()
    log = logger.bind(
        environment=settings.environment.value,
        service="aegis-ai",
    )

    log.info("server_starting", version=app.version)
    _startup_time = time.monotonic()

    # ── Startup validation (fail-fast) ────────────────────────────────────────
    try:
        warnings = await validate_production_config(settings)
        for w in warnings:
            log.warning("startup_warning", component=w.component, message=w.message)
    except Exception as exc:
        log.critical("startup_validation_failed", error=str(exc))
        raise

    # ── Build pipeline ────────────────────────────────────────────────────────
    _pipeline = PipelineFactory.create(settings=settings)
    _health_check = HealthCheck(settings=settings, version=app.version)

    # ── Initialise JWTHandler for /api/auth/* endpoints ───────────────────────
    from aegis_ai.auth.jwt_handler import JWTHandler
    _jwt_handler = JWTHandler(settings=settings)

    log.info(
        "server_ready",
        environment=settings.environment.value,
        circuit_breaker=_pipeline.circuit_breaker_state,
    )

    yield  # ← Application handles requests here

    # ── Graceful shutdown ─────────────────────────────────────────────────────
    if _pipeline:
        await _pipeline.drain_event_bus()
    log.info("server_shutdown_complete")


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI Application
# ─────────────────────────────────────────────────────────────────────────────

settings = get_settings()

app = FastAPI(
    title="Aegis AI Security Gateway",
    description=(
        "Enterprise AI Security & Governance SDK — "
        "Authentication, Authorization, GuardRails, Zero-Retention LLM Proxy, Audit Trail. "
        "OWASP LLM Top 10 coverage: LLM01–LLM10."
    ),
    version="1.2.0",
    docs_url="/docs" if settings.is_development() else None,   # Disable Swagger in prod
    redoc_url="/redoc" if settings.is_development() else None,
    openapi_url="/openapi.json" if not settings.is_production() else None,
    lifespan=lifespan,
)

# ── Middleware ────────────────────────────────────────────────────────────────

# Trusted host validation (prevents Host header injection)
_allowed_hosts = os.getenv("ALLOWED_HOSTS", "*").split(",")
if _allowed_hosts != ["*"]:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts)

# CORS — very restrictive in production
_cors_origins = os.getenv("CORS_ORIGINS", "").split(",") if os.getenv("CORS_ORIGINS") else []
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["POST", "GET"],
    allow_headers=["Authorization", "Content-Type", "X-Correlation-ID"],
    max_age=600,
)


# ── Request timing middleware ─────────────────────────────────────────────────

@app.middleware("http")
async def add_timing_header(request: Request, call_next):
    """Add X-Response-Time-Ms header and structured request logging."""
    start = time.monotonic()
    correlation_id = request.headers.get("X-Correlation-ID", "")

    response = await call_next(request)

    latency_ms = round((time.monotonic() - start) * 1000, 2)
    response.headers["X-Response-Time-Ms"] = str(latency_ms)
    if correlation_id:
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


# ── Global exception handlers ─────────────────────────────────────────────────

@app.exception_handler(AuthenticationError)
async def auth_error_handler(request: Request, exc: AuthenticationError):
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"error": exc.error_code, "message": exc.message, "details": exc.details},
        headers={"WWW-Authenticate": "Bearer"},
    )


@app.exception_handler(AuthorizationError)
async def authz_error_handler(request: Request, exc: AuthorizationError):
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={"error": exc.error_code, "message": exc.message, "details": exc.details},
    )


@app.exception_handler(RateLimitExceededError)
async def rate_limit_handler(request: Request, exc: RateLimitExceededError):
    retry_after = exc.details.get("retry_after_seconds", 60)
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"error": exc.error_code, "message": exc.message, "details": exc.details},
        headers={"Retry-After": str(retry_after)},
    )


@app.exception_handler(GuardRailViolationError)
async def guardrail_handler(request: Request, exc: GuardRailViolationError):
    return JSONResponse(
        status_code=getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422),
        content={"error": exc.error_code, "message": exc.message, "details": exc.details},
    )


@app.exception_handler(AegisBaseError)
async def aegis_error_handler(request: Request, exc: AegisBaseError):
    return JSONResponse(
        status_code=exc.http_status,
        content={"error": exc.error_code, "message": exc.message, "details": exc.details},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Request / Response Models — Auth
# ─────────────────────────────────────────────────────────────────────────────


class TokenRequest(BaseModel):
    """Request body for POST /api/auth/token."""

    grant_type: str = Field(
        "password",
        pattern=r"^password$",
        description="OAuth2 grant type. Only 'password' is supported.",
    )
    username: str = Field(..., description="User email address")
    password: str = Field(..., description="User password")
    scope: str = Field(
        "openid profile email",
        description="Space-separated OAuth2 scopes",
    )

    model_config = {"json_schema_extra": {"example": {
        "grant_type": "password",
        "username": "TestUser@gmail.com",
        "password": "test@123",
        "scope": "openid profile email agents.call",
    }}}


class TokenGrantResponse(BaseModel):
    """Response body for POST /api/auth/token."""

    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    refresh_token: Optional[str] = None
    scope: str


class UserProfileResponse(BaseModel):
    """Response body for GET /api/auth/me."""

    sub: str
    email: str
    name: str
    tenant_id: str
    roles: List[str]
    permissions: List[str]
    auth_method: str


# ─────────────────────────────────────────────────────────────────────────────
# Request / Response Models — Pipeline
# ─────────────────────────────────────────────────────────────────────────────

class InvokeRequest(BaseModel):
    """Request body for POST /v1/agent/invoke."""

    agent_id: str = Field(..., description="Unique agent identifier")
    provider: str = Field("openai", description="LLM provider: openai | anthropic | google")
    model: str = Field("gpt-4o", description="LLM model name")
    messages: List[Dict[str, str]] = Field(
        ..., description="Chat messages [{role: user|assistant|system, content: ...}]"
    )
    required_permission: str = Field("agents.call", description="IAM permission required")
    resource: Optional[str] = Field(None, description="GCP resource path")
    context_docs: Optional[List[str]] = Field(None, description="Grounding documents")
    correlation_id: Optional[str] = Field(None, description="Caller-supplied correlation ID")

    model_config = {"json_schema_extra": {"example": {
        "agent_id": "my-agent-001",
        "provider": "openai",
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Summarise this contract."}],
        "required_permission": "agents.call",
    }}}


class InvokeResponse(BaseModel):
    """Response body for POST /v1/agent/invoke."""

    response: Optional[str]
    audit_id: str
    latency_ms: float
    guard_results: List[Dict[str, Any]]
    masked_prompt_hash: str
    circuit_breaker_state: str


class VerifyResponse(BaseModel):
    """Response body for GET /v1/auth/token/verify."""

    identity_id: str
    agent_id: Optional[str]
    tenant_id: Optional[str]
    auth_method: str
    roles: List[str]
    expires_at: Optional[str]


# ─────────────────────────────────────────────────────────────────────────────
# API Routes — Authentication  (POST /api/auth/token, GET /api/auth/me)
# ─────────────────────────────────────────────────────────────────────────────


@app.post(
    "/api/auth/token",
    response_model=TokenGrantResponse,
    summary="Authenticate with username + password — returns a JWT",
    tags=["Auth"],
    responses={
        200: {"description": "Token issued successfully"},
        400: {"description": "Invalid grant_type or malformed request"},
        401: {"description": "Incorrect username or password (from external IdP)"},
        502: {"description": "External IdP unreachable or returned an error"},
        503: {"description": "Server not initialised"},
    },
)
async def auth_token(
    body: TokenRequest,
    x_correlation_id: Optional[str] = Header(None, alias="X-Correlation-ID"),
):
    """
    **Password Grant** — forwards credentials to the external IdP, then issues
    an RS256 JWT if the IdP confirms the user is valid.

    Full flow:
    1. Receive ``{username, password}`` from the client.
    2. **Call** ``POST {EXTERNAL_IDP_URL}/authenticate`` with those credentials.
       - Development: ``http://localhost:9000/authenticate`` (mock_idp_server.py)
       - Production:  ``https://mycompany.com/authenticate``
    3. Parse the IdP response — abort with 401 if ``authenticated = false``.
    4. Build an ``IdentityContext`` from the IdP's user record.
    5. Issue an RS256 access token + refresh token via ``JWTHandler``.
    6. Return ``TokenGrantResponse`` to the client.

    Requires: ``Content-Type: application/json``

    Set ``EXTERNAL_IDP_URL`` env var to point at your IdP:
        EXTERNAL_IDP_URL=http://localhost:9000   # mock (dev)
        EXTERNAL_IDP_URL=https://mycompany.com   # production
    """
    if _jwt_handler is None:
        raise HTTPException(status_code=503, detail="Auth service not initialised")

    cid = x_correlation_id or ""
    log = logger.bind(correlation_id=cid, username=body.username)
    idp_url = f"{_EXTERNAL_IDP_URL}/authenticate"

    # ── Step 1: Call the External IdP ────────────────────────────────────────────
    log.info("auth_calling_external_idp", idp_url=idp_url)

    import httpx
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=3.0),
            verify=True,                    # enforce TLS in production
            follow_redirects=False,
            headers={
                "Content-Type": "application/json",
                "X-Correlation-ID": cid,
                "X-Client-ID": "aegis-gateway",
            },
        ) as client:
            idp_resp = await client.post(
                idp_url,
                json={
                    "username": body.username,
                    "password": body.password,
                    "client_id": "aegis-gateway",
                    "correlation_id": cid,
                },
            )
    except httpx.ConnectError:
        log.error("auth_idp_unreachable", idp_url=idp_url)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": "idp_unreachable",
                "error_description": (
                    f"Cannot connect to the identity provider at {_EXTERNAL_IDP_URL}. "
                    "Ensure the IdP service is running. "
                    "Development: run 'uvicorn mocks.mock_idp_server:app --port 9000'."
                ),
            },
        )
    except httpx.TimeoutException:
        log.error("auth_idp_timeout", idp_url=idp_url)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={
                "error": "idp_timeout",
                "error_description": f"Identity provider at {_EXTERNAL_IDP_URL} timed out.",
            },
        )

    # ── Step 2: Handle non-200 from IdP ───────────────────────────────────────
    if idp_resp.status_code != 200:
        log.error(
            "auth_idp_error_status",
            status_code=idp_resp.status_code,
            body=idp_resp.text[:200],
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": "idp_error",
                "error_description": f"Identity provider returned HTTP {idp_resp.status_code}.",
            },
        )

    # ── Step 3: Parse IdP response ──────────────────────────────────────────
    try:
        idp_data = idp_resp.json()
    except Exception:
        log.error("auth_idp_invalid_json", body=idp_resp.text[:200])
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": "idp_invalid_response", "error_description": "IdP returned non-JSON."},
        )

    if not idp_data.get("authenticated", False):
        # IdP explicitly rejected the credentials
        idp_error = idp_data.get("error", "invalid_grant")
        idp_msg   = idp_data.get("error_description", "Incorrect username or password.")
        log.warning("auth_idp_rejected_credentials", idp_error=idp_error)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": idp_error, "error_description": idp_msg},
            headers={"WWW-Authenticate": "Bearer"},
        )

    # ── Step 4: Build IdentityContext from IdP user record ─────────────────────
    idp_user = idp_data.get("user", {})

    from aegis_ai.auth.identity_context import IdentityContext
    from aegis_ai.types import AuthMethod, Permission, TenantID, UserID
    import uuid as _uuid

    identity = IdentityContext(
        identity_id=UserID(idp_user.get("sub", body.username)),
        tenant_id=TenantID(idp_user.get("tenant_id", "default")),
        auth_method=AuthMethod.JWT,
        session_id=str(_uuid.uuid4()),
        email=idp_user.get("email", body.username),
        roles=set(idp_user.get("roles", [])),
        permissions=frozenset(
            Permission(p) for p in idp_user.get("permissions", [])
        ),
    )

    # ── Step 5: Issue RS256 access + refresh tokens ──────────────────────────
    access_token  = _jwt_handler.create_access_token(identity)
    refresh_token = _jwt_handler.create_refresh_token(identity)
    expire_minutes = get_settings().jwt.access_token_expire_minutes

    log.info(
        "auth_token_issued_via_idp",
        sub=str(identity.identity_id),
        tenant_id=str(identity.tenant_id),
        roles=list(identity.roles),
        idp_url=idp_url,
    )

    # ── Step 6: Return to client ───────────────────────────────────────────────
    return TokenGrantResponse(
        access_token=access_token,
        token_type="Bearer",
        expires_in=expire_minutes * 60,
        refresh_token=refresh_token,
        scope=body.scope,
    )


@app.get(
    "/api/auth/me",
    response_model=UserProfileResponse,
    summary="Return the authenticated user's profile",
    tags=["Auth"],
    responses={
        200: {"description": "User profile returned successfully"},
        401: {"description": "Token missing, invalid, or expired"},
        503: {"description": "Server not initialised"},
    },
)
async def auth_me(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_correlation_id: Optional[str] = Header(None, alias="X-Correlation-ID"),
):
    """
    **Profile endpoint** — validate the Bearer token and return the user's claims.

    This is the endpoint `auth_client.py` calls in Step 2 to confirm the issued
    token is accepted by the same server that issued it.

    Validation flow:
    1. Extract Bearer token from ``Authorization`` header.
    2. Call ``JWTHandler.verify_token()`` — full RS256 + revocation check.
    3. Look up the user record from the verified ``sub`` claim.
    4. Return ``UserProfileResponse`` with all role and permission claims.

    Requires: ``Authorization: Bearer <access_token>``
    """
    if _jwt_handler is None:
        raise HTTPException(status_code=503, detail="Auth service not initialised")

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_token", "error_description": "Missing Bearer token."},
            headers={"WWW-Authenticate": "Bearer"},
        )

    raw_token = authorization.removeprefix("Bearer ").strip()

    try:
        identity = _jwt_handler.verify_token(raw_token)
    except Exception as exc:
        logger.warning(
            "auth_me_token_invalid",
            error=str(exc),
            correlation_id=x_correlation_id or "",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_token", "error_description": str(exc)},
            headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
        )

    logger.info(
        "auth_me_success",
        sub=str(identity.identity_id),
        correlation_id=x_correlation_id or "",
    )

    return UserProfileResponse(
        sub=str(identity.identity_id),
        email=identity.email or "",
        name=identity.email or str(identity.identity_id),
        tenant_id=str(identity.tenant_id),
        roles=sorted(identity.roles) if identity.roles else [],
        permissions=sorted(str(p) for p in identity.permissions),
        auth_method=identity.auth_method.value,
    )


# ─────────────────────────────────────────────────────────────────────────────
# API Routes — Pipeline
# ─────────────────────────────────────────────────────────────────────────────

@app.post(
    "/v1/agent/invoke",
    response_model=InvokeResponse,
    summary="Invoke an LLM through the full security pipeline",
    tags=["Pipeline"],
)
async def invoke_agent(
    body: InvokeRequest,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_correlation_id: Optional[str] = Header(None, alias="X-Correlation-ID"),
):
    """
    Full 9-step security pipeline:
    Auth → AuthZ → Rate Limit → GuardRails → PII Masking → LLM Call
    → Response Validation → Retention Enforcement → Audit

    Requires: ``Authorization: Bearer <jwt>``
    """
    if _pipeline is None:
        raise HTTPException(status_code=503, detail="Pipeline not initialised")

    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_token", "error_description": "Missing Authorization header."},
            headers={"WWW-Authenticate": "Bearer"},
        )

    import hashlib

    llm_request = LLMRequest(
        provider=body.provider,
        model=body.model,
        messages=[LLMMessage(**m) for m in body.messages],
    )

    extra_context: Dict[str, Any] = {}
    if x_correlation_id or body.correlation_id:
        extra_context["correlation_id"] = x_correlation_id or body.correlation_id

    result = await _pipeline.secure_agent_call(
        token=authorization,
        agent_id=body.agent_id,
        llm_request=llm_request,
        required_permission=body.required_permission,
        resource=body.resource,
        context_docs=body.context_docs,
        extra_context=extra_context or None,
    )

    return InvokeResponse(
        response=result.response,
        audit_id=result.audit_id,
        latency_ms=round(result.latency_ms, 2),
        guard_results=[
            {"name": g.name, "passed": g.passed, "score": g.score, "action": g.action}
            for g in result.guard_results
        ],
        masked_prompt_hash=hashlib.sha256(result.masked_prompt.encode()).hexdigest()[:16],
        circuit_breaker_state=_pipeline.circuit_breaker_state,
    )


@app.get(
    "/v1/auth/token/verify",
    response_model=VerifyResponse,
    summary="Verify a bearer token and return identity claims",
    tags=["Auth"],
)
async def verify_token(
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """
    Authenticate a token without making an LLM call.
    Useful for API gateways, middleware, and service-to-service verification.
    """
    if _pipeline is None:
        raise HTTPException(status_code=503, detail="Pipeline not initialised")

    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_token", "error_description": "Missing Authorization header."},
            headers={"WWW-Authenticate": "Bearer"},
        )

    identity = await _pipeline.authenticate_only(authorization)
    return VerifyResponse(
        identity_id=identity.identity_id,
        agent_id=identity.agent_id,
        tenant_id=identity.tenant_id,
        auth_method=identity.auth_method.value,
        roles=list(identity.roles) if hasattr(identity, "roles") else [],
        expires_at=identity.expires_at.isoformat() if hasattr(identity, "expires_at") and identity.expires_at else None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Health Probes (Kubernetes-compatible)
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/health/live", tags=["Health"], summary="Liveness probe")
async def liveness():
    """
    Returns 200 if the process is alive.
    Kubernetes: used to decide whether to restart the container.
    """
    return {"status": "alive", "uptime_seconds": round(time.monotonic() - (_startup_time or 0), 1)}


@app.get("/health/ready", tags=["Health"], summary="Readiness probe")
async def readiness():
    """
    Returns 200 if the service can handle requests (all deps healthy).
    Kubernetes: used to decide whether to route traffic to this pod.
    """
    if _pipeline is None or _health_check is None:
        raise HTTPException(status_code=503, detail="Service not initialised")

    system_health = await _health_check.check_all()

    if system_health.overall_status == HealthStatus.UNHEALTHY:
        raise HTTPException(
            status_code=503,
            detail={
                "status": system_health.overall_status.value,
                "components": {
                    k: {"status": v.status.value, "error": v.error}
                    for k, v in system_health.components.items()
                },
            },
        )

    return {
        "status": system_health.overall_status.value,
        "version": system_health.version,
        "timestamp": system_health.timestamp.isoformat(),
        "circuit_breaker": _pipeline.circuit_breaker_state if _pipeline else "UNKNOWN",
        "components": {
            k: {"status": v.status.value, "latency_ms": v.latency_ms}
            for k, v in system_health.components.items()
        },
    }


@app.get("/health/startup", tags=["Health"], summary="Startup probe")
async def startup_probe():
    """
    Returns 200 once startup validation has completed.
    Kubernetes: used to delay liveness/readiness checks until app is ready.
    """
    if _pipeline is None:
        raise HTTPException(status_code=503, detail="Startup not complete")
    return {"status": "started", "uptime_seconds": round(time.monotonic() - (_startup_time or 0), 1)}


# ─────────────────────────────────────────────────────────────────────────────
# Root
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/", include_in_schema=False)
async def root():
    return {
        "service": "Aegis AI Security Gateway",
        "version": "1.2.0",
        "environment": settings.environment.value,
        "docs": "/docs" if settings.is_development() else "disabled",
    }
