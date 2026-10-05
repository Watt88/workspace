#!/bin/bash
# reader3: starts the reader for this computer and other devices in the same WiFi network
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

export HOST=0.0.0.0 PORT=8123
(sleep 3 && open http://localhost:8123) &
uv run server.py
