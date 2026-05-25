#!/bin/bash
set -e
cd "$(dirname "$0")"

echo "Setting up Job Applier..."

# Create venv if needed
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt
playwright install chromium

echo ""
echo "Setup complete! Run the app with:"
echo "  source .venv/bin/activate && python app.py"
echo ""
echo "Then open: http://localhost:5055"
