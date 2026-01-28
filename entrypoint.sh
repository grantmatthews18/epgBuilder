#!/bin/bash
set -e

# Default cron schedule if not provided
: "${CRON_SCHEDULE:=0 4 * * *}"

echo "Using cron schedule: $CRON_SCHEDULE"

# Run strm_builder immediately
echo "Running strm_builder..."
python3 -m app.app