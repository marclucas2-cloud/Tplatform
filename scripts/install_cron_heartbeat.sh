#!/bin/bash
# Install / uninstall / status the dead man's switch cron on VPS Hetzner.
#
# Usage (sur le VPS apres deploy code) :
#   sudo bash /opt/trading-platform/scripts/install_cron_heartbeat.sh install
#   sudo bash /opt/trading-platform/scripts/install_cron_heartbeat.sh status
#   sudo bash /opt/trading-platform/scripts/install_cron_heartbeat.sh uninstall
#   sudo bash /opt/trading-platform/scripts/install_cron_heartbeat.sh test
#
# Phase R2 residuel post-XXL (2026-04-19).
#
# Idempotent : peut etre re-execute sans probleme. install ne duplique pas.

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-/opt/trading-platform}"
CHECK_SCRIPT="$REPO_ROOT/scripts/check_heartbeat.sh"
LOG_FILE="${LOG_FILE:-/var/log/check_heartbeat.log}"
CRON_LINE="*/15 * * * * $CHECK_SCRIPT >> $LOG_FILE 2>&1"
CRON_MARKER="# trading-platform-heartbeat-watchdog"

ACTION="${1:-status}"

require_root() {
    if [[ $EUID -ne 0 ]]; then
        echo "ERROR: this script must run as root (sudo)." >&2
        exit 1
    fi
}

show_status() {
    echo "=== install_cron_heartbeat.sh status ==="
    echo "REPO_ROOT     : $REPO_ROOT"
    echo "CHECK_SCRIPT  : $CHECK_SCRIPT"
    if [[ -x "$CHECK_SCRIPT" ]]; then
        echo "  -> exists + executable: OK"
    elif [[ -f "$CHECK_SCRIPT" ]]; then
        echo "  -> exists but NOT executable (run: chmod +x $CHECK_SCRIPT)"
    else
        echo "  -> MISSING (deploy code first)"
    fi
    echo "LOG_FILE      : $LOG_FILE"
    if [[ -f "$LOG_FILE" ]]; then
        n_lines=$(wc -l < "$LOG_FILE" 2>/dev/null || echo "?")
        last_log=$(tail -1 "$LOG_FILE" 2>/dev/null || echo "")
        echo "  -> exists ($n_lines lines)"
        echo "  -> last log: $last_log"
    else
        echo "  -> not yet written (cron pas encore execute)"
    fi
    echo ""
    echo "Cron entry:"
    if crontab -l 2>/dev/null | grep -F "$CRON_MARKER" -A1 -B1 >/dev/null 2>&1; then
        echo "  -> INSTALLED:"
        crontab -l 2>/dev/null | grep -F "$CRON_MARKER" -A1 | sed 's/^/    /'
    else
        echo "  -> NOT INSTALLED"
    fi
    echo ""
    echo "Heartbeat file (worker side):"
    HB="$REPO_ROOT/data/monitoring/heartbeat.json"
    if [[ -f "$HB" ]]; then
        mtime=$(stat -c %Y "$HB" 2>/dev/null || stat -f %m "$HB")
        now=$(date +%s)
        age=$((now - mtime))
        age_min=$((age / 60))
        echo "  -> exists, age=${age_min} min"
        if [[ $age -gt 1800 ]]; then
            echo "  -> WARNING: STALE (>30min) — alert should fire"
        fi
    else
        echo "  -> MISSING (worker pas encore demarre ou path different)"
    fi
}

do_install() {
    require_root
    if [[ ! -f "$CHECK_SCRIPT" ]]; then
        echo "ERROR: $CHECK_SCRIPT not found. Deploy code first." >&2
        exit 1
    fi
    chmod +x "$CHECK_SCRIPT" || true
    touch "$LOG_FILE" 2>/dev/null || true

    # Idempotent : skip if already installed
    if crontab -l 2>/dev/null | grep -F "$CRON_MARKER" >/dev/null 2>&1; then
        echo "Already installed. Run 'status' to see details, 'uninstall' first to reinstall."
        exit 0
    fi

    # Append marker + cron line via crontab edit
    (crontab -l 2>/dev/null || true; echo ""; echo "$CRON_MARKER"; echo "$CRON_LINE") | crontab -
    echo "Installed cron:"
    echo "  $CRON_LINE"
    echo ""
    echo "Logs: $LOG_FILE"
    echo "Verify with: bash $0 status"
    echo "Test now:    bash $0 test"
}

do_uninstall() {
    require_root
    if ! crontab -l 2>/dev/null | grep -F "$CRON_MARKER" >/dev/null 2>&1; then
        echo "Not installed (no marker found in crontab)."
        exit 0
    fi
    # Remove marker + next line
    crontab -l 2>/dev/null \
        | awk -v marker="$CRON_MARKER" 'BEGIN{skip=0} {if($0==marker){skip=2; next} if(skip>0){skip--; next} print}' \
        | crontab -
    echo "Uninstalled. Verify with: bash $0 status"
}

do_test() {
    if [[ ! -x "$CHECK_SCRIPT" ]]; then
        echo "ERROR: $CHECK_SCRIPT not executable." >&2
        exit 1
    fi
    echo "Running $CHECK_SCRIPT manually (will log + maybe send Telegram if stale)..."
    "$CHECK_SCRIPT"
    echo ""
    echo "Last 5 log lines:"
    tail -5 "$LOG_FILE" 2>/dev/null || echo "  (no logs yet)"
}

case "$ACTION" in
    install)   do_install ;;
    uninstall) do_uninstall ;;
    status)    show_status ;;
    test)      do_test ;;
    *)
        echo "Usage: $0 {install|uninstall|status|test}"
        echo "Default action (no arg): status"
        exit 1
        ;;
esac
