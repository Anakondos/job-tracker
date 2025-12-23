#!/bin/bash
# Safe merge from dev with automatic backup

cd /Users/antonkondakov/projects/job-tracker

echo "🔒 Running backup before merge..."
./scripts/backup-data.sh

echo ""
echo "🔀 Merging dev into main..."
git merge dev

echo ""
echo "✅ Done! If something went wrong, restore from backup."
