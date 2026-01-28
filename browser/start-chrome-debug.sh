#!/bin/bash
# Start Chrome with remote debugging enabled
# This allows Playwright to connect to your existing Chrome with all logins/cookies

PORT=9222

# Check if Chrome is already running with debugging
if lsof -i :$PORT > /dev/null 2>&1; then
    echo "✅ Chrome already running with debugging on port $PORT"
    echo "   You can connect with: BrowserMode.CDP"
    exit 0
fi

echo "🚀 Starting Chrome with remote debugging on port $PORT..."
echo ""
echo "   This Chrome instance will have your regular profile."
echo "   All logins, cookies, extensions will be available."
echo ""

# Start Chrome with debugging
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
    --remote-debugging-port=$PORT \
    --user-data-dir="$HOME/Library/Application Support/Google/Chrome" \
    &

sleep 2

if lsof -i :$PORT > /dev/null 2>&1; then
    echo "✅ Chrome started successfully!"
    echo ""
    echo "Now you can run:"
    echo "   python browser/v5/engine.py <job_url>"
    echo ""
    echo "Or in Python:"
    echo "   from browser.v5 import FormFillerV5, BrowserMode"
    echo "   filler = FormFillerV5(browser_mode=BrowserMode.CDP)"
    echo "   filler.fill('https://...')"
else
    echo "❌ Failed to start Chrome"
    exit 1
fi
