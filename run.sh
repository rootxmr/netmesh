#!/usr/bin/env bash
# netmesh launcher — starts API + serves the UI
cd "$(dirname "$0")"
mkdir -p runs
exec python3 netmesh.py serve --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}"

