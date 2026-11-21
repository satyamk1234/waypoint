#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x .venv/bin/python ]]; then python3 -m venv .venv; fi
.venv/bin/python -m pip install -r requirements.txt
if [[ ! -f .env ]]; then cp .env.example .env; chmod 600 .env; fi
.venv/bin/python -m app.ingest --source sample
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
