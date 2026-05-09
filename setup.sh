#!/bin/bash
# HH-Tool — first-time setup + run
# Usage:
#   bash setup.sh          # native Python (recommended)
#   bash setup.sh docker   # Docker mode
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# Create dirs and config
mkdir -p logs
[ -f config.json ] || echo '{}' > config.json

# Docker mode
if [ "$1" = "docker" ]; then
    if ! command -v docker &>/dev/null; then
        echo "ERROR: docker not found"
        exit 1
    fi
    echo "Building and starting with Docker..."
    docker compose up --build -d
    echo ""
    echo "=== Running at http://localhost:5050 ==="
    echo "  Stop:  docker compose down"
    echo "  Logs:  docker compose logs -f"
    exit 0
fi

# Native Python
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found. Install Python 3.10+ or use: bash setup.sh docker"
    exit 1
fi

if [ ! -d .venv ]; then
    echo "Creating venv..."
    python3 -m venv .venv
fi

echo "Installing deps..."
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e hh-applicant-tool-main flask requests

echo ""
echo "=== Starting at http://localhost:5050 ==="
echo "  Stop: Ctrl+C"
echo ""
exec .venv/bin/python webui.py
