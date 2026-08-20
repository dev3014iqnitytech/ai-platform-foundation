# Aegis AI — Enterprise Security & Governance SDK

A production-ready **Python SDK** providing comprehensive security, governance, and audit trails for AI Agents.

---

## Key Features & OWASP LLM Top 10 Coverage

Aegis AI enforces the **EVLAS (Evaluate, Validate, Log, Audit, Safety)** security lifecycle:

| OWASP ID | Risk Description | Mitigating Component | File Link |
| :--- | :--- | :--- | :--- |
| **LLM01** | Prompt Injection | Multi-layer Injection Detector & Prompt Defender | [injection_detector.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/guardrails/injection_detector.py) |
| **LLM02** | Insecure Output Handling | Exfiltration Scanner & Response Validator | [response_validator.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/proxy/response_validator.py) |
| **LLM03** | Training Data Poisoning | Semantic RAG Grounding Check | [dynamic_grounder.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/guardrails/dynamic_grounder.py) |
| **LLM04** | Model Denial of Service | Sliding-Window Rate Limiter (Redis / Memory) | [rate_limiter.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/guardrails/rate_limiter.py) |
| **LLM05** | Supply Chain Vulnerabilities | Zero Data Retention Contractual Enforcer | [zero_retention_policy.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/proxy/zero_retention_policy.py) |
| **LLM06** | Sensitive Info Disclosure | Presidio Analyzer PII Detector & Data Masker | [pii_detector.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/guardrails/pii_detector.py) |
| **LLM07** | Insecure Plugin Design | Attribute-Based Access Control (ABAC) Engine | [policy_engine.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/authz/policy_engine.py) |
| **LLM08** | Excessive Agency | Least-Privilege Scope Reduction Enforcer | [least_privilege.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/authz/least_privilege.py) |
| **LLM09** | Overreliance | Semantic TF Cosine Grounding Validator | [dynamic_grounder.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/guardrails/dynamic_grounder.py) |
| **LLM10** | Model Theft | Strict TLS 1.3 Cipher Enforcer & AES-256 Envelope | [tls_enforcer.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/crypto/tls_enforcer.py) |

---

## Architecture Flow

```
Agent / Client
    │
    ▼
┌────────────────────────────────────────────────────┐
│               TLS 1.3 Transport Layer              │  ← crypto/tls_enforcer.py
└────────────────────────────────────────────────────┘
    │
    ▼
┌────────────────────────────────────────────────────┐
│            SecurityPipeline (Facade)               │  ← pipeline.py
│  EVLAS: Evaluate → Validate → Log → Audit → Safety │
└──┬──────────┬──────────┬───────────┬──────────────┘
   │          │          │           │
   ▼          ▼          ▼           ▼
[Auth]    [AuthZ]   [GuardRails]  [LLM Proxy]
SSO/JWT   IAM+RBAC  Injection     Zero-Retention
API Key   Policy    Toxicity      TLS-enforced
  MFA     ABAC      PII/Masking   Retry+Circuit
          LeastPriv Dynamic Ground   Breaker
   │          │          │           │
   └──────────┴──────────┴───────────┘
                    │
                    ▼
          ┌─────────────────┐
          │  Audit Trail    │  ← HMAC-signed, immutable
          │  SIEM Export    │  ← Cloud Logging / Splunk
          │  Metrics/Trace  │  ← OpenTelemetry
          └─────────────────┘
```

---

## Configuration Reference

Aegis AI loads environment variables with the `AEGIS__` prefix (using double underscores for nested objects):

```ini
# Environment
AEGIS__ENVIRONMENT=production

# GCP settings
AEGIS__GCP__PROJECT_ID=my-gcp-project
AEGIS__GCP__USE_GCP=true
AEGIS__GCP__KMS_KEY_RING=aegis-ai-keyring
AEGIS__GCP__KMS_LOCATION=global
AEGIS__GCP__KMS_CRYPTO_KEY=aegis-ai-key

# JWT authentication
AEGIS__JWT__ISSUER=https://auth.aegis-ai.internal
AEGIS__JWT__AUDIENCE=aegis-ai-agents
AEGIS__JWT__ALGORITHM=RS256
AEGIS__JWT__PRIVATE_KEY_SECRET_NAME=aegis-ai-jwt-private-key
AEGIS__JWT__PUBLIC_KEY_SECRET_NAME=aegis-ai-jwt-public-key

# Rate limiting
AEGIS__RATE_LIMIT__ENABLED=true
AEGIS__RATE_LIMIT__REDIS_URL=redis://localhost:6379/0
AEGIS__RATE_LIMIT__REQUESTS_PER_MINUTE=60

# Audit trail
AEGIS__AUDIT__ENABLED=true
AEGIS__AUDIT__USE_GCP_LOGGING=true
AEGIS__AUDIT__LOG_NAME=aegis-ai-audit
AEGIS__AUDIT__SIGNING_KEY_SECRET_NAME=aegis-ai-audit-signing-key

# Guardrails
AEGIS__GUARDRAILS__INJECTION_THRESHOLD=0.4
AEGIS__GUARDRAILS__TOXICITY_THRESHOLD=0.7
AEGIS__GUARDRAILS__ENABLE_DYNAMIC_GROUNDING=true
AEGIS__GUARDRAILS__GROUNDING_MIN_SIMILARITY=0.3
```

---

## Quick Start Example

Here is how to integrate `SecurityPipeline` into your agent codebase:

```python
import asyncio
from aegis_ai import (
    AegisSettings,
    PipelineConfig,
    SecurityPipeline,
    LLMRequest,
    LLMMessage,
)

async def main():
    # Load configuration
    settings = AegisSettings()

    # Build Pipeline config container
    # (injecting any mock clients or custom providers if needed)
    config = PipelineConfig(settings)

    # Initialize the Security Pipeline
    pipeline = SecurityPipeline(config)

    # Build an LLM request
    request = LLMRequest(
        provider="openai",
        model="gpt-4o",
        messages=[
            LLMMessage(role="user", content="Here is my email: alice@example.com")
        ]
    )

    try:
        # Securely execute the request
        result = await pipeline.secure_agent_call(
            token="your-jwt-access-token-here",
            request=request,
            required_permissions=["agents.call"],
            context={"allowed_cidrs": ["10.0.0.0/8"]}
        )

        print(f"Is Allowed: {result.allowed}")
        print(f"Masked Prompt: {result.masked_prompt}")
        print(f"Response: {result.response}")
        print(f"Audit ID: {result.audit_id}")

    except Exception as exc:
        print(f"Request blocked by Aegis security: {exc}")

if __name__ == "__main__":
    asyncio.run(main())
```

---

## Docker Deployment

### Prerequisites

```bash
# Generate local RSA dev keys (first time only)
chmod +x scripts/gen_keys.sh && ./scripts/gen_keys.sh

# Copy the env template
cp .env.example .env
# Edit .env — set OPENAI_API_KEY etc.
```

### Local Development Stack

```bash
# Start full stack (API + Redis + Jaeger tracing UI + Redis Commander)
docker compose up

# Services:
#   API          → http://localhost:8080
#   Swagger UI   → http://localhost:8080/docs
#   Jaeger       → http://localhost:16686
#   Redis UI     → http://localhost:8081

# Check health
curl http://localhost:8080/health/ready | jq .

# Follow logs
docker compose logs -f aegis-ai
```

### Staging

```bash
docker compose -f docker-compose.yml -f docker-compose.staging.yml up -d
```

### Production (Kubernetes / GKE)

```bash
# Build and push
IMAGE_REPO=us-central1-docker.pkg.dev/YOUR_PROJECT/aegis-ai/aegis-ai \
PUSH=true \
./scripts/docker-build.sh 1.2.0

# Deploy to GKE
kubectl apply -k k8s/
kubectl rollout status deployment/aegis-ai -n aegis-system

# Check readiness
kubectl get pods -n aegis-system
curl https://api.aegis-ai.example.com/health/ready | jq .
```

### API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/agent/invoke` | Full pipeline: Auth → GuardRails → LLM |
| `GET` | `/v1/auth/token/verify` | Verify JWT, returns identity claims |
| `GET` | `/health/live` | Liveness probe |
| `GET` | `/health/ready` | Readiness probe (deep check) |
| `GET` | `/health/startup` | Startup probe |
| `GET` | `/docs` | Swagger UI (development only) |

### Invoke Example

```bash
curl -X POST http://localhost:8080/v1/agent/invoke \
  -H "Authorization: Bearer <your-jwt>" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "my-agent",
    "provider": "openai",
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Summarise this contract."}],
    "required_permission": "agents.call"
  }' | jq .
```

---

## Design Patterns (v1.2.0)

| Pattern | File | Usage |
|---|---|---|
| **Factory** | [`factory.py`](aegis_ai/factory.py) | `PipelineFactory.create("production")` |
| **Builder** | [`builder.py`](aegis_ai/builder.py) | Fluent pipeline construction |
| **Strategy** | [`secrets/`](aegis_ai/secrets/) | GCP / Env-var secret backends |
| **Observer** | [`events/`](aegis_ai/events/) | `SecurityEventBus` audit fan-out |
| **Composite** | [`audit/composite_audit_logger.py`](aegis_ai/audit/composite_audit_logger.py) | GCP + Splunk concurrent |
| **Decorator** | [`decorators.py`](aegis_ai/decorators.py) | `@retry_on_transient`, `@require_permission`, `CircuitBreaker` |

---

## Multi-Environment Configuration

Set `AEGIS_ENV` before running — configuration loads automatically:

| `AEGIS_ENV` | GCP | Rate Limiter | Secret Backend | TLS |
|---|---|---|---|---|
| `development` | Off | In-memory | Env-var / file | TLS 1.2 |
| `staging` | On | Redis | GCP Secret Manager | TLS 1.2 |
| `production` | On | Redis cluster | GCP Secret Manager | TLS 1.3 only |

See [`envs/README.md`](envs/README.md) for the full configuration guide.

---

## Running Verification Suites

All test suites verify compliance with OWASP guidelines:

```bash
# Install dependencies
pip install -e ".[dev,server,pentest]"

# Run unit tests
pytest tests/unit/ -v

# Run compliance penetration tests
pytest tests/penetration/ -v

# Static analysis
ruff check aegis_ai/
mypy aegis_ai/ --strict
bandit -r aegis_ai/
```

---

## Quick Start (SDK Mode)

```python
import asyncio
from aegis_ai import PipelineFactory, LLMRequest, LLMMessage

async def main():
    # Reads AEGIS_ENV from environment — no manual wiring
    pipeline = PipelineFactory.create()

    result = await pipeline.secure_agent_call(
        token="Bearer <your-jwt>",
        agent_id="my-agent",
        llm_request=LLMRequest(
            provider="openai",
            model="gpt-4o",
            messages=[LLMMessage(role="user", content="Summarise the contract.")]
        ),
        required_permission="agents.call",
    )

    print(f"Response : {result.response}")
    print(f"Audit ID : {result.audit_id}")
    print(f"Latency  : {result.latency_ms:.1f} ms")

asyncio.run(main())
```

Or use the **fluent Builder** for custom configurations:

```python
from aegis_ai import PipelineBuilder

pipeline = (
    PipelineBuilder()
    .with_environment("development")
    .with_auth("jwt")
    .with_guardrails(["prompt_defender", "injection", "toxicity", "pii"])
    .with_rate_limiter("memory")
    .with_audit_logger("stdout")
    .build()
)
```

---

## File Structure

```
AuthenticationSDKAI/
├── Dockerfile                    ← Multi-stage production image
├── .dockerignore
├── docker-compose.yml            ← Local dev stack
├── docker-compose.staging.yml    ← Staging override
├── docker-compose.prod.yml       ← Production override
├── gunicorn.conf.py              ← Gunicorn configuration
├── .env.example                  ← Full env variable reference
├── envs/
│   ├── .env.development
│   ├── .env.staging
│   ├── .env.production
│   └── README.md
├── k8s/
│   ├── deployment.yaml           ← Deployment + HPA + PDB + SA
│   ├── configmap.yaml
│   ├── ingress.yaml              ← GKE Managed Certificate
│   └── kustomization.yaml
├── scripts/
│   ├── gen_keys.sh               ← Generate dev RSA key pair
│   └── docker-build.sh           ← Multi-arch build + scan
├── .github/workflows/
│   └── deploy.yml                ← CI/CD: test → build → staging → prod
└── aegis_ai/
    ├── server.py                 ← FastAPI ASGI application
    ├── factory.py                ← Factory pattern
    ├── builder.py                ← Builder pattern
    ├── decorators.py             ← Retry, RBAC, CircuitBreaker
    ├── startup.py                ← Fail-fast startup validator
    ├── events/                   ← Observer (SecurityEventBus)
    └── secrets/                  ← Strategy (GCP / Env backends)
```
