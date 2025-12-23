#!/bin/bash
cd /Users/antonkondakov/projects/job-tracker
source venv/bin/activate
export JOB_TRACKER_ENV=PROD
echo "🟢 Starting PROD on http://localhost:8000"
uvicorn main:app --port 8000
