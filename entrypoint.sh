#!/bin/bash
set -e

# Default cron schedule if not provided
: "${CRON_SCHEDULE:=0 4 * * *}"

echo "Using cron schedule: $CRON_SCHEDULE"

# Get server port from environment or default to 8080
: "${SERVER_PORT:=8080}"
echo "Web server will listen on port: $SERVER_PORT"

# Run XMLTV generator immediately
echo "Running XMLTV generator"
python3 /app/main.py

# Start web server in background
echo "Starting web server..."
python3 /app/server.py &

# Create a temporary crontab with user-defined schedule, use full path to python3
echo "$CRON_SCHEDULE /usr/local/bin/python3 /app/main.py > /proc/1/fd/1 2>&1" > /tmp/crontab
crontab /tmp/crontab

# Start cron in foreground
echo "Starting cron..."
cron -f