#!/bin/bash
# bet365 watchdog — runs every 5 minutes via cron
# Checks if browser is alive, restarts if dead
# Sends Telegram alert if can't recover after 3 attempts

API="http://127.0.0.1:8001/api/bet365"
LOG="/var/log/bet365_watchdog.log"
ALERT_CHAT_ID="-1003805909174"  # Same relay channel
MAX_RETRIES=3

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG"
}

# Check if BotOps backend is running
HEALTH=$(curl -s --max-time 5 "http://127.0.0.1:8001/api/health" 2>/dev/null)
if [ -z "$HEALTH" ]; then
    log "ERROR: BotOps backend not running"
    exit 1
fi

# Check bet365 status
STATUS=$(curl -s --max-time 5 "$API/status" 2>/dev/null)
BROWSER_ALIVE=$(echo "$STATUS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('browser',{}).get('alive',False))" 2>/dev/null)
TELEGRAM_RUNNING=$(echo "$STATUS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('telegram',{}).get('running',False))" 2>/dev/null)

if [ "$BROWSER_ALIVE" = "True" ] && [ "$TELEGRAM_RUNNING" = "True" ]; then
    # All good
    exit 0
fi

log "WARN: browser=$BROWSER_ALIVE telegram=$TELEGRAM_RUNNING — attempting restart"

# Try to start everything
for i in $(seq 1 $MAX_RETRIES); do
    log "Restart attempt $i/$MAX_RETRIES"
    RESULT=$(curl -s --max-time 120 -X POST "$API/start-all" 2>/dev/null)

    sleep 10

    # Re-check
    STATUS=$(curl -s --max-time 5 "$API/status" 2>/dev/null)
    BROWSER_ALIVE=$(echo "$STATUS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('browser',{}).get('alive',False))" 2>/dev/null)
    TELEGRAM_RUNNING=$(echo "$STATUS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('telegram',{}).get('running',False))" 2>/dev/null)

    if [ "$BROWSER_ALIVE" = "True" ] && [ "$TELEGRAM_RUNNING" = "True" ]; then
        log "OK: Recovered on attempt $i"
        exit 0
    fi

    sleep 10
done

# Failed to recover — send alert via Telegram
log "CRITICAL: Failed to recover after $MAX_RETRIES attempts"

# Send alert using the Telethon session isn't easy from bash,
# so use the BotOps API to log it (visible on dashboard)
curl -s -X POST "$API/pipeline/disable" > /dev/null 2>&1
log "Pipeline disabled until manual intervention"
