#!/bin/bash
# D10-03 — Backup & Disaster Recovery
# Daily backup of critical data to local compressed archive
# Run via cron: 0 3 * * * /opt/trading-platform/scripts/backup.sh
#
# Backs up: data/, config/, .env (encrypted), state files
# Retention: 30 days
# Restore: scripts/restore.sh <backup_file>

set -euo pipefail

# === CONFIG ===
PLATFORM_DIR="/opt/trading-platform"
BACKUP_DIR="/opt/backups/trading-platform"
RETENTION_DAYS=30
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/backup_${DATE}.tar.gz"
LOG_FILE="${PLATFORM_DIR}/logs/backup/backup_${DATE}.log"

# === SETUP ===
mkdir -p "${BACKUP_DIR}"
mkdir -p "${PLATFORM_DIR}/logs/backup"

exec > >(tee -a "${LOG_FILE}") 2>&1

echo "=== BACKUP START: $(date) ==="
echo "Platform: ${PLATFORM_DIR}"
echo "Backup to: ${BACKUP_FILE}"

# === BACKUP ===
cd "${PLATFORM_DIR}"

# Create tar with critical directories
tar -czf "${BACKUP_FILE}" \
    --exclude='*.pyc' \
    --exclude='__pycache__' \
    --exclude='.venv' \
    --exclude='node_modules' \
    --exclude='logs/worker/*.log.*' \
    data/ \
    config/ \
    paper_portfolio_state.json \
    2>/dev/null || true

# Backup .env encrypted (if age is available)
if command -v age &> /dev/null && [ -f "${PLATFORM_DIR}/.env" ]; then
    age -p -o "${BACKUP_DIR}/env_${DATE}.age" "${PLATFORM_DIR}/.env" 2>/dev/null || \
        echo "WARNING: .env encryption failed (need passphrase)"
else
    echo "INFO: age not installed — .env not backed up (install: apt install age)"
fi

# === VERIFY ===
if [ -f "${BACKUP_FILE}" ]; then
    SIZE=$(du -h "${BACKUP_FILE}" | cut -f1)
    COUNT=$(tar -tzf "${BACKUP_FILE}" | wc -l)
    echo "OK: ${BACKUP_FILE} (${SIZE}, ${COUNT} files)"
else
    echo "ERROR: Backup file not created!"
    exit 1
fi

# === CLEANUP OLD BACKUPS ===
DELETED=$(find "${BACKUP_DIR}" -name "backup_*.tar.gz" -mtime +${RETENTION_DAYS} -delete -print | wc -l)
find "${BACKUP_DIR}" -name "env_*.age" -mtime +${RETENTION_DAYS} -delete 2>/dev/null || true
echo "Cleaned up: ${DELETED} old backup(s) (>${RETENTION_DAYS} days)"

# === LOG CLEANUP ===
find "${PLATFORM_DIR}/logs/backup" -name "backup_*.log" -mtime +${RETENTION_DAYS} -delete 2>/dev/null || true

echo "=== BACKUP COMPLETE: $(date) ==="
echo "File: ${BACKUP_FILE}"
echo "Size: ${SIZE}"
