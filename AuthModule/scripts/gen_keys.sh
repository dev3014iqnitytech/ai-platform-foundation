#!/usr/bin/env bash
# =============================================================================
# scripts/gen_keys.sh — Generate local RSA key pair for development
# =============================================================================
# Usage:
#   chmod +x scripts/gen_keys.sh
#   ./scripts/gen_keys.sh
#
# Creates:
#   keys/dev_private_key.pem   (RSA-4096 private key — GITIGNORED)
#   keys/dev_public_key.pem    (RSA-4096 public key  — GITIGNORED)
#
# These keys are used by AEGIS__JWT__LOCAL_PRIVATE_KEY_PATH in development.
# Never commit these files. The keys/ directory is in .gitignore.
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
KEYS_DIR="$REPO_ROOT/keys"

mkdir -p "$KEYS_DIR"
chmod 700 "$KEYS_DIR"

echo "🔑 Generating RSA-4096 key pair for local development..."

# Private key
openssl genrsa -out "$KEYS_DIR/dev_private_key.pem" 4096
chmod 600 "$KEYS_DIR/dev_private_key.pem"

# Public key (extracted from private)
openssl rsa -in "$KEYS_DIR/dev_private_key.pem" \
            -pubout -out "$KEYS_DIR/dev_public_key.pem"
chmod 644 "$KEYS_DIR/dev_public_key.pem"

echo ""
echo "✅ Keys generated:"
echo "   $KEYS_DIR/dev_private_key.pem  (private — keep secret)"
echo "   $KEYS_DIR/dev_public_key.pem   (public)"
echo ""
echo "⚠️  These keys are GITIGNORED. Do NOT commit keys/ to version control."
echo ""
echo "Set in your .env:"
echo "   AEGIS__JWT__LOCAL_PRIVATE_KEY_PATH=keys/dev_private_key.pem"
echo "   AEGIS__JWT__LOCAL_PUBLIC_KEY_PATH=keys/dev_public_key.pem"
