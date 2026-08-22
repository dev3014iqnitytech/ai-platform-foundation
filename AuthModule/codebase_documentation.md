# Aegis AI SDK — Codebase Documentation & API Map

This document lists and describes every module, class, and core function inside the `aegis_ai/` SDK, mapping out their responsibilities and functionalities.

---

## 📁 1. Core & Orchestration

### 📄 [pipeline.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/pipeline.py)
*The central security orchestrator for the Aegis AI SDK.*
- **`PipelineConfig`**: Configuration container holding all active handlers and settings.
- **`SecurityPipeline`**: Main class coordinating E2E safety checks:
  - `secure_agent_call(token, request, required_permissions, context)`: The primary method. Performs the following lifecycle steps:
    1. Authenticates token via JWT or SSO.
    2. Validates authorization via RBAC, ABAC, and GCP IAM.
    3. Runs rate limiting.
    4. Evaluates prompt against the Guardrails chain (Prompt injection, toxicity, PII, etc.).
    5. Masks sensitive data (PII).
    6. Calls LLM via gateway with zero-retention headers.
    7. Evaluates response safety (PII and toxicity leak checks).
    8. Records audit trails (HMAC signed) and streams metrics.

### 📄 [types.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/types.py)
*Definitions of enums, data models, and custom types.*
- **`AuthMethod`** (Enum): Values: `JWT`, `SSO`, `API_KEY`.
- **`GuardRailAction`** (Enum): Values: `PASS`, `BLOCK`, `REMEDIATE`.
- **`GuardRailResult`** (Pydantic model): Repesents guardrail outcomes.
- **`PipelineResult`** (Pydantic model): Represents E2E pipeline results.

### 📄 [settings.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/settings.py)
*SDK configuration and environments management.*
- **`AegisSettings`**: Extends Pydantic `BaseSettings` to load environment variables from `.env` covering JWT settings, Redis connections, and Perspective API keys.

---

## 📁 2. Identity & Authentication (`aegis_ai/auth/`)

### 📄 [base.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/auth/base.py)
- **`AuthProvider`**: Abstract base class defining `validate_token(token)` and `verify_token(token)`.

### 📄 [identity_context.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/auth/identity_context.py)
- **`IdentityContext`**: Immutable Pydantic model representing authenticated users/agents containing:
  - `identity_id`, `tenant_id`, `permissions`, `session_id`, `mfa_verified`.
  - `has_permission(permission)`: Checks if permission is present.

### 📄 [jwt_handler.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/auth/jwt_handler.py)
- **`JWTHandler`**: Implements RS256 JWT issuance and validation.
  - `create_token(payload, expires_delta)`: Signs standard RS256 JWTs with a key header (`kid`).
  - `verify_token(token)`: Validates JWT signature, expiration, issuer/audience, and checks Redis JTI blocklist.
  - `set_keys(private_pem, public_pem, kid)`: Injects RSA keys.

### 📄 [sso_provider.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/auth/sso_provider.py)
- **`SSOProvider`**: Handles single sign-on using OIDC and remote JWKS endpoint validation.
  - `verify_token(token)`: Fetches OIDC configuration, retrieves active public keys, and validates signatures.

### 📄 [api_key_manager.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/auth/api_key_manager.py)
- **`APIKeyManager`**: Generates and validates long-lived agent API keys.
  - `generate_key(tenant_id, prefix)`: Returns a cryptographically random prefixed key.
  - `validate_key(api_key)`: Performs constant-time Argon2id hash checks against key databases.

### 📄 [mfa_verifier.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/auth/mfa_verifier.py)
- **`MFAVerifier`**: Validates Time-Based One-Time Passwords (TOTP).
  - `verify_code(secret, code)`: Validates a 6-digit TOTP token using a timing-safe comparative buffer.

---

## 📁 3. Access Control & Authorization (`aegis_ai/authz/`)

### 📄 [rbac_engine.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/authz/rbac_engine.py)
- **`RBACEngine`**: Handles hierarchical role permissions matching.
  - `has_permission(roles, required_permission)`: Recursively traverses role structures (e.g., `ADMIN -> OPERATOR`) to evaluate active permissions.

### 📄 [policy_engine.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/authz/policy_engine.py)
- **`PolicyEngine`**: Implements Attribute-Based Access Control (ABAC).
  - `evaluate(identity, action, resource, context)`: Evaluates dynamic context (IP subnets, time boundaries, MFA verification status) against safety rules.

### 📄 [least_privilege.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/authz/least_privilege.py)
- **`LeastPrivilegeEnforcer`**: Limits API scopes dynamically.
  - `enforce_least_privilege(identity, required_permissions)`: Intersects user scopes with active permissions, raising errors if permissions are violated.

### 📄 [iam_client.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/authz/iam_client.py)
- **`IAMClient`**: Integrates with Google Cloud IAM.
  - `has_project_permission(project_id, permission)`: Validates active GCP credentials for projects using the `google-auth` library.

---

## 📁 4. Input & Output Guardrails (`aegis_ai/guardrails/`)

### 📄 [base.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/guardrails/base.py)
- **`GuardRail`**: Abstract base class for all guardrails.
- **`GuardRailChain`**: Sequential guardrail execution manager.
  - `run(prompt, context)`: Evaluates a prompt against all active guardrails.

### 📄 [injection_detector.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/guardrails/injection_detector.py)
- **`InjectionDetector`**: Detects prompt injection (OWASP LLM01).
  - `check(prompt, context)`: Normalizes characters, decodes base64/ROT13 layers, measures text entropy, and applies heuristic regex pattern matching.

### 📄 [toxicity_detector.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/guardrails/toxicity_detector.py)
- **`ToxicityDetector`**: Identifies hate speech and abuse (OWASP LLM06).
  - `check(prompt, context)`: Queries Google Perspective API. Falls back to local `detoxify` models via circuit-breaker logic if the API fails.

### 📄 [pii_detector.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/guardrails/pii_detector.py)
- **`PIIDetector`**: Scans prompt text for PII (OWASP LLM06).
  - `analyze(text)`: Utilizes Microsoft Presidio Analyzer to find phone numbers, emails, credit cards, and addresses.

### 📄 [data_masker.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/guardrails/data_masker.py)
- **`DataMasker`**: Redacts sensitive data.
  - `mask(text, entities, operator)`: Masks PII using `replace` (placeholders), `redact` (removes values), or `hash` (SHA-256).
  - `unmask(masked_text, masking_map)`: Reverses masking using standard mapping keys.

### 📄 [dynamic_grounder.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/guardrails/dynamic_grounder.py)
- **`DynamicGrounder`**: Prevents hallucinations (OWASP LLM09).
  - `check_grounding(response, context_chunks)`: Checks similarity between LLM responses and source context documents to verify facts.

### 📄 [prompt_defender.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/guardrails/prompt_defender.py)
- **`PromptDefender`**: Implements structural defences.
  - `check(prompt, context)`: Detects attempts to read or bypass system instructions.
  - `add_system_prompt_delimiter(prompt)`: Wraps user prompts in delimiter tags.

### 📄 [rate_limiter.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/guardrails/rate_limiter.py)
- **`RateLimiter`**: Prevents denial of service (OWASP LLM04).
  - `check_rate_limit(key)`: Implements sliding window rate limiting using Redis, falling back to a thread-safe local queue.

---

## 📁 5. Cryptography (`aegis_ai/crypto/`)

### 📄 [key_manager.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/crypto/key_manager.py)
- **`KeyManager`**: Handles key storage and GCP Secret Manager integrations.
  - `wrap_key(plaintext_key)` / `unwrap_key(encrypted_key)`: Wraps/unwraps keys using GCP KMS envelopes.

### 📄 [tls_enforcer.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/crypto/tls_enforcer.py)
- **`TLSEnforcer`**: Secures outgoing HTTP/HTTPS traffic.
  - `create_ssl_context()`: Builds strict SSL context enforcing TLS 1.3, disabling old suites, and enforcing hostname validations.

### 📄 [token_signer.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/crypto/token_signer.py)
- **`TokenSigner`**: Signs arbitrary payloads.
  - `sign_dict(payload)` / `verify_dict(payload, signature)`: Creates HMAC-SHA256 signatures with sorted keys to avoid ordering conflicts.

### 📄 [encryption.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/crypto/encryption.py)
- **`AESGCMEncryptor`**: Fast authenticated data encryption.
  - `encrypt(data)` / `decrypt(ciphertext)`: Uses AES-256-GCM to encrypt/decrypt payloads with random initialization vectors (IVs).

---

## 📁 6. Gateway & Compliance (`aegis_ai/proxy/`)

### 📄 [llm_gateway.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/proxy/llm_gateway.py)
- **`LLMGateway`**: Proxy for LLM queries.
  - `call(request)`: Makes outbound HTTP requests to OpenAI or Anthropic using `TLSEnforcer`.

### 📄 [zero_retention_policy.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/proxy/zero_retention_policy.py)
- **`ZeroRetentionPolicy`**: Enforces strict LLM API contracts (OWASP LLM05).
  - `validate_provider(provider)`: Verifies providers are compliant with data privacy rules.
  - `get_zero_retention_headers()`: Appends compliance headers (`X-Zero-Retention: true`).

### 📄 [response_validator.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/proxy/response_validator.py)
- **`ResponseValidator`**: Scans generated outputs before return.
  - `validate(response, request)`: Evaluates model responses for sensitive data leakages or high toxicity scores.

---

## 📁 7. Logging, Audits & Metrics (`aegis_ai/audit/`, `aegis_ai/observability/`)

### 📄 [audit_event.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/audit/audit_event.py)
- **`AuditEvent`**: Data structure representing log events.
  - `compute_hmac(key)`: Signs log event records using HMAC-SHA256 to guarantee log immutability.

### 📄 [audit_logger.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/audit/audit_logger.py)
- **`AuditLogger`**: Manages logging.
  - `log(event)`: Computes signatures, appends events to an in-memory queue, and flushes them to exporters.
  - `log_llm_call(...)` / `log_guard_rail(...)`: Convenience methods.

### 📄 [siem_exporter.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/audit/siem_exporter.py)
- **`SIEMExporter`**: Exports logs to external systems.
  - `export(events)`: Streams events to GCP Cloud Logging or external SIEM systems, falling back to local files if the export fails.

### 📄 [retention_policy.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/audit/retention_policy.py)
- **`RetentionPolicy`**: Prevents sensitive raw data logging.
  - `enforce(provider, prompt, response)`: Generates cryptographic hashes of the prompt and response, discarding the raw texts.

### 📄 [health_check.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/observability/health_check.py)
- **`HealthChecker`**: Liveness and readiness monitoring.
  - `check_readiness()`: Validates connection states to external databases, Redis, and GCP KMS systems.

### 📄 [metrics_collector.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/observability/metrics_collector.py)
- **`MetricsCollector`**: Records system performance.
  - `record_pipeline(latency_ms, outcome)` / `record_guard_rail(name, passed, latency_ms)`: Exports counters and histograms to OpenTelemetry/Prometheus.

### 📄 [tracer.py](file:///c:/Ramendra/AI-Learning/AuthenitcationModule/AuthenticationSDKAI/aegis_ai/observability/tracer.py)
- **`AegisTracer`**: Manages telemetry traces.
  - `start_span(name)`: Wraps actions in OpenTelemetry spans to trace requests E2E.
