#!/usr/bin/env bash
# Stop the processes dev-start.sh started (never anything else). Data is never touched.
# Usage: ./scripts/dev-stop.sh [--docker] [--force]
set -euo pipefail
cd "$(dirname "$0")/.."
exec apps/api/venv/bin/python scripts/dev.py stop "$@"
