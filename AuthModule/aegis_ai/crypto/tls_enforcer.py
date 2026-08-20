"""
aegis_ai.crypto.tls_enforcer
================================
TLS 1.3 enforcement for all outbound HTTP connections.

Security guarantees:
- Minimum TLS 1.2, preferred TLS 1.3
- Permitted cipher suites only (GCM + ChaCha20-Poly1305)
- Certificate verification always enabled (no skip)
- Returns pre-configured httpx.AsyncClient for SDK-wide use
- HSTS header injection for inbound responses

OWASP: A02:2021-Cryptographic Failures, LLM10-Model Theft
"""

from __future__ import annotations

import ssl
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, Optional

import httpx
import structlog

from aegis_ai.settings import AegisSettings

logger = structlog.get_logger(__name__)

# TLS 1.3-preferred cipher suites (RFC 8446)
_TLS13_CIPHERS = [
    "TLS_AES_256_GCM_SHA384",
    "TLS_CHACHA20_POLY1305_SHA256",
    "TLS_AES_128_GCM_SHA256",
]

# TLS 1.2 fallback cipher suites (FIPS-approved)
_TLS12_CIPHERS = [
    "ECDHE-ECDSA-AES256-GCM-SHA384",
    "ECDHE-RSA-AES256-GCM-SHA384",
    "ECDHE-ECDSA-CHACHA20-POLY1305",
    "ECDHE-RSA-CHACHA20-POLY1305",
    "ECDHE-ECDSA-AES128-GCM-SHA256",
    "ECDHE-RSA-AES128-GCM-SHA256",
]


class TLSEnforcer:
    """
    Enforces strict TLS policies on all outbound connections.

    Creates pre-configured httpx.AsyncClient instances with:
    - Minimum TLS 1.2 (TLS 1.3 preferred)
    - Verified certificate chain
    - Whitelisted cipher suites only
    - Sensible timeouts
    """

    def __init__(self, settings: Optional[AegisSettings] = None) -> None:
        self._settings = settings

    def create_ssl_context(self) -> ssl.SSLContext:
        """
        Create a hardened SSL context.

        Returns:
            ssl.SSLContext configured for TLS 1.2+ with strong ciphers.
        """
        ctx = ssl.create_default_context()

        # Enforce minimum TLS 1.2
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2

        # Prefer TLS 1.3 (no explicit maximum — allow latest)
        # ctx.maximum_version = ssl.TLSVersion.MAXIMUM_SUPPORTED

        # Enable certificate verification (always)
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.check_hostname = True

        # Load system CA bundle (certifi override if available)
        try:
            import certifi
            ctx.load_verify_locations(certifi.where())
            logger.debug("tls_ca_bundle_loaded", source="certifi")
        except ImportError:
            ctx.load_default_certs()
            logger.debug("tls_ca_bundle_loaded", source="system")

        # Set cipher list (TLS 1.3 suites + TLS 1.2 ECDHE suites)
        try:
            all_ciphers = ":".join(_TLS13_CIPHERS + _TLS12_CIPHERS)
            ctx.set_ciphers(all_ciphers)
        except ssl.SSLError:
            # Fallback: set only TLS 1.2 ciphers (some platforms don't support TLS 1.3 names)
            try:
                ctx.set_ciphers(":".join(_TLS12_CIPHERS))
            except ssl.SSLError:
                logger.warning("tls_cipher_set_failed", note="Using platform defaults")

        # Belt-and-suspenders: block SSLv2/v3 explicitly (minimum_version already does this).
        # OP_NO_TLSv1/TLSv1_1 intentionally omitted — deprecated in Python 3.12+ and
        # already blocked by minimum_version = TLSv1_2 above.
        for opt_name in ("OP_NO_SSLv2", "OP_NO_SSLv3"):
            opt = getattr(ssl, opt_name, None)
            if opt is not None:
                ctx.options |= opt
        ctx.options |= ssl.OP_NO_COMPRESSION  # Prevent CRIME attack

        logger.debug("tls_ssl_context_created", min_version="TLSv1.2")
        return ctx

    def create_httpx_client(
        self,
        timeout: float = 30.0,
        headers: Optional[Dict[str, str]] = None,
        **kwargs: Any,
    ) -> httpx.AsyncClient:
        """
        Create a pre-configured httpx.AsyncClient with TLS enforcement.

        Args:
            timeout: Request timeout in seconds.
            headers: Default headers to include on every request.
            **kwargs: Additional kwargs passed to httpx.AsyncClient.

        Returns:
            Configured httpx.AsyncClient (caller must close/use as context manager).
        """
        ssl_ctx = self.create_ssl_context()
        default_headers = {
            "User-Agent": "aegis-ai-sdk/1.0.0",
            "X-Aegis-SDK-Version": "1.0.0",
        }
        if headers:
            default_headers.update(headers)

        try:
            client = httpx.AsyncClient(
                verify=ssl_ctx,
                timeout=httpx.Timeout(timeout, connect=10.0),
                headers=default_headers,
                http2=True,  # Prefer HTTP/2
                follow_redirects=False,  # Disable redirect following (security)
                **kwargs,
            )
        except ImportError:
            client = httpx.AsyncClient(
                verify=ssl_ctx,
                timeout=httpx.Timeout(timeout, connect=10.0),
                headers=default_headers,
                http2=False,
                follow_redirects=False,  # Disable redirect following (security)
                **kwargs,
            )
        logger.debug("tls_httpx_client_created", timeout=timeout)
        return client

    @asynccontextmanager
    async def get_client(
        self,
        timeout: float = 30.0,
        headers: Optional[Dict[str, str]] = None,
    ) -> AsyncIterator[httpx.AsyncClient]:
        """
        Async context manager that yields a TLS-enforced httpx client.

        Usage::

            async with tls_enforcer.get_client() as client:
                response = await client.get("https://api.example.com/data")
        """
        client = self.create_httpx_client(timeout=timeout, headers=headers)
        try:
            yield client
        finally:
            await client.aclose()

    @staticmethod
    def validate_url_scheme(url: str) -> None:
        """
        Reject non-HTTPS URLs (prevents downgrade attacks).

        Args:
            url: The URL to validate.

        Raises:
            ValueError: If the URL does not use https://.
        """
        if not url.startswith("https://"):
            raise ValueError(
                f"TLS enforcement requires HTTPS. Rejected URL: {url[:50]}"
            )

    @staticmethod
    def get_hsts_header(max_age: int = 31536000) -> Dict[str, str]:
        """
        Return HTTP Strict Transport Security header for inbound responses.

        Args:
            max_age: HSTS max-age in seconds (default 1 year).

        Returns:
            Dict with Strict-Transport-Security header.
        """
        return {
            "Strict-Transport-Security": (
                f"max-age={max_age}; includeSubDomains; preload"
            )
        }
