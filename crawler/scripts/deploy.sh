#!/usr/bin/env bash
# Deploy tender crawler on VPS
# Usage: ./scripts/deploy.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "=== Deploying tender crawler ==="

# Pull latest code
echo "[1/3] Pulling latest code..."
git pull --ff-only

# Build Docker image
echo "[2/3] Building Docker image..."
docker compose build --no-cache

# Restart service
echo "[3/3] Restarting crawler..."
docker compose up -d

echo "=== Deploy complete ==="
echo "Logs: docker compose logs -f crawler"
