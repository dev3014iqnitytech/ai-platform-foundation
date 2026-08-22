# Changelog

All notable changes to Aegis AI SDK are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [1.2.0] — 2026-08-14 — Design Patterns & Multi-Environment Config

### Added — Design Patterns

- **`aegis_ai/factory.py`** `[NEW]` — Factory pattern. `PipelineFactory.create("development"|"staging"|"production")` wires environment-appropriate concrete components without callers knowing concrete types. Includes `AuthProviderFactory`, `AuditLoggerFactory`, `SecretRepositoryFactory`.
- **`aegis_ai/builder.py`** `[NEW]` — Builder pattern. `PipelineBuilder` fluent API: `.with_auth("jwt").with_guardrails([...]).with_rate_limiter("redis").build()`. Validates all components before construction.
- **`aegis_ai/decorators.py`** `[NEW]` — Decorator utilities: `@retry_on_transient` (exponential backoff), `@require_permission` (RBAC enforcement), `@audit_action` (structured entry/exit logging), `CircuitBreaker` (CLOSED/OPEN/HALF_OPEN state machine).
- **`aegis_ai/secrets/`** `[NEW]` — Strategy pattern: `SecretRepository` ABC, `GCPSecretRepository` (TTL cache, per-secret asyncio locks, backoff retries), `EnvSecretRepository` (env-var/file, dev-only).
- **`aegis_ai/events/`** `[NEW]` — Observer pattern: `SecurityEventBus` (fire-and-forget fan-out, `@bus.on()` decorator), `SecurityEvent` immutable value object with 28 typed `EventCategory` entries.
- **`aegis_ai/audit/composite_audit_logger.py`** `[NEW]` — Composite pattern: concurrent fan-out to multiple audit sinks with per-sink error isolation.
- **`aegis_ai/audit/splunk_audit_logger.py`** `[NEW]` — Splunk HEC sink with async batching and lazy token resolution.
- **`aegis_ai/startup.py`** `[NEW]` — Fail-fast startup validator: `validate_production_config()` runs concurrent checks (GCP, secrets, Redis, TLS, JWT keys).

### Added — Pipeline & Settings

- `secure_session()` async context manager for multi-turn sessions.
- `drain_event_bus()` / `circuit_breaker_state` on `SecurityPipeline`.
- Module-level `CircuitBreaker` wrapping the LLM gateway (5-failure threshold, 30 s recovery).
- Correlation ID (`correlation_id`) threaded through all pipeline steps and log entries.
- `SecurityEventBus` publishing `auth_success`, `auth_failure`, `pipeline_blocked` events.
- `Environment` enum with `is_production()` / `is_staging()` / `is_development()` helpers.
- `@model_validator` enforcing production-safety rules (GCP required, TLS 1.3, Redis backend, etc.).
- `AegisSettings.for_environment(env)` classmethod for test isolation.

### Added — Multi-Environment Config

- `envs/.env.development` — GCP off, in-memory, local PEM keys, permissive thresholds.
- `envs/.env.staging` — GCP staging project, Redis, near-production thresholds.
- `envs/.env.production` — Full GCP, Redis cluster (TLS), MFA enforced, TLS 1.3 only.
- `envs/README.md` — Environment configuration guide.

---

## [1.1.0] — 2026-08-13 — Production-Ready Refinement


### Fixed — Critical

- **`audit_event.py`**: `hmac.new()` does not exist on Python's `hmac` module.
  Fixed to use the correct `hmac.HMAC(key, msg, digestmod).hexdigest()` API.
  **Previously caused `AttributeError` at runtime on every audit event.**

- **`encryption.py`**: Removed `FakeKeyManager` and `HybridEncryptionResult` test
  helpers from the production module. These belonged in `tests/conftest.py`.
  `FakeKeyManager` is now importable from `tests.conftest`.

- **`encryption.py`**: Fixed deprecated Pydantic v2 `EncryptedPayload.parse_raw()`
  → `EncryptedPayload.model_validate_json()`.
  **Previously caused `AttributeError` on `decrypt_field()` calls.**

- **`encryption.py`**: Replaced `asyncio.get_event_loop()` (deprecated in Python 3.10,
  raises `DeprecationWarning` in 3.12) with proper sync/async API split.
  `Encryptor` now exposes `encrypt_async()` / `decrypt_async()` for production
  and `encrypt_sync()` / `decrypt_sync()` for testing.

- **`audit_logger.py`**: Replaced insecure `b"\x00" * 32` null-byte default HMAC
  signing key with a random `secrets.token_bytes(32)` ephemeral key + loud warning.
  Null-byte keys provide essentially **zero cryptographic strength**.

- **`pipeline.py`**: Removed `hasattr(x, "_mock_return_value")` blocks throughout
  the audit section. pytest's internal `_mock_return_value` attribute has no place
  in production code and caused silent data corruption when mocks were present.

- **`pipeline.py`**: Removed duplicate guard-rail audit loop. Every guardrail event
  was being logged twice — once in `_run_guard_rails` via `log_guard_rail()` and
  again in the main pipeline body. **Halved audit event volume.**

- **`settings.py`**: Removed `ToxicitySettingsShim` and `PIISettingsShim` classes
  and their `.toxicity` / `.pii` properties from `AegisSettings`. These were test
  workarounds that polluted the production settings model.

- **`llm_gateway.py`**: Moved `import random` from inside the exponential-backoff
  retry loop to module-level imports. Redundant module lookups on each retry.

### Fixed — Important

- **`rate_limiter.py`**: Separated concerns — removed `raise RateLimitExceededError`
  from `check_rate_limit()`. Rate limiters should return results; callers decide
  exception flow. The pipeline's `_enforce_rate_limit` already raises correctly.

- **`pipeline.py`**: `PipelineConfig.__init__` now accepts `settings` as an optional
  positional argument (was keyword-only), matching the documented
  `PipelineConfig(settings)` usage shown in README.md.

- **`pipeline.py`**: Audit hashes now computed directly from `prompt_text` and
  `final_response` strings via `_hash()` — cleaner than extracting from the
  `RetentionEnforcement` object with mock-conditional branching.

### Added

- **`tests/conftest.py`**: Shared test fixtures including `FakeKeyManager`,
  `mock_identity`, `admin_identity`, `sample_request`, `pii_request`,
  `injection_request`, `test_settings`. Eliminates fixture duplication across
  test files.

- **`tests/unit/conftest.py`**: Unit-test-specific fixtures with session-scoped
  RSA key pair generation (`rsa_private_pem`, `rsa_public_pem`) and a
  preconfigured `jwt_handler` fixture.

- **`.github/workflows/ci.yml`**: GitHub Actions CI pipeline with three jobs:
  lint + type check + bandit security scan; unit test matrix (Python 3.11 + 3.12)
  with 85% coverage gate; dependency vulnerability scan via `safety`.

---

## [1.0.0] — 2026-08-01 — Initial Release

- Full OWASP LLM Top 10 coverage (LLM01–LLM10)
- JWT/SSO/API-Key authentication with RS256
- Three-layer authorization: Google IAM + RBAC + ABAC Policy Engine
- Sliding-window rate limiter (Redis primary, in-memory fallback)
- GuardRail chain: PromptDefender, InjectionDetector, ToxicityDetector,
  PIIDetector, DynamicGrounder
- Microsoft Presidio PII masking
- Zero-retention LLM gateway (OpenAI, Anthropic, Google Gemini)
- HMAC-SHA256 signed audit trail with GCP Cloud Logging / SIEM export
- AES-256-GCM encryption with HKDF key derivation
- OpenTelemetry metrics and tracing
- Health check endpoints for Kubernetes readiness/liveness probes
- Penetration test suite covering prompt injection and auth bypass scenarios
