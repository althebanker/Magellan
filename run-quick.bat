#!/usr/bin/env bash
# Double-click (or run: ./run.sh) to build today's deck from live data and open it.
# First run: add  --quick  for a fast ~2-min test:   ./run.sh --quick
set -e
cd "$(dirname "$0")"
PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then echo "Python not found. Install it from https://python.org then re-run."; exit 1; fi
echo "Installing dependencies (first run only)…"
"$PY" -m pip install --quiet --user yfinance pandas numpy
echo "Scanning the market — this can take a few minutes…"
"$PY" screener.py "$@"
