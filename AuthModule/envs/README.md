# Environment Configuration Guide

This directory contains **environment-specific configuration templates**.
They are committed to source control because they contain **no secrets** —
only references to where secrets live (GCP Secret Manager secret names, Redis URLs, etc.).

## Files

| File | Purpose | GCP Required | Secret Backend |
|---|---|---|---|
| `.env.development` | Local dev workstation | ❌ No | `env` (env-vars / files) |
| `.env.staging` | Pre-production validation | ✅ Yes (staging project) | `gcp` |
| `.env.production` | Live production | ✅ Yes (prod project) | `gcp` |

## Usage

### Local Development

```bash
# Copy the dev template to the repo root as .env
cp envs/.env.development .env

# Generate local RSA key pair (required for JWT in dev)
mkdir -p keys
openssl genrsa -out keys/dev_private_key.pem 4096
openssl rsa -in keys/dev_private_key.pem -pubout -out keys/dev_public_key.pem

# Run the application — settings load from .env automatically
AEGIS_ENV=development python -m aegis_ai.cli serve
```

### CI / Staging

```bash
# In CI pipeline (GitHub Actions, Cloud Build, etc.)
AEGIS_ENV=staging python -m aegis_ai.cli serve
# The envs/.env.staging file is loaded automatically.
# Real secrets are injected by GCP Workload Identity.
```

### Production (Kubernetes / Cloud Run)

```bash
# Set in pod spec / Cloud Run service config
AEGIS_ENV=production
# Secrets come from GCP Secret Manager via Workload Identity.
# No secret values should be in any environment file.
```

## How Settings Are Loaded

Priority order (highest → lowest):

```
Process env vars  (AEGIS__*)           ← always wins
.env              (gitignored override) ← developer local
envs/.env.{AEGIS_ENV}                  ← this directory
Pydantic defaults                       ← fallback
```

## Production Safety Rules

The `AegisSettings` validator enforces these rules and will **refuse to start**
if violated in production:

| Rule | Development | Staging | Production |
|---|---|---|---|
| `AEGIS__GCP__USE_GCP` | `false` OK | `true` required | `true` required |
| Local PEM key paths | Allowed | ❌ Rejected | ❌ Rejected |
| TLS minimum version | TLSv1.2 OK | TLSv1.2 OK | TLSv1.3 required |
| Rate limit backend | `memory` OK | `redis` required | `redis` required |
| `fail_open` | Allowed | Allowed | ❌ Rejected |
| Secret backend | `env` OK | `gcp` required | `gcp` required |

## Adding a New Environment Variable

1. Add the variable to **all three** env files with appropriate values.
2. Add the corresponding field to the relevant `*Settings` model in `aegis_ai/settings.py`.
3. Update `.env.example` with a documented placeholder.
4. If it's a secret reference (not the secret value itself), add it to
   `_check_required_secrets()` in `aegis_ai/startup.py`.

> ⚠️ **Never put actual secret values** (API keys, PEM keys, passwords) in any
> file in this directory. All secret values must live in GCP Secret Manager
> and be referenced only by name.
