#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# Activate virtualenv
if [ ! -d "$DIR/venv" ]; then
    echo "[run] Membuat virtual environment..."
    python3 -m venv "$DIR/venv"
fi

source "$DIR/venv/bin/activate"

# Install dependencies if needed
if [ ! -f "$DIR/venv/.deps_installed" ]; then
    echo "[run] Menginstall dependencies..."
    pip install -r "$DIR/requirements.txt" --quiet
    touch "$DIR/venv/.deps_installed"
fi

# Copy config if missing
if [ ! -f "$DIR/config.json" ]; then
    if [ -f "$DIR/config.example.json" ]; then
        echo "[run] Membuat config.json dari template..."
        cp "$DIR/config.example.json" "$DIR/config.json"
        chmod 600 "$DIR/config.json"
    fi
fi

# Run
case "${1:-cli}" in
    gui|--gui)
        echo "[run] Mode GUI"
        python "$DIR/grok_register_ttk.py"
        ;;
    cli|--cli|start|"")
        echo "[run] Mode CLI"
        python "$DIR/grok_register_ttk.py" cli
        ;;
    *)
        echo "Usage: $0 [cli|gui]"
        exit 1
        ;;
esac
