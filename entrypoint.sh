#!/bin/bash
set -e

# Default cron schedule if not provided
: "${CRON_SCHEDULE:=0 4 * * *}"

echo "Using cron schedule: $CRON_SCHEDULE"

# Run XMLTV generator immediately
echo "Running XMLTV generator"
python3 /app/main.py

# Create a temporary crontab with user-defined schedule, use full path to python3
echo "$CRON_SCHEDULE /usr/local/bin/python3 /app/main.py > /proc/1/fd/1 2>&1" > /tmp/crontab
crontab /tmp/crontab

# Start cron in foreground
echo "Starting cron..."
cron -f