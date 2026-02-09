#!/bin/bash
# Run V8 Agent for form filling
# Usage: ./run_v8.sh <job_url>

cd "$(dirname "$0")/../.."

if [ -z "$1" ]; then
    echo "Usage: ./run_v8.sh <job_url>"
    echo "Example: ./run_v8.sh https://boards.greenhouse.io/company/jobs/12345"
    exit 1
fi

# Check if Chrome is running with debug port
if ! curl -s http://localhost:9222/json/version > /dev/null 2>&1; then
    echo "Chrome debug port not found. Starting Chrome..."
    /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
        --remote-debugging-port=9222 \
        --user-data-dir=/tmp/chrome-debug-profile \
        --window-size=1280,900 &
    sleep 3
fi

# Run the agent
python3 browser/v8/agent.py "$1"
