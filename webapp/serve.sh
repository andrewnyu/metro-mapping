#!/usr/bin/env bash
# Serve the Metro-Mapping web app (static files + /api/build city generator).
cd "$(dirname "$0")"
PORT="${1:-8010}"
exec python3 serve.py "$PORT"
