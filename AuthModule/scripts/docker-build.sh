#!/usr/bin/env bash
# =============================================================================
# scripts/docker-build.sh — Build and tag the Aegis AI Docker image
# =============================================================================
# Usage:
#   ./scripts/docker-build.sh [TAG]          # default tag: git short SHA
#   ./scripts/docker-build.sh 1.2.0          # explicit tag
#   IMAGE_REPO=us-central1-docker.pkg.dev/my-project/aegis-ai \
#     ./scripts/docker-build.sh 1.2.0        # custom repo
#
# Features:
#   - Multi-platform build (linux/amd64, linux/arm64)
#   - Layer caching via local cache
#   - Pushes to registry if PUSH=true
#   - Generates SBOM (Software Bill of Materials) if syft is installed
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

# ── Configuration ─────────────────────────────────────────────────────────────
IMAGE_REPO="${IMAGE_REPO:-aegis-ai}"
TAG="${1:-$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo 'local')}"
FULL_IMAGE="${IMAGE_REPO}:${TAG}"
LATEST_IMAGE="${IMAGE_REPO}:latest"
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
PUSH="${PUSH:-false}"
CACHE_DIR="${CACHE_DIR:-/tmp/.buildx-cache}"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║           Aegis AI Docker Build                              ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "  Image    : $FULL_IMAGE"
echo "  Platforms: $PLATFORMS"
echo "  Push     : $PUSH"
echo "  Cache    : $CACHE_DIR"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ── Ensure Docker Buildx is available ────────────────────────────────────────
if ! docker buildx version &>/dev/null; then
  echo "❌ Docker Buildx is required. Install Docker Desktop or 'docker buildx install'."
  exit 1
fi

mkdir -p "$CACHE_DIR"

# ── Build ─────────────────────────────────────────────────────────────────────
BUILD_ARGS=(
  --file "$REPO_ROOT/Dockerfile"
  --target runtime
  --platform "$PLATFORMS"
  --tag "$FULL_IMAGE"
  --tag "$LATEST_IMAGE"
  --cache-from "type=local,src=$CACHE_DIR"
  --cache-to "type=local,dest=$CACHE_DIR,mode=max"
  --label "org.opencontainers.image.revision=$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo 'unknown')"
  --label "org.opencontainers.image.created=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  --label "org.opencontainers.image.version=$TAG"
)

if [ "$PUSH" = "true" ]; then
  BUILD_ARGS+=(--push)
  echo "🚀 Building and pushing to registry..."
else
  # Load into local Docker daemon (single platform only)
  BUILD_ARGS+=(--load)
  PLATFORMS="linux/amd64"   # --load requires single platform
  echo "🔨 Building locally (use PUSH=true to push to registry)..."
fi

docker buildx build "${BUILD_ARGS[@]}" "$REPO_ROOT"

echo ""
echo "✅ Build complete: $FULL_IMAGE"

# ── SBOM (if syft is available) ───────────────────────────────────────────────
if command -v syft &>/dev/null; then
  SBOM_FILE="$REPO_ROOT/dist/aegis-ai-${TAG}-sbom.spdx.json"
  mkdir -p "$(dirname "$SBOM_FILE")"
  echo "📋 Generating SBOM → $SBOM_FILE"
  syft "$FULL_IMAGE" -o spdx-json > "$SBOM_FILE"
fi

# ── Security scan (if grype is available) ─────────────────────────────────────
if command -v grype &>/dev/null; then
  echo ""
  echo "🔍 Running vulnerability scan..."
  grype "$FULL_IMAGE" --fail-on high
fi

echo ""
echo "Run locally:"
echo "  docker run --rm -e AEGIS_ENV=development -p 8080:8080 $FULL_IMAGE"
