#!/bin/bash
cd /Users/antonkondakov/projects/job-tracker-dev
source .venv/bin/activate
export JOB_TRACKER_ENV=DEV
echo "🟡 Starting DEV on http://localhost:8001"
uvicorn main:app --port 8001
