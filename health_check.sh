#!/bin/bash
# Health check script for Polymarket Bot
# Auto-restart if bot stuck or disconnected for >5 minutes

LOG_DIR="/root/polymarket-bot/logs"
LAST_LOG=$(ls -t ${LOG_DIR}/bot_TAHAP1B*.log 2>/dev/null | head -1)

if [ -z "$LAST_LOG" ]; then
    echo "$(date): No log found, bot may be dead"
    /tmp/restart_tahap1b.sh
    exit 1
fi

# Check last log update time
LAST_UPDATE=$(stat -c %Y "$LAST_LOG")
NOW=$(date +%s)
DIFF=$((NOW - LAST_UPDATE))

# If no update for 5 minutes (300 seconds), restart
if [ $DIFF -gt 300 ]; then
    echo "$(date): Bot stuck for ${DIFF}s, restarting..."
    pkill -9 -f "python.*main\.py"
    sleep 2
    /tmp/restart_tahap1b.sh
    echo "$(date): Bot restarted"
else
    echo "$(date): Bot healthy (last update ${DIFF}s ago)"
fi
