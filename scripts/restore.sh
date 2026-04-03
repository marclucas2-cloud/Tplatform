#!/bin/bash
# D10-03 — Restore from backup
# Usage: ./scripts/restore.sh /opt/backups/trading-platform/backup_20260331_030000.tar.gz
#
# IMPORTANT: After restore, run reconciliation:
#   cd /opt/trading-platform && .venv/bin/python -c "from worker import reconcile_positions_at_startup; reconcile_positions_at_startup()"

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <backup_file.tar.gz>"
    echo "Available backups:"
    ls -lh /opt/backups/trading-platform/backup_*.tar.gz 2>/dev/null || echo "  (none found)"
    exit 1
fi

BACKUP_FILE="$1"
PLATFORM_DIR="/opt/trading-platform"

if [ ! -f "${BACKUP_FILE}" ]; then
    echo "ERROR: Backup file not found: ${BACKUP_FILE}"
    exit 1
fi

echo "=== RESTORE START: $(date) ==="
echo "From: ${BACKUP_FILE}"
echo "To: ${PLATFORM_DIR}"

# Safety: stop worker first
echo "Stopping services..."
systemctl stop trading-worker 2>/dev/null || true
systemctl stop trading-watchdog 2>/dev/null || true

# Backup current state before overwriting
CURRENT_BACKUP="${PLATFORM_DIR}/data_pre_restore_$(date +%Y%m%d_%H%M%S).tar.gz"
echo "Backing up current state to ${CURRENT_BACKUP}..."
cd "${PLATFORM_DIR}"
tar -czf "${CURRENT_BACKUP}" data/ config/ paper_portfolio_state.json 2>/dev/null || true

# Extract
echo "Extracting backup..."
cd "${PLATFORM_DIR}"
tar -xzf "${BACKUP_FILE}"

echo "=== RESTORE COMPLETE ==="
echo ""
echo "NEXT STEPS:"
echo "  1. Verify: ls -la ${PLATFORM_DIR}/data/"
echo "  2. Decrypt .env if needed: age -d -o .env /opt/backups/trading-platform/env_*.age"
echo "  3. Reconcile positions:"
echo "     cd ${PLATFORM_DIR} && .venv/bin/python -c 'from worker import reconcile_positions_at_startup; reconcile_positions_at_startup()'"
echo "  4. Restart services:"
echo "     systemctl start trading-watchdog"
echo "     systemctl start trading-worker"
echo "  5. Verify: systemctl status trading-worker"
