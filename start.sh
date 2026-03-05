#!/bin/bash
# The Gork v2.0 - Production Startup Script

echo "------------------------------------------------"
echo "Initializing The Gork v2.0 Environment..."
echo "------------------------------------------------"

# Ensure we are in the project directory
cd "$(dirname "$0")"

# Virtual Environment Setup
VENV_DIR="venv"

if [ ! -d "$VENV_DIR" ]; then
    echo "[1/3] Creating virtual environment..."
    python3 -m venv "$VENV_DIR" || {
        echo "Error: Failed to create virtual environment. Make sure python3-venv is installed."
        exit 1
    }
else
    echo "[1/3] Virtual environment found."
fi

# Activate virtual environment
source "$VENV_DIR/bin/activate" || {
    echo "Error: Failed to activate virtual environment."
    exit 1
}

# Install/Update Dependencies
echo "[2/3] Checking dependencies..."
# Try to update, but don't fail if network is down and packages exist
pip install flask requests pyjwt pandas google-generativeai python-dotenv pydantic --timeout 10 || {
    echo "Warning: Dependency check timed out or failed. Attempting to start anyway..."
}

# Port Management
PORT=5001
echo "Checking port $PORT..."
if command -v lsof >/dev/null 2>&1; then
    PID=$(lsof -t -i:$PORT 2>/dev/null)
    if [ -n "$PID" ]; then
        echo "Port $PORT is in use by PID $PID. Killing process..."
        kill -9 $PID
        sleep 1
    fi
else
    echo "Warning: lsof not found. Skipping port check."
fi

# Database Migration/Check
echo "[3/3] Verifying database integrity..."
if [ ! -f "gork_data.db" ]; then
    echo "Notice: New database will be initialized on startup."
fi

echo "------------------------------------------------"
echo "THE GORK IS ONLINE. Access at: http://127.0.0.1:5001"
echo "------------------------------------------------"

python3 the_gork_v2.py
