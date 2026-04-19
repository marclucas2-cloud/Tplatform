#!/bin/bash
# DR restore drill — test la procedure de restoration backup en sandbox.
#
# Phase R4 residuel post-XXL (2026-04-19). A faire QUARTERLY.
#
# Ce script:
#   1. Cree une copie sandbox du repo (workspace temporaire)
#   2. Trouve le dernier backup data/backups/<date>/<ts>/
#   3. Simule la restoration dans la sandbox
#   4. Verifie integrite via load_baselines + load_state
#   5. Compare positions/configs avant/apres restoration
#   6. Cleanup la sandbox
#
# Usage:
#   bash scripts/drill_dr_restore.sh                  # dry-run (default)
#   APPLY=true bash scripts/drill_dr_restore.sh       # actual restore (sandbox only)
#
# NE TOUCHE JAMAIS le repo principal — sandbox isolated.

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SANDBOX_DIR="${SANDBOX_DIR:-/tmp/dr_drill_$(date +%Y%m%d_%H%M%S)}"
APPLY="${APPLY:-false}"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[0;33m'; NC='\033[0m'

_log() { echo "[$(date -u +%FT%TZ)] $*"; }
_section() { echo ""; echo -e "${GREEN}========== $1 ==========${NC}"; }
_warn() { echo -e "${YELLOW}WARN:${NC} $*"; }
_err() { echo -e "${RED}ERROR:${NC} $*"; }

cleanup() {
    if [[ -d "$SANDBOX_DIR" ]]; then
        _log "Cleanup sandbox $SANDBOX_DIR..."
        rm -rf "$SANDBOX_DIR"
    fi
}
trap cleanup EXIT

_section "DR RESTORE DRILL"
_log "REPO_ROOT  : $REPO_ROOT"
_log "SANDBOX    : $SANDBOX_DIR"
_log "APPLY      : $APPLY (false=dry-run, ne fait que verifier)"

# ------------------------------------------------------------------
# Step 1: Find latest backup
# ------------------------------------------------------------------
_section "Step 1: Find latest backup"
BACKUP_ROOT="$REPO_ROOT/data/backups"
if [[ ! -d "$BACKUP_ROOT" ]]; then
    _err "Pas de dossier backups : $BACKUP_ROOT"
    _err "Run d'abord: python scripts/backup_state.py"
    exit 1
fi

LATEST_DATE=$(ls -1 "$BACKUP_ROOT" 2>/dev/null | sort -r | head -1)
if [[ -z "$LATEST_DATE" ]]; then
    _err "Aucun backup trouve dans $BACKUP_ROOT"
    exit 1
fi

LATEST_TS_DIR=$(ls -1 "$BACKUP_ROOT/$LATEST_DATE" 2>/dev/null | sort -r | head -1)
if [[ -z "$LATEST_TS_DIR" ]]; then
    _err "Aucun timestamp dans $BACKUP_ROOT/$LATEST_DATE"
    exit 1
fi

LATEST_BACKUP="$BACKUP_ROOT/$LATEST_DATE/$LATEST_TS_DIR"
_log "Latest backup: $LATEST_BACKUP"
_log "Files in backup:"
find "$LATEST_BACKUP" -type f | head -10 | sed 's/^/  /'
N_FILES=$(find "$LATEST_BACKUP" -type f | wc -l)
_log "Total: $N_FILES files"

# ------------------------------------------------------------------
# Step 2: Create sandbox + copy current state
# ------------------------------------------------------------------
_section "Step 2: Create sandbox"
mkdir -p "$SANDBOX_DIR"
_log "Sandbox cree: $SANDBOX_DIR"

# Copy current data/state for comparison after restore
if [[ -d "$REPO_ROOT/data/state" ]]; then
    cp -a "$REPO_ROOT/data/state" "$SANDBOX_DIR/state_before_restore"
    _log "Snapshot pre-restore copie dans $SANDBOX_DIR/state_before_restore"
fi

# ------------------------------------------------------------------
# Step 3: Simulate restoration
# ------------------------------------------------------------------
_section "Step 3: Simulate restoration"
mkdir -p "$SANDBOX_DIR/restored"

if [[ "$APPLY" == "true" ]]; then
    _log "APPLY=true : copie reelle backup -> sandbox/restored/"
    cp -a "$LATEST_BACKUP/." "$SANDBOX_DIR/restored/"
else
    _log "DRY-RUN : liste ce qui SERAIT copie:"
    find "$LATEST_BACKUP" -type f -printf '  -> %P\n' | head -20
    _log "(skipping cp pour dry-run, ajout APPLY=true pour executer reellement)"
fi

# ------------------------------------------------------------------
# Step 4: Integrity check on restored state
# ------------------------------------------------------------------
_section "Step 4: Integrity check"

if [[ "$APPLY" != "true" ]]; then
    _warn "Skip integrity check en mode dry-run"
else
    cd "$REPO_ROOT"
    # Test load_baselines on restored DD state
    DD_PATH="$SANDBOX_DIR/restored/data/crypto_dd_state.json"
    if [[ -f "$DD_PATH" ]]; then
        _log "Testing DDBaselines.load on $DD_PATH..."
        python3 -c "
from pathlib import Path
from core.crypto.dd_baseline_state import load_baselines, BootState
state, baselines = load_baselines(Path('$DD_PATH'))
print(f'  state={state.value}, peak=\${baselines.peak_equity:,.0f}, ' +
      f'session_id={baselines.session_id[:8]}')
assert state in (BootState.STATE_RESTORED, BootState.STATE_STALE), f'STATE_CORRUPT detecte'
print('  -> DDBaselines integrity OK')
" || { _err "DDBaselines integrity FAILED"; exit 1; }
    else
        _warn "Pas de crypto_dd_state.json dans backup, skip"
    fi

    # Test load_state on restored OrderTracker
    OT_PATH="$SANDBOX_DIR/restored/data/state/order_tracker.json"
    if [[ -f "$OT_PATH" ]]; then
        _log "Testing OrderTracker.load on $OT_PATH..."
        python3 -c "
from pathlib import Path
from core.execution.order_tracker import OrderTracker
t = OrderTracker(state_path=Path('$OT_PATH'))
summary = t.recovery_summary()
print(f'  recovered={summary[\"total_recovered\"]} active={len(summary[\"active_order_ids\"])}')
print('  -> OrderTracker integrity OK')
" || { _err "OrderTracker integrity FAILED"; exit 1; }
    else
        _warn "Pas de order_tracker.json dans backup, skip"
    fi

    # Test live_whitelist load
    WL_PATH="$SANDBOX_DIR/restored/config/live_whitelist.yaml"
    if [[ -f "$WL_PATH" ]]; then
        _log "Testing whitelist YAML parse on $WL_PATH..."
        python3 -c "
import yaml
with open('$WL_PATH') as f:
    data = yaml.safe_load(f)
total = sum(len(v) for v in data.values() if isinstance(v, list))
print(f'  whitelist parses OK, {total} strats restorees')
" || { _err "Whitelist parse FAILED"; exit 1; }
    fi
fi

# ------------------------------------------------------------------
# Step 5: Diff before/after (if APPLY)
# ------------------------------------------------------------------
_section "Step 5: Diff pre vs post restoration"

if [[ "$APPLY" == "true" && -d "$SANDBOX_DIR/state_before_restore" ]]; then
    BEFORE_FILES=$(find "$SANDBOX_DIR/state_before_restore" -type f | wc -l)
    AFTER_FILES=$(find "$SANDBOX_DIR/restored" -type f 2>/dev/null | wc -l)
    _log "Files: pre=$BEFORE_FILES post=$AFTER_FILES"
    DIFF_COUNT=$(diff -rq "$SANDBOX_DIR/state_before_restore" "$SANDBOX_DIR/restored" 2>/dev/null | wc -l || echo "?")
    _log "Differences detected: $DIFF_COUNT"
else
    _log "Skip diff (dry-run ou pas de pre-snapshot)"
fi

# ------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------
_section "DRILL COMPLETE"
_log "Latest backup: $LATEST_BACKUP ($N_FILES files)"
if [[ "$APPLY" == "true" ]]; then
    _log "${GREEN}APPLY mode: backup deserialize OK + integrity checks PASS${NC}"
    _log "Recommande RTO mesure: $(date +%s -d 'now') -> calcule duration"
    _log ""
    _log "Procedure prod (pas execute par ce drill):"
    _log "  1. ssh root@VPS && systemctl stop trading-worker"
    _log "  2. mv data/state data/state.before_restore.\$(date +%Y%m%d_%H%M%S)"
    _log "  3. cp -a $LATEST_BACKUP/. ./"
    _log "  4. python3 -c 'from core.crypto.dd_baseline_state import load_baselines; ...' (verify)"
    _log "  5. systemctl start trading-worker && journalctl -u trading-worker -f"
    _log "  6. Telegram alerte: 'OrderTracker recovered N orders'"
else
    _log "${YELLOW}DRY-RUN mode: verification structure backup OK, pas de restoration reelle${NC}"
    _log "Pour test complet: APPLY=true bash $0"
fi

exit 0
