#!/bin/bash
# Dead man's switch — alert if worker heartbeat is stale (Phase 15 XXL plan)
#
# Install on VPS via cron:
#   */15 * * * * /opt/trading-platform/scripts/check_heartbeat.sh
#
# Env required:
#   TELEGRAM_BOT_TOKEN
#   TELEGRAM_CHAT_ID
#
# Exit 0 if heartbeat fresh, 1 if stale (after sending alert)

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-/opt/trading-platform}"
HEARTBEAT_FILE="$REPO_ROOT/data/monitoring/heartbeat.json"
MAX_AGE_SEC=1800   # 30 min
COOLDOWN_FILE="/tmp/check_heartbeat_last_alert"
COOLDOWN_SEC=3600  # 1 alert/hour

if [[ ! -f "$HEARTBEAT_FILE" ]]; then
    msg="WORKER HEARTBEAT FILE MISSING ($HEARTBEAT_FILE). Worker may not be running."
    echo "$msg" >&2
    exit 1
fi

# Compute age in seconds
file_mtime=$(stat -c %Y "$HEARTBEAT_FILE" 2>/dev/null || stat -f %m "$HEARTBEAT_FILE")
now=$(date +%s)
age=$((now - file_mtime))

if [[ $age -le $MAX_AGE_SEC ]]; then
    # Fresh — clear cooldown so next stale alert fires immediately
    rm -f "$COOLDOWN_FILE"
    exit 0
fi

# Stale — check cooldown
if [[ -f "$COOLDOWN_FILE" ]]; then
    last_alert=$(cat "$COOLDOWN_FILE" 2>/dev/null || echo 0)
    since_last=$((now - last_alert))
    if [[ $since_last -lt $COOLDOWN_SEC ]]; then
        exit 1   # still stale but within cooldown
    fi
fi

age_min=$((age / 60))
msg=$(cat <<EOF
WORKER HEARTBEAT STALE
heartbeat age: ${age_min} min (threshold ${MAX_AGE_SEC}s)
file: $HEARTBEAT_FILE
host: $(hostname)
time: $(date -u +%FT%TZ)
EOF
)

if [[ -n "${TELEGRAM_BOT_TOKEN:-}" && -n "${TELEGRAM_CHAT_ID:-}" ]]; then
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
         -d "chat_id=${TELEGRAM_CHAT_ID}" \
         -d "text=${msg}" >/dev/null
fi

echo "$now" > "$COOLDOWN_FILE"
echo "$msg" >&2
exit 1
