#!/usr/bin/env bash
# Start the RepoLens OAuth-check stack (Docker, API, worker, web) from one merged configuration, then verify it.
# Usage: ./scripts/dev-start.sh [--offline]
set -euo pipefail
cd "$(dirname "$0")/.."
exec apps/api/venv/bin/python scripts/dev.py start "$@"
