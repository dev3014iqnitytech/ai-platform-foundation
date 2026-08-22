# Client App (Agent A)

> Enterprise Agent A — demonstrates secure **Agent-to-Agent (A2A)** communication
> via the **Aegis AI Security Gateway** using SSO / OIDC M2M tokens.

---

## Architecture

```
  ┌──────────────────────────────────────────────────────────────┐
  │  End User / Browser                                          │
  │   POST /api/secure/analyze   Bearer <user-jwt>              │
  └────────────────────────────┬─────────────────────────────────┘
                               │ ① Verify user token (local JWT)
                               ▼
  ┌──────────────────────────────────────────────────────────────┐
  │  Client API  — Agent A  (this service, port 8001)            │
  │                                                              │
  │   MockSSOTokenManager                                        │
  │   ② Fetch cached M2M token (stable RSA key, auto-refresh)   │
  └────────────────────────────┬─────────────────────────────────┘
                               │ ③ POST /v1/agent/invoke  Bearer <m2m-token>
                               ▼
  ┌──────────────────────────────────────────────────────────────┐
  │  Aegis Security Gateway  (port 8080)                         │
  │                                                              │
  │   Auth → AuthZ → Rate Limit → GuardRails → PII Mask →      │
  │   LLM Call → Response Validation → Audit                    │
  └────────────────────────────┬─────────────────────────────────┘
                               │ ④ Call LLM Provider (OpenAI / Anthropic / Google)
                               ▼
                         LLM Response
```

---

## Files

| File | Purpose |
|------|----------|
| `main.py` | FastAPI microservice (Agent A) |
| `agent_a.py` | Standalone CLI script (SSO M2M flow) |
| `auth_client.py` | **Production auth client** — Username/Password → Token Exchange with loop-engineering retry |
| `config.py` | Pydantic `BaseSettings` — env var reference |
| `token_manager.py` | Stable SSO token cache (one key per process) |
| `requirements.txt` | Python dependencies |
| `Dockerfile` | Multi-stage container (non-root, health-checked) |

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AEGIS_GATEWAY_URL` | `http://localhost:8080` | Aegis Gateway base URL |
| `CLIENT_HOST` | `0.0.0.0` | FastAPI bind host |
| `CLIENT_PORT` | `8001` | FastAPI bind port |
| `CLIENT_ENVIRONMENT` | `development` | `development \| staging \| production` |
| `CLIENT_LOG_LEVEL` | `info` | `debug \| info \| warning \| error` |
| `CLIENT_SSO_ISSUER` | `https://mock-sso.local` | JWT `iss` claim for M2M token |
| `CLIENT_SSO_AUDIENCE` | `aegis-ai-gateway` | JWT `aud` claim for M2M token |
| `CLIENT_SSO_SUBJECT` | `agent-a-client-id` | JWT `sub` claim for M2M token |
| `CLIENT_SSO_TENANT_ID` | `enterprise-tenant-01` | Tenant ID in M2M token |
| `CLIENT_TOKEN_TTL_MINUTES` | `5` | M2M token validity window (minutes) |
| `CLIENT_TOKEN_REFRESH_BUFFER` | `60` | Seconds before expiry to auto-refresh |
| `CLIENT_MOCK_KEY_PATH` | *(generated)* | Path to PEM private key for mock SSO signing |
| `CLIENT_USER_JWT_SECRET` | *(empty)* | HS256 secret for verifying end-user tokens |
| `CLIENT_USER_JWT_ALGORITHM` | `HS256` | Algorithm for user token verification |
| `CLIENT_USER_JWT_ISSUER` | *(skip check)* | Expected issuer in user tokens |
| `CLIENT_USER_JWT_AUDIENCE` | *(skip check)* | Expected audience in user tokens |

> Copy `.env.example` from the project root and adjust values.

---

## Quick Start

### 1 — Start the full stack

```bash
# From the project root
docker-compose up -d
```

### 2 — Run the FastAPI service locally

```bash
cd client_app
pip install -r requirements.txt

# Development (auto-reload, Swagger UI at http://localhost:8001/docs)
CLIENT_USER_JWT_SECRET=dev-secret uvicorn main:app --port 8001 --reload
```

### 3 — Hit the public endpoint

```bash
curl http://localhost:8001/api/public/status
```

Expected response:
```json
{
  "status": "ok",
  "service": "Client API (Agent A)",
  "version": "1.0.0",
  "environment": "development",
  "uptime_seconds": 3.2,
  "gateway_url": "http://localhost:8080",
  "auth_required": false
}
```

### 4 — Generate a test user token

```bash
python - <<'EOF'
import time, jwt
token = jwt.encode(
    {"sub": "user-001", "email": "user@example.com", "iat": int(time.time()), "exp": int(time.time()) + 3600},
    "dev-secret",
    algorithm="HS256",
)
print(token)
EOF
```

### 5 — Hit the secure analyze endpoint

```bash
USER_TOKEN="<token from step 4>"

curl -X POST http://localhost:8001/api/secure/analyze \
  -H "Authorization: Bearer $USER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Summarise the OWASP LLM Top 10 in 3 bullet points."}'
```

### 6 — Check your token claims

```bash
curl http://localhost:8001/api/secure/token-info \
  -H "Authorization: Bearer $USER_TOKEN"
```

---

## CLI Script

```bash
# Default prompt
python agent_a.py

# Custom prompt
python agent_a.py --prompt "Explain zero-trust networking"

# Machine-readable JSON output
python agent_a.py --prompt "Hello" --json

# Dry-run — print request without calling the gateway
python agent_a.py --dry-run --json

# Custom gateway URL
python agent_a.py --gateway-url http://my-gateway:8080

# Full options
python agent_a.py --help
```

---

## Running in Docker

```bash
# Build
docker build -t aegis-client-app:latest -f client_app/Dockerfile client_app/

# Run (connected to the compose gateway network)
docker run --rm \
  --network aegis-network \
  -e AEGIS_GATEWAY_URL=http://aegis-gateway:8080 \
  -e CLIENT_USER_JWT_SECRET=dev-secret \
  -p 8001:8001 \
  aegis-client-app:latest
```

---

## Health Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /health/live` | Kubernetes liveness probe (200 = process alive) |
| `GET /health/ready` | Kubernetes readiness probe (200 = token manager ready) |
| `GET /api/public/status` | Human-readable status page |

---

## Production Checklist

- [ ] Set `CLIENT_ENVIRONMENT=production`
- [ ] Set `CLIENT_USER_JWT_SECRET` to a strong random secret (or switch to RS256 with a real public key)
- [ ] Replace `MockSSOTokenManager._fetch_token_from_idp()` with a real Client Credentials grant to your IdP
- [ ] Set `CLIENT_MOCK_KEY_PATH` to a stable PEM file (or remove the mock entirely)
- [ ] Set `AEGIS_GATEWAY_URL` to the production gateway address
- [ ] Enable TLS (`AEGIS__TLS__MINIMUM_VERSION=TLSv1.3`) on the gateway
- [ ] Run with `uvicorn --workers <N>` or behind Gunicorn with multiple workers

---

## A2A Authentication — How It Works

```
Agent A (this service)                    Aegis Gateway
        │                                       │
        │  1. Generate / load RSA key once      │
        │     (MockSSOTokenManager.__init__)     │
        │                                       │
        │  2. Mint signed JWT (RS256)            │
        │     iss = https://mock-sso.local       │
        │     aud = aegis-ai-gateway             │
        │     sub = agent-a-client-id            │
        │                                       │
        │  3. POST /v1/agent/invoke              │
        │     Authorization: Bearer <m2m-token> ─────────────────►│
        │                                       │
        │                                       │  4. SSOProvider.validate_id_token()
        │                                       │     - Fetch JWKS from mock-sso.local
        │                                       │     - Verify RS256 signature
        │                                       │     - Check iss / aud / exp / nbf
        │                                       │
        │                                       │  5. IAM + RBAC + Policy check
        │                                       │
        │                                       │  6. Full GuardRails pipeline
        │                                       │
        │                                       │  7. LLM call (OpenAI / Anthropic)
        │                                       │
        │  ◄────────────────────────────────────── 8. Return PipelineResult
        │
        │  9. Return AnalyzeResponse to end user
```

> **Key insight**: The `MockSSOTokenManager` generates a **single RSA key per process**
> and caches signed tokens until they're within 60 s of expiry. This is what makes
> the mock token cryptographically verifiable by the Gateway — unlike the original demo
> which generated a new key on every request.
