"""
tests/unit/test_crypto.py
===========================
Unit tests for the cryptography layer.

Covers: TokenSigner, TLSEnforcer, Encryption (AES-256-GCM)
"""

from __future__ import annotations

import json
import os
import ssl
import tempfile
from unittest.mock import MagicMock

import pytest

from aegis_ai.crypto.token_signer import TokenSigner
from aegis_ai.crypto.tls_enforcer import TLSEnforcer
from aegis_ai.settings import AegisSettings


# ─────────────────────────────────────────────────────────────────────────────
# Token Signer Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestTokenSigner:
    @pytest.fixture
    def signer(self):
        return TokenSigner(signing_key=os.urandom(32))

    def test_sign_and_verify_bytes(self, signer):
        payload = b"critical-data"
        sig = signer.sign(payload)
        assert signer.verify(payload, sig) is True

    def test_verify_fails_on_tampered_payload(self, signer):
        payload = b"critical-data"
        sig = signer.sign(payload)
        assert signer.verify(b"tampered-data", sig) is False

    def test_verify_fails_on_tampered_signature(self, signer):
        payload = b"critical-data"
        sig = signer.sign(payload)
        # Corrupt the signature
        tampered_sig = sig[:-2] + ("ZZ" if not sig.endswith("ZZ") else "AA")
        assert signer.verify(payload, tampered_sig) is False

    def test_sign_dict(self, signer):
        data = {"event": "auth", "user": "alice", "timestamp": 12345}
        sig = signer.sign_dict(data)
        assert signer.verify_dict(data, sig) is True

    def test_dict_signing_is_key_order_independent(self, signer):
        data_a = {"b": 2, "a": 1}
        data_b = {"a": 1, "b": 2}
        sig_a = signer.sign_dict(data_a)
        sig_b = signer.sign_dict(data_b)
        assert sig_a == sig_b  # Sort keys ensures same result

    def test_different_signers_produce_different_sigs(self):
        key_a = os.urandom(32)
        key_b = os.urandom(32)
        signer_a = TokenSigner(key_a)
        signer_b = TokenSigner(key_b)
        payload = b"test-payload"
        sig_a = signer_a.sign(payload)
        assert signer_b.verify(payload, sig_a) is False

    def test_key_too_short_raises(self):
        with pytest.raises(ValueError):
            TokenSigner(signing_key=b"tooshort")

    def test_compute_hmac_static(self):
        key = os.urandom(32)
        data = b"test"
        h1 = TokenSigner.compute_hmac(key, data)
        h2 = TokenSigner.compute_hmac(key, data)
        assert h1 == h2

    def test_compare_hmac_timing_safe(self):
        key = os.urandom(32)
        data = b"test"
        h = TokenSigner.compute_hmac(key, data)
        assert TokenSigner.compare_hmac(key, data, h) is True
        assert TokenSigner.compare_hmac(key, data, "wrong") is False


# ─────────────────────────────────────────────────────────────────────────────
# TLS Enforcer Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestTLSEnforcer:
    @pytest.fixture
    def enforcer(self):
        settings = MagicMock(spec=AegisSettings)
        return TLSEnforcer(settings)

    def test_ssl_context_minimum_version(self, enforcer):
        ctx = enforcer.create_ssl_context()
        assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2

    def test_ssl_context_cert_required(self, enforcer):
        ctx = enforcer.create_ssl_context()
        assert ctx.verify_mode == ssl.CERT_REQUIRED

    def test_ssl_context_hostname_check(self, enforcer):
        ctx = enforcer.create_ssl_context()
        assert ctx.check_hostname is True

    def test_https_url_passes(self, enforcer):
        # Should not raise
        TLSEnforcer.validate_url_scheme("https://api.openai.com/v1/chat/completions")

    def test_http_url_raises(self, enforcer):
        with pytest.raises(ValueError, match="HTTPS"):
            TLSEnforcer.validate_url_scheme("http://api.openai.com/v1/chat/completions")

    def test_hsts_header_present(self, enforcer):
        headers = TLSEnforcer.get_hsts_header()
        assert "Strict-Transport-Security" in headers
        assert "max-age" in headers["Strict-Transport-Security"]
        assert "includeSubDomains" in headers["Strict-Transport-Security"]

    def test_httpx_client_created(self, enforcer):
        import httpx
        client = enforcer.create_httpx_client(timeout=10.0)
        assert client is not None
        assert isinstance(client, httpx.AsyncClient)


# ─────────────────────────────────────────────────────────────────────────────
# Encryption Tests (AES-256-GCM)
# ─────────────────────────────────────────────────────────────────────────────

class TestEncryptor:
    @pytest.fixture
    def encryptor(self):
        try:
            from aegis_ai.crypto.encryption import Encryptor
            return Encryptor(key=os.urandom(32))
        except ImportError:
            pytest.skip("encryption module not available")

    def test_encrypt_decrypt_roundtrip(self, encryptor):
        plaintext = b"super-secret-data"
        payload = encryptor.encrypt_sync(plaintext)
        recovered = encryptor.decrypt_sync(payload)
        assert recovered == plaintext

    def test_tampered_ciphertext_raises(self, encryptor):
        plaintext = b"important-data"
        payload = encryptor.encrypt_sync(plaintext)
        # Corrupt the ciphertext by flipping all bits
        import base64
        raw = base64.urlsafe_b64decode(payload.ciphertext_b64)
        tampered_b64 = base64.urlsafe_b64encode(bytes([b ^ 0xFF for b in raw])).decode()
        tampered_payload = payload.model_copy(update={"ciphertext_b64": tampered_b64})
        with pytest.raises(Exception):
            encryptor.decrypt_sync(tampered_payload)

    def test_different_keys_cannot_decrypt(self, encryptor):
        from aegis_ai.crypto.encryption import Encryptor
        plaintext = b"classified"
        payload = encryptor.encrypt_sync(plaintext)
        other_encryptor = Encryptor(key=os.urandom(32))
        with pytest.raises(Exception):
            other_encryptor.decrypt_sync(payload)
