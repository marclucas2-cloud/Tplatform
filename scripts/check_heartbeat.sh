#!/bin/bash
# Dead man's switch — alert if worker heartbeat is stale.
#
# Phase 15 XXL plan + R2 residuel post-XXL hardening (2026-04-19).
#
# Install on VPS via cron (tous les 15 min):
#   */15 * * * * /opt/trading-platform/scripts/check_heartbeat.sh >> /var/log/check_heartbeat.log 2>&1
#
# Variables d'env (lue depuis .env ou systemd):
#   TELEGRAM_BOT_TOKEN
#   TELEGRAM_CHAT_ID
#   REPO_ROOT (optional, default /opt/trading-platform)
#   MAX_AGE_SEC (optional, default 1800 = 30 min)
#   COOLDOWN_SEC (optional, default 3600 = 1h)
#
# Exit codes:
#   0 = heartbeat fresh OR stale dans cooldown (silent)
#   1 = stale + alert sent OR file missing
#   2 = config error (telegram unset, etc)

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-/opt/trading-platform}"
HEARTBEAT_FILE="${HEARTBEAT_FILE:-$REPO_ROOT/data/monitoring/heartbeat.json}"
MAX_AGE_SEC="${MAX_AGE_SEC:-1800}"
COOLDOWN_SEC="${COOLDOWN_SEC:-3600}"

# State persistant (pas /tmp qui peut purge au reboot)
STATE_DIR="${STATE_DIR:-$REPO_ROOT/data/monitoring}"
COOLDOWN_FILE="${STATE_DIR}/check_heartbeat_last_alert"
LOG_DIR="${LOG_DIR:-/var/log}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/check_heartbeat.log}"

# Source .env si present pour TELEGRAM_BOT_TOKEN / CHAT_ID
if [[ -f "$REPO_ROOT/.env" ]]; then
    # shellcheck disable=SC1091
    set +u
    source "$REPO_ROOT/.env" 2>/dev/null || true
    set -u
fi

mkdir -p "$STATE_DIR" 2>/dev/null || true

# Standardized log helper (timestamp + UTC)
_log() {
    echo "[$(date -u +%FT%TZ)] $*"
}

_send_telegram() {
    local message="$1"
    if [[ -z "${TELEGRAM_BOT_TOKEN:-}" || -z "${TELEGRAM_CHAT_ID:-}" ]]; then
        _log "WARNING: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set, skip alert"
        return 2
    fi
    local response
    response=$(curl -s -m 10 -X POST \
        "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d "chat_id=${TELEGRAM_CHAT_ID}" \
        -d "text=${message}" 2>&1)
    if [[ $? -ne 0 ]]; then
        _log "ERROR: telegram POST failed: $response"
        return 1
    fi
    return 0
}

# Sanity check: if no heartbeat file at all, escalate
if [[ ! -f "$HEARTBEAT_FILE" ]]; then
    msg=$(printf 'WORKER HEARTBEAT FILE MISSING\nfile: %s\nhost: %s\ntime: %s\nLikely worker has never started or REPO_ROOT misconfigured.' \
        "$HEARTBEAT_FILE" "$(hostname)" "$(date -u +%FT%TZ)")
    _log "$msg"
    _send_telegram "$msg" || true
    exit 1
fi

# Compute age (Linux stat -c, fallback BSD stat -f)
file_mtime=$(stat -c %Y "$HEARTBEAT_FILE" 2>/dev/null || stat -f %m "$HEARTBEAT_FILE" 2>/dev/null || echo 0)
now=$(date +%s)
if [[ "$file_mtime" -eq 0 ]]; then
    _log "ERROR: cannot stat $HEARTBEAT_FILE"
    exit 2
fi
age=$((now - file_mtime))

if [[ $age -le $MAX_AGE_SEC ]]; then
    # Fresh — clear cooldown so next stale fires immediately
    rm -f "$COOLDOWN_FILE" 2>/dev/null || true
    _log "OK: heartbeat age=${age}s (threshold=${MAX_AGE_SEC}s)"
    exit 0
fi

# Stale — check cooldown to avoid spam
if [[ -f "$COOLDOWN_FILE" ]]; then
    last_alert=$(cat "$COOLDOWN_FILE" 2>/dev/null || echo 0)
    since_last=$((now - last_alert))
    if [[ $since_last -lt $COOLDOWN_SEC ]]; then
        _log "STALE (silent within cooldown): age=${age}s, ${since_last}s since last alert"
        exit 0
    fi
fi

# Build alert message with diagnostics
age_min=$((age / 60))
last_iso=$(date -u -d "@$file_mtime" +%FT%TZ 2>/dev/null || date -u -r "$file_mtime" +%FT%TZ 2>/dev/null || echo "?")
msg=$(printf 'WORKER HEARTBEAT STALE\nage: %d min (threshold %d s)\nlast write: %s\nfile: %s\nhost: %s\nnow: %s\nDiagnose:\n  systemctl status trading-worker\n  journalctl -u trading-worker -n 100 --no-pager' \
    "$age_min" "$MAX_AGE_SEC" "$last_iso" "$HEARTBEAT_FILE" "$(hostname)" "$(date -u +%FT%TZ)")

_log "ALERT: $msg"

if _send_telegram "$msg"; then
    echo "$now" > "$COOLDOWN_FILE"
    _log "Alert sent successfully, cooldown started"
else
    _log "Alert send FAILED — will retry next cron tick"
fi

exit 1
