#!/bin/bash
# Backup data before risky operations

BACKUP_DIR="$HOME/projects/job-tracker-backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_PATH="$BACKUP_DIR/backup_$TIMESTAMP"

mkdir -p "$BACKUP_PATH"

echo "📦 Backing up data to $BACKUP_PATH"

# Copy data files
cp -r /Users/antonkondakov/projects/job-tracker/data "$BACKUP_PATH/"
cp -r /Users/antonkondakov/projects/job-tracker/cache "$BACKUP_PATH/" 2>/dev/null
cp /Users/antonkondakov/projects/job-tracker/job_status.json "$BACKUP_PATH/" 2>/dev/null

echo "✅ Backup complete!"
echo "   Location: $BACKUP_PATH"
ls -la "$BACKUP_PATH"
