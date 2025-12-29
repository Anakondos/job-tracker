#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
export JOB_TRACKER_ENV=DEV

# Start Ollama if not running
if ! pgrep -x "ollama" > /dev/null; then
    echo "🤖 Starting Ollama..."
    ollama serve &>/dev/null &
    sleep 2
fi

echo "🟡 Starting DEV on http://localhost:8001"
echo "🤖 Ollama AI: $(curl -s http://localhost:11434/api/tags >/dev/null 2>&1 && echo 'Ready' || echo 'Not available')"
uvicorn main:app --port 8001 --reload
