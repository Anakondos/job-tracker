#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
export JOB_TRACKER_ENV=DEV
echo "🟡 Starting DEV on http://localhost:8001"
uvicorn main:app --port 8001 --reload
