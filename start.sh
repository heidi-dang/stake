#!/bin/bash
echo "Installing dependencies..."
pip install flask requests pyjwt pandas

echo "Starting The Gork v2.0 Dashboard..."
python3 the_gork_v2.py
