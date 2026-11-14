#!/usr/bin/env bash
# First run: creates .venv and installs dependencies. Then starts dictation.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Creating virtualenv…"
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip -q
  .venv/bin/pip install -r requirements.txt -q
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env — add your ELEVENLABS_API_KEY there and run again."
  exit 1
fi

exec .venv/bin/python dictate.py
