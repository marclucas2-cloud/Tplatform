#!/bin/bash
# Backup quotidien du trading platform live
# Cron : 0 23 * * * /path/to/scripts/backup_live.sh
#
# Sauvegarde :
#   - SQLite databases (trades, features, VaR history, execution metrics)
#   - Config files (allocation, limits, strategies, engine)
#   - State files (kill switch, leverage, autonomous, engine state)
#   - Logs des 7 derniers jours
#
# Destination : backup/ directory avec rotation 30 jours
# Taille estimee : < 50 MB compresse

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_BASE="${PROJECT_DIR}/backup"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="${BACKUP_BASE}/${DATE}"
RETENTION_DAYS=30

# Create backup directory
mkdir -p "$BACKUP_DIR"

echo "[$(date)] Starting backup to ${BACKUP_DIR}"

# 1. SQLite databases
echo "  Backing up databases..."
mkdir -p "$BACKUP_DIR/data"
for db in live_journal.db paper_journal.db execution_metrics.db var_history.db features_store.db; do
    if [ -f "${PROJECT_DIR}/data/${db}" ]; then
        # Use sqlite3 .backup for consistency (no corruption from concurrent writes)
        if command -v sqlite3 &> /dev/null; then
            sqlite3 "${PROJECT_DIR}/data/${db}" ".backup '${BACKUP_DIR}/data/${db}'"
        else
            cp "${PROJECT_DIR}/data/${db}" "${BACKUP_DIR}/data/${db}"
        fi
    fi
done

# 2. Config files
echo "  Backing up configs..."
mkdir -p "$BACKUP_DIR/config"
cp -r "${PROJECT_DIR}/config/"*.yaml "$BACKUP_DIR/config/" 2>/dev/null || true
cp -r "${PROJECT_DIR}/config/"*.json "$BACKUP_DIR/config/" 2>/dev/null || true

# 3. State files
echo "  Backing up state..."
mkdir -p "$BACKUP_DIR/state"
for state_file in kill_switch_state.json leverage_state.json autonomous_state.json engine_state.json paper_portfolio_state.json; do
    if [ -f "${PROJECT_DIR}/data/${state_file}" ]; then
        cp "${PROJECT_DIR}/data/${state_file}" "$BACKUP_DIR/state/"
    fi
done

# 4. Recent logs (7 days)
echo "  Backing up recent logs..."
mkdir -p "$BACKUP_DIR/logs"
find "${PROJECT_DIR}/logs" -name "*.log" -mtime -7 -exec cp {} "$BACKUP_DIR/logs/" \; 2>/dev/null || true
# Also backup risk audit logs
if [ -d "${PROJECT_DIR}/logs/risk_audit" ]; then
    mkdir -p "$BACKUP_DIR/logs/risk_audit"
    find "${PROJECT_DIR}/logs/risk_audit" -name "*.jsonl" -mtime -7 -exec cp {} "$BACKUP_DIR/logs/risk_audit/" \; 2>/dev/null || true
fi

# 5. Compress
echo "  Compressing..."
ARCHIVE="${BACKUP_BASE}/backup_${DATE}.tar.gz"
tar czf "$ARCHIVE" -C "$BACKUP_BASE" "$DATE"
rm -rf "$BACKUP_DIR"

# 6. Rotation (delete backups older than RETENTION_DAYS)
echo "  Cleaning old backups (>${RETENTION_DAYS} days)..."
find "$BACKUP_BASE" -name "backup_*.tar.gz" -mtime +$RETENTION_DAYS -delete 2>/dev/null || true

# 7. Summary
ARCHIVE_SIZE=$(du -h "$ARCHIVE" | cut -f1)
echo "[$(date)] Backup complete: ${ARCHIVE} (${ARCHIVE_SIZE})"

# 8. Optional: send notification
# If TELEGRAM_BOT_TOKEN is set, notify
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
    curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d "chat_id=${TELEGRAM_CHAT_ID}" \
        -d "text=Backup OK: ${ARCHIVE_SIZE} (${DATE})" \
        > /dev/null 2>&1 || true
fi
