#!/usr/bin/env bash
# RepoLens dev: web (see scripts/dev.py and README "Start and stop"). Runs in the foreground where it is a server.
set -euo pipefail
cd "$(dirname "$0")/.."
exec apps/api/venv/bin/python scripts/dev.py web "$@"
