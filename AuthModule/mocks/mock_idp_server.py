"""
mocks.mock_idp_server
======================
Mock External Identity Provider (IdP) — simulates https://mycompany.com

This service stands in for the enterprise corporate IdP at mycompany.com.
It exposes a single endpoint:

    POST /authenticate

The Aegis Security Gateway calls this endpoint to validate user credentials
before issuing its own RS256 JWT.

Mock Users (valid credentials):
    Username : TestUser@gmail.com
    Password : test@123

Run this BEFORE starting the Aegis Gateway:
    uvicorn mocks.mock_idp_server:app --port 9000

Or directly:
    python -m mocks.mock_idp_server

Then set in your .env:
    EXTERNAL_IDP_URL=http://localhost:9000
"""

from __future__ import annotations

import hmac
import time
import uuid
from typing import Any, Dict, Optional

import structlog
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ]
)
logger = structlog.get_logger("mock-idp")

# ─────────────────────────────────────────────────────────────────────────────
# Mock User Registry
# simulates the corporate user directory at https://mycompany.com
#
# In a real IdP this would be an LDAP / AD lookup or a DB query.
# Passwords here are plaintext ONLY because this is a mock.
# ─────────────────────────────────────────────────────────────────────────────

_MOCK_USERS: Dict[str, Dict[str, Any]] = {
    # lookup key is lowercase email
    "testuser@gmail.com": {
        "sub": "user-testuser-001",
        "email": "TestUser@gmail.com",
        "name": "Test User",
        "password": "test@123",             # plaintext — mock only
        "tenant_id": "enterprise-tenant-01",
        "department": "Engineering",
        "roles": ["USER", "AGENT_CALLER"],
        "permissions": ["agents.call", "profile.read"],
        "account_status": "active",
        "mfa_enabled": False,
    },
    # Add more test users here as needed
    "admin@mycompany.com": {
        "sub": "user-admin-001",
        "email": "admin@mycompany.com",
        "name": "Company Admin",
        "password": "Admin@2024",
        "tenant_id": "enterprise-tenant-01",
        "department": "IT",
        "roles": ["ADMIN", "USER", "AGENT_CALLER"],
        "permissions": ["agents.call", "agents.manage", "profile.read", "admin.all"],
        "account_status": "active",
        "mfa_enabled": True,
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Request / Response Models
# ─────────────────────────────────────────────────────────────────────────────

class AuthenticateRequest(BaseModel):
    """Request body sent by the Aegis Gateway to validate credentials."""

    username: str = Field(..., description="User email / username")
    password: str = Field(..., description="User password")
    client_id: Optional[str] = Field(
        None,
        description="Optional: the requesting service / application ID",
    )
    correlation_id: Optional[str] = Field(
        None,
        description="Optional: end-to-end trace ID from the originating request",
    )

    model_config = {"json_schema_extra": {"example": {
        "username": "TestUser@gmail.com",
        "password": "test@123",
        "client_id": "aegis-gateway",
        "correlation_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    }}}


class AuthenticatedUser(BaseModel):
    """User record returned on successful authentication."""

    sub: str
    email: str
    name: str
    tenant_id: str
    department: str
    roles: list[str]
    permissions: list[str]
    account_status: str
    mfa_enabled: bool


class AuthenticateResponse(BaseModel):
    """Response from POST /authenticate."""

    authenticated: bool
    user: Optional[AuthenticatedUser] = None
    error: Optional[str] = None
    error_description: Optional[str] = None
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI Application
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Mock External IdP — mycompany.com",
    description=(
        "Simulates the corporate Identity Provider at `https://mycompany.com`.\n\n"
        "**This is a development/test mock only.** It validates credentials against\n"
        "a hardcoded user registry and returns a structured authentication response.\n\n"
        "The Aegis Security Gateway calls `POST /authenticate` before issuing its own JWT."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log every request with timing."""
    start = time.monotonic()
    response = await call_next(request)
    latency_ms = round((time.monotonic() - start) * 1000, 2)
    logger.info(
        "idp_request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        latency_ms=latency_ms,
        correlation_id=request.headers.get("X-Correlation-ID", ""),
    )
    return response


@app.get("/", include_in_schema=False)
async def root():
    return {
        "service": "Mock External IdP (mycompany.com)",
        "version": "1.0.0",
        "endpoint": "POST /authenticate",
        "mock_users": list(_MOCK_USERS.keys()),
    }


@app.post(
    "/authenticate",
    response_model=AuthenticateResponse,
    summary="Validate user credentials",
    tags=["Identity"],
    responses={
        200: {"description": "Authentication result (check 'authenticated' field)"},
        422: {"description": "Request validation error"},
    },
)
async def authenticate(
    body: AuthenticateRequest,
    request: Request,
):
    """
    Validate a username and password against the corporate user directory.

    This endpoint is called by the **Aegis Security Gateway** as part of its
    `POST /api/auth/token` password-grant flow.

    ### Success Response (authenticated = true)
    ```json
    {
      "authenticated": true,
      "user": {
        "sub": "user-testuser-001",
        "email": "TestUser@gmail.com",
        "name": "Test User",
        "tenant_id": "enterprise-tenant-01",
        "roles": ["USER", "AGENT_CALLER"],
        "permissions": ["agents.call", "profile.read"],
        ...
      }
    }
    ```

    ### Failure Response (authenticated = false)
    ```json
    {
      "authenticated": false,
      "error": "invalid_credentials",
      "error_description": "Incorrect username or password."
    }
    ```

    ### Valid Test Credentials
    | Username | Password |
    |----------|----------|
    | `TestUser@gmail.com` | `test@123` |
    | `admin@mycompany.com` | `Admin@2024` |
    """
    cid = body.correlation_id or request.headers.get("X-Correlation-ID", "")
    log = logger.bind(correlation_id=cid, client_id=body.client_id or "unknown")

    # ── Step 1: User lookup (case-insensitive) ────────────────────────────────
    lookup_key = body.username.lower().strip()
    user = _MOCK_USERS.get(lookup_key)

    if user is None:
        log.warning("idp_user_not_found", username=body.username)
        return AuthenticateResponse(
            authenticated=False,
            error="invalid_credentials",
            error_description="Incorrect username or password.",
        )

    # ── Step 2: Account status check ──────────────────────────────────────────
    if user.get("account_status") != "active":
        log.warning("idp_account_inactive", sub=user["sub"])
        return AuthenticateResponse(
            authenticated=False,
            error="account_disabled",
            error_description=f"Account is {user.get('account_status', 'inactive')}.",
        )

    # ── Step 3: Password verification (constant-time) ─────────────────────────
    # hmac.compare_digest prevents timing oracle attacks even on plaintext.
    # Production: use argon2-cffi verify() or passlib[bcrypt] check() here.
    password_valid = hmac.compare_digest(
        user["password"].encode("utf-8"),
        body.password.encode("utf-8"),
    )

    if not password_valid:
        log.warning("idp_invalid_password", sub=user["sub"])
        return AuthenticateResponse(
            authenticated=False,
            error="invalid_credentials",
            error_description="Incorrect username or password.",
        )

    # ── Step 4: Return authenticated user ─────────────────────────────────────
    log.info(
        "idp_authentication_success",
        sub=user["sub"],
        email=user["email"],
        tenant_id=user["tenant_id"],
        roles=user["roles"],
    )
    return AuthenticateResponse(
        authenticated=True,
        user=AuthenticatedUser(
            sub=user["sub"],
            email=user["email"],
            name=user["name"],
            tenant_id=user["tenant_id"],
            department=user["department"],
            roles=user["roles"],
            permissions=user["permissions"],
            account_status=user["account_status"],
            mfa_enabled=user["mfa_enabled"],
        ),
    )


@app.get("/health", tags=["Health"])
async def health():
    """Liveness probe."""
    return {"status": "ok", "service": "mock-idp"}


# ─────────────────────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "mock_idp_server:app",
        host="0.0.0.0",
        port=9000,
        reload=True,
        log_level="info",
    )
