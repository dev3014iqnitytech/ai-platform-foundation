"""
Agent A: Client CLI Script — Option 2: HTTP Microservice Pattern with SSO

Demonstrates Agent-to-Agent communication using an SSO Identity Provider (IdP).
Agent A authenticates with the Enterprise IdP using Client Credentials flow to
get an OIDC access token, then passes it to the Aegis Security Gateway.

Key differences from the original demo
---------------------------------------
- A single RSA key is generated (or loaded) once per process run.
  The same key is re-used for all requests, making it verifiable.
- Retry logic with exponential backoff mirrors the Aegis Gateway's own policy.
- Full CLI argument parsing for scripting and CI integration.
- Structured JSON output mode for machine consumption.
- Environment variables loaded from .env via python-dotenv.

Usage
-----
    # Human-readable output (default)
    python agent_a.py

    # Custom prompt
    python agent_a.py --prompt "Summarise OWASP LLM Top 10"

    # JSON output for scripting
    python agent_a.py --prompt "Hello" --json

    # Dry-run (show what would be sent without calling the gateway)
    python agent_a.py --dry-run

    # Point at a different gateway
    python agent_a.py --gateway-url http://my-gateway:8080

Environment variables (override via .env or shell)
---------------------------------------------------
    AEGIS_GATEWAY_URL          Gateway base URL (default: http://localhost:8080)
    CLIENT_SSO_ISSUER          SSO issuer claim (default: https://mock-sso.local)
    CLIENT_SSO_AUDIENCE        SSO audience claim (default: aegis-ai-gateway)
    CLIENT_SSO_SUBJECT         SSO subject (default: agent-a-client-id)
    CLIENT_TOKEN_TTL_MINUTES   Token TTL in minutes (default: 5)
    CLIENT_MOCK_KEY_PATH       Path to PEM private key (auto-generated if unset)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

# Load .env from the same directory as this script (dev convenience)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=False)
except ImportError:
    pass  # python-dotenv is optional; env vars take precedence anyway

import httpx
import jwt
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# ─────────────────────────────────────────────────────────────────────────────
# Configuration (resolved once at module import, before any arg parsing)
# ─────────────────────────────────────────────────────────────────────────────

_GATEWAY_BASE = os.environ.get("AEGIS_GATEWAY_URL", "http://localhost:8080").rstrip("/")
_INVOKE_URL = f"{_GATEWAY_BASE}/v1/agent/invoke"
_SSO_ISSUER = os.environ.get("CLIENT_SSO_ISSUER", "https://mock-sso.local")
_SSO_AUDIENCE = os.environ.get("CLIENT_SSO_AUDIENCE", "aegis-ai-gateway")
_SSO_SUBJECT = os.environ.get("CLIENT_SSO_SUBJECT", "agent-a-client-id")
_SSO_TENANT = os.environ.get("CLIENT_SSO_TENANT_ID", "enterprise-tenant-01")
_TOKEN_TTL = int(os.environ.get("CLIENT_TOKEN_TTL_MINUTES", "5")) * 60
_MAX_RETRIES = 3

# ─────────────────────────────────────────────────────────────────────────────
# Stable Key Management
# ─────────────────────────────────────────────────────────────────────────────

def _load_or_generate_key() -> rsa.RSAPrivateKey:
    """
    Load a private key from CLIENT_MOCK_KEY_PATH, or generate one for this run.

    IMPORTANT: The same key is used for every call in this process.
    This is what makes the mock token verifiable by the Gateway.
    """
    key_path_env = os.environ.get("CLIENT_MOCK_KEY_PATH", "")
    if key_path_env:
        key_file = Path(key_path_env)
        if key_file.exists():
            private_key = serialization.load_pem_private_key(
                key_file.read_bytes(),
                password=None,
                backend=default_backend(),
            )
            print(f"🔑 [Agent A] Loaded signing key from {key_file}")
            return private_key
        print(f"⚠️  [Agent A] Key file not found at {key_path_env}. Generating a new key.")

    print("🔑 [Agent A] Generating RSA-2048 signing key for this session...")
    return rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )


# Module-level stable key — generated ONCE for the entire process run
_PRIVATE_KEY = _load_or_generate_key()
_CACHED_TOKEN: Optional[str] = None
_TOKEN_EXPIRY: float = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Token Acquisition
# ─────────────────────────────────────────────────────────────────────────────

def _get_or_mint_token() -> str:
    """
    Return a cached token or mint a fresh one if within 60 s of expiry.

    In production replace the body with a real Client Credentials HTTP call to
    your IdP:
        resp = httpx.post(IDP_TOKEN_URL, data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        })
        return resp.json()["access_token"]
    """
    global _CACHED_TOKEN, _TOKEN_EXPIRY

    now = time.time()
    if _CACHED_TOKEN and (_TOKEN_EXPIRY - 60) > now:
        return _CACHED_TOKEN

    print(f"🔐 [Agent A] Minting SSO token (iss={_SSO_ISSUER})...")
    exp = int(now) + _TOKEN_TTL
    payload = {
        "sub": _SSO_SUBJECT,
        "email": f"{_SSO_SUBJECT}@enterprise.com",
        "tenant_id": _SSO_TENANT,
        "roles": ["AGENT_CALLER"],
        "permissions": ["agents.call"],
        "iat": int(now),
        "nbf": int(now) - 5,
        "exp": exp,
        "iss": _SSO_ISSUER,
        "aud": _SSO_AUDIENCE,
        "jti": str(uuid.uuid4()),
    }
    token = jwt.encode(
        payload,
        _PRIVATE_KEY,
        algorithm="RS256",
        headers={"kid": "mock-sso-kid", "typ": "JWT"},
    )
    _CACHED_TOKEN = token
    _TOKEN_EXPIRY = float(exp)
    print("✅ [Agent A] SSO token minted successfully.")
    return token


# ─────────────────────────────────────────────────────────────────────────────
# Gateway Call with Retry
# ─────────────────────────────────────────────────────────────────────────────

async def call_aegis_gateway(
    prompt: str,
    agent_id: str = "agent-b",
    provider: str = "openai",
    model: str = "gpt-4o",
    gateway_url: str = _INVOKE_URL,
    json_output: bool = False,
) -> Dict[str, Any]:
    """
    Send a prompt to the Aegis Gateway and return the parsed response.

    Retries up to _MAX_RETRIES times on transient failures (5xx / network errors)
    with exponential backoff, matching the Gateway's own retry policy.

    Args:
        prompt:      User prompt string.
        agent_id:    Target agent ID.
        provider:    LLM provider (openai | anthropic | google).
        model:       Model name.
        gateway_url: Full invoke URL.
        json_output: Suppress human-readable log lines when True.

    Returns:
        Parsed JSON response from the Gateway.

    Raises:
        SystemExit on unrecoverable errors.
    """
    correlation_id = str(uuid.uuid4())
    token = _get_or_mint_token()

    request_body = {
        "agent_id": agent_id,
        "provider": provider,
        "model": model,
        "required_permission": "agents.call",
        "correlation_id": correlation_id,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Correlation-ID": correlation_id,
    }

    if not json_output:
        print(f"\n🌐 [Agent A] → Aegis Gateway: {gateway_url}")
        print(f"   Correlation ID : {correlation_id}")
        print(f"   Agent          : {agent_id}  |  {provider}/{model}")
        print(f"   Prompt         : {prompt[:80]}{'...' if len(prompt) > 80 else ''}")

    last_error: Optional[Exception] = None

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = await client.post(gateway_url, json=request_body, headers=headers)

                if resp.status_code == 200:
                    result = resp.json()
                    if not json_output:
                        print("\n✅ [Agent A] Request successful!")
                    return result

                if resp.status_code == 401:
                    _print_error(
                        json_output,
                        "AUTH_FAILED",
                        "Gateway rejected the Agent A M2M token (401).",
                        {"hint": "Ensure the Gateway SSO provider trusts 'https://mock-sso.local'."},
                    )
                    sys.exit(1)

                if resp.status_code == 403:
                    _print_error(
                        json_output,
                        "AUTHZ_FAILED",
                        "Agent A lacks the 'agents.call' permission (403).",
                        {"agent_id": agent_id},
                    )
                    sys.exit(1)

                if resp.status_code == 422:
                    _print_error(
                        json_output,
                        "GUARDRAIL_VIOLATION",
                        "Prompt blocked by Aegis GuardRails (422).",
                        _safe_json(resp),
                    )
                    sys.exit(1)

                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "5"))
                    if not json_output:
                        print(f"⚠️  [Agent A] Rate limited. Retrying in {retry_after}s...")
                    await asyncio.sleep(retry_after)
                    continue

                # 5xx — retry with backoff
                last_error = Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")

            except httpx.TimeoutException as exc:
                last_error = exc
                if not json_output:
                    print(f"⏱️  [Agent A] Timeout on attempt {attempt}.")

            except httpx.ConnectError:
                _print_error(
                    json_output,
                    "GATEWAY_UNREACHABLE",
                    f"Cannot connect to {gateway_url}.",
                    {"hint": "Run 'docker-compose up -d' to start the Aegis Gateway."},
                )
                sys.exit(1)

            if attempt < _MAX_RETRIES:
                delay = 2 ** attempt  # 2s, 4s
                if not json_output:
                    print(f"⟳  [Agent A] Retrying in {delay}s (attempt {attempt}/{_MAX_RETRIES})...")
                await asyncio.sleep(delay)

    _print_error(
        json_output,
        "MAX_RETRIES_EXCEEDED",
        f"Gateway call failed after {_MAX_RETRIES} attempts.",
        {"last_error": str(last_error)},
    )
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# Output helpers
# ─────────────────────────────────────────────────────────────────────────────

def _print_error(json_output: bool, code: str, message: str, details: Any = None) -> None:
    if json_output:
        print(json.dumps({"error": code, "message": message, "details": details or {}}))
    else:
        print(f"\n❌ [Agent A] {message}")
        if details:
            print(f"   Details: {json.dumps(details, indent=2)}")


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return {"raw": response.text}


# ─────────────────────────────────────────────────────────────────────────────
# CLI Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Agent A CLI — calls the Aegis AI Security Gateway via SSO.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Environment variables")[0].strip(),
    )
    parser.add_argument(
        "--prompt",
        default="Analyze this data for security risks. My email is admin@company.com.",
        help="User prompt to send (default: built-in security analysis prompt)",
    )
    parser.add_argument(
        "--agent-id",
        default="agent-b",
        metavar="AGENT_ID",
        help="Target agent ID on the Gateway (default: agent-b)",
    )
    parser.add_argument(
        "--provider",
        default="openai",
        choices=["openai", "anthropic", "google"],
        help="LLM provider (default: openai)",
    )
    parser.add_argument(
        "--model",
        default="gpt-4o",
        help="LLM model name (default: gpt-4o)",
    )
    parser.add_argument(
        "--gateway-url",
        default=_INVOKE_URL,
        metavar="URL",
        help=f"Full Gateway invoke URL (default: {_INVOKE_URL})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be sent without making a network call.",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Output machine-readable JSON (suppresses all log lines).",
    )
    return parser


async def _main_async(args: argparse.Namespace) -> None:
    if args.dry_run:
        token = _get_or_mint_token()
        payload = {
            "agent_id": args.agent_id,
            "provider": args.provider,
            "model": args.model,
            "required_permission": "agents.call",
            "messages": [{"role": "user", "content": args.prompt}],
        }
        output = {
            "dry_run": True,
            "gateway_url": args.gateway_url,
            "request_body": payload,
            "token_preview": f"{token[:40]}...",
        }
        if args.json_output:
            print(json.dumps(output, indent=2))
        else:
            print("\n🧪 [Agent A] DRY RUN — would send:")
            print(json.dumps(output, indent=2))
        return

    result = await call_aegis_gateway(
        prompt=args.prompt,
        agent_id=args.agent_id,
        provider=args.provider,
        model=args.model,
        gateway_url=args.gateway_url,
        json_output=args.json_output,
    )

    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        print("\n📋 [Agent A] Gateway Response:")
        print(json.dumps(result, indent=2))


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
