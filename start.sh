#!/bin/bash
# The Gork v2.0 - Production Startup Script

echo "------------------------------------------------"
echo "Initializing The Gork v2.0 Environment..."
echo "------------------------------------------------"

# Ensure we are in the project directory
cd "$(dirname "$0")"

# Install/Update Dependencies
echo "[1/3] Checking dependencies..."
pip install --upgrade flask requests pyjwt pandas google-generativeai || {
    echo "Error: Failed to install dependencies. Please check your internet connection and python/pip installation."
    exit 1
}

# Database Migration/Check (if needed in future)
echo "[2/3] Verifying database integrity..."
if [ ! -f "gork_data.db" ]; then
    echo "Notice: New database will be initialized on startup."
fi

# Start the Dashboard
echo "[3/3] Launching Production Dashboard..."
echo "------------------------------------------------"
echo "THE GORK IS ONLINE. Access at: http://127.0.0.1:5000"
echo "------------------------------------------------"

python3 the_gork_v2.py
