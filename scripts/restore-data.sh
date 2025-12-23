#!/bin/bash
# Restore data from backup

BACKUP_DIR="$HOME/projects/job-tracker-backups"

echo "📂 Available backups:"
ls -lt "$BACKUP_DIR" | head -10

echo ""
read -p "Enter backup name (e.g. backup_20241223_141500): " BACKUP_NAME

BACKUP_PATH="$BACKUP_DIR/$BACKUP_NAME"

if [ ! -d "$BACKUP_PATH" ]; then
    echo "❌ Backup not found: $BACKUP_PATH"
    exit 1
fi

echo "♻️ Restoring from $BACKUP_PATH..."

cp -r "$BACKUP_PATH/data/"* /Users/antonkondakov/projects/job-tracker/data/
cp -r "$BACKUP_PATH/cache/"* /Users/antonkondakov/projects/job-tracker/cache/ 2>/dev/null
cp "$BACKUP_PATH/job_status.json" /Users/antonkondakov/projects/job-tracker/ 2>/dev/null

echo "✅ Restore complete!"
