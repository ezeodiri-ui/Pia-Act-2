#!/usr/bin/env bash
# Usage: ./run.sh   (loads .env if present, builds the index on first run, serves http://localhost:8000)
set -e
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a
[ -f data/chunks.jsonl ] || python -m backend.ingest.build_index
exec uvicorn backend.api:app --host 0.0.0.0 --port "${PORT:-8000}"
