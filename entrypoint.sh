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

# Create crontab
echo "$CRON_SCHEDULE /usr/local/bin/python3 /app/main.py > /proc/1/fd/1 2>&1" > /tmp/crontab
crontab /tmp/crontab

# Start cron in background
echo "Starting cron..."
cron

# Start web server in foreground (this keeps the container alive)
echo "Starting web server and stream manager on port $SERVER_PORT..."
exec python3 /app/server.py