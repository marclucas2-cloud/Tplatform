#!/bin/bash
# Restauration depuis un backup
# Usage : ./scripts/restore_live.sh backup/backup_20260327_230000.tar.gz
#
# ATTENTION : ecrase les donnees actuelles !
# Temps estime : < 5 minutes

set -euo pipefail

if [ -z "${1:-}" ]; then
    echo "Usage: $0 <backup_archive.tar.gz>"
    echo "Example: $0 backup/backup_20260327_230000.tar.gz"
    exit 1
fi

ARCHIVE="$1"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

if [ ! -f "$ARCHIVE" ]; then
    echo "ERROR: Archive not found: $ARCHIVE"
    exit 1
fi

echo "WARNING: This will overwrite current data!"
echo "Archive: $ARCHIVE"
echo ""
read -p "Continue? (yes/no) " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "Aborted."
    exit 0
fi

# Extract to temp directory
TEMP_DIR=$(mktemp -d)
echo "Extracting to ${TEMP_DIR}..."
tar xzf "$ARCHIVE" -C "$TEMP_DIR"

# Find the extracted directory (date-named)
EXTRACTED=$(ls "$TEMP_DIR")

echo "Restoring databases..."
if [ -d "${TEMP_DIR}/${EXTRACTED}/data" ]; then
    cp "${TEMP_DIR}/${EXTRACTED}/data/"*.db "${PROJECT_DIR}/data/" 2>/dev/null || true
fi

echo "Restoring configs..."
if [ -d "${TEMP_DIR}/${EXTRACTED}/config" ]; then
    cp "${TEMP_DIR}/${EXTRACTED}/config/"* "${PROJECT_DIR}/config/" 2>/dev/null || true
fi

echo "Restoring state..."
if [ -d "${TEMP_DIR}/${EXTRACTED}/state" ]; then
    cp "${TEMP_DIR}/${EXTRACTED}/state/"* "${PROJECT_DIR}/data/" 2>/dev/null || true
fi

# Cleanup
rm -rf "$TEMP_DIR"

echo "Restore complete from $ARCHIVE"
echo "WARNING: Restart the worker to apply restored state."
