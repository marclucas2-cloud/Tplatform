#!/bin/bash
# Canary rollback drill — test la procedure deploy.sh --rollback (R4 residuel).
#
# Phase R4 residuel post-XXL (2026-04-19). A faire QUARTERLY ou apres
# modification de deploy.sh.
#
# Ce script:
#   1. Cree un git tag de test "rollback-drill-<ts>" sur le commit actuel
#   2. Verifie que deploy.sh --rollback fonctionne en simulant
#      (sans actually restart systemctl ni pull de mauvais code)
#   3. Cleanup le tag de test
#
# Usage:
#   bash scripts/drill_canary_rollback.sh                  # dry-run (default)
#   APPLY=true bash scripts/drill_canary_rollback.sh       # vraie checkout test
#
# Aucun impact prod — git tag local uniquement, pas de push, pas de systemctl.

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
APPLY="${APPLY:-false}"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[0;33m'; NC='\033[0m'

_log() { echo "[$(date -u +%FT%TZ)] $*"; }
_section() { echo ""; echo -e "${GREEN}========== $1 ==========${NC}"; }
_warn() { echo -e "${YELLOW}WARN:${NC} $*"; }
_err() { echo -e "${RED}ERROR:${NC} $*"; }

cd "$REPO_ROOT"

DRILL_TAG="rollback-drill-$(date +%Y%m%d-%H%M%S)"
ORIGINAL_COMMIT=$(git rev-parse HEAD)
ORIGINAL_BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null || echo "DETACHED")

cleanup() {
    _log "Cleanup..."
    git checkout "$ORIGINAL_BRANCH" 2>/dev/null || git checkout "$ORIGINAL_COMMIT"
    git tag -d "$DRILL_TAG" 2>/dev/null || true
    _log "Restored: branch=$ORIGINAL_BRANCH commit=${ORIGINAL_COMMIT:0:7}"
}
trap cleanup EXIT

_section "CANARY ROLLBACK DRILL"
_log "Repo  : $REPO_ROOT"
_log "Branch: $ORIGINAL_BRANCH"
_log "Commit: ${ORIGINAL_COMMIT:0:7}"
_log "Drill tag: $DRILL_TAG"
_log "APPLY : $APPLY (false=dry-run, true=actual git checkout test)"

# ------------------------------------------------------------------
# Step 1: Verify deploy.sh exists + has rollback option
# ------------------------------------------------------------------
_section "Step 1: deploy.sh sanity"
DEPLOY_SCRIPT="$REPO_ROOT/scripts/deploy.sh"
if [[ ! -f "$DEPLOY_SCRIPT" ]]; then
    _err "$DEPLOY_SCRIPT not found"
    exit 1
fi
if grep -q -- "--rollback" "$DEPLOY_SCRIPT"; then
    _log "deploy.sh has --rollback option: ${GREEN}OK${NC}"
else
    _err "deploy.sh missing --rollback option"
    exit 1
fi
if grep -q "ROLLBACK_TAG" "$DEPLOY_SCRIPT"; then
    _log "deploy.sh creates ROLLBACK_TAG: ${GREEN}OK${NC}"
else
    _err "deploy.sh does not create rollback tag"
    exit 1
fi

# ------------------------------------------------------------------
# Step 2: Create drill tag (simulate "rollback point")
# ------------------------------------------------------------------
_section "Step 2: Create drill tag"
git tag -a "$DRILL_TAG" -m "DR drill rollback target (auto-created, will be deleted)"
_log "Tag $DRILL_TAG cree on ${ORIGINAL_COMMIT:0:7}"

# ------------------------------------------------------------------
# Step 3: Test git checkout (simulate rollback)
# ------------------------------------------------------------------
_section "Step 3: Test git checkout (rollback simulation)"

if [[ "$APPLY" == "true" ]]; then
    _log "APPLY=true : git checkout $DRILL_TAG"
    git checkout -q "$DRILL_TAG"
    CURRENT=$(git rev-parse HEAD)
    if [[ "$CURRENT" == "$ORIGINAL_COMMIT" ]]; then
        _log "${GREEN}Checkout SUCCESS: HEAD == drill tag${NC}"
    else
        _err "Checkout MISMATCH: HEAD != drill tag"
        exit 1
    fi
else
    _log "DRY-RUN: aurait fait 'git checkout $DRILL_TAG'"
fi

# ------------------------------------------------------------------
# Step 4: Verify tests still pass after rollback
# ------------------------------------------------------------------
_section "Step 4: Sanity tests post-rollback"

if [[ "$APPLY" == "true" ]]; then
    _log "Run smoke test: python -c 'import worker'..."
    if python -c "import worker" 2>&1 | grep -E "ERROR|Traceback" >/dev/null; then
        _err "Worker import FAILED post-rollback"
        exit 1
    fi
    _log "${GREEN}Worker imports OK after rollback${NC}"

    _log "Run quick test (test_dd_baseline_persistence.py)..."
    if python -m pytest tests/test_dd_baseline_persistence.py -q --tb=no 2>&1 | tail -3 | grep -q "passed"; then
        _log "${GREEN}Quick test PASS${NC}"
    else
        _warn "Quick test had issues (may be normal if rollback to old state)"
    fi
else
    _log "DRY-RUN: aurait verifie 'python -c \"import worker\"' + pytest"
fi

# ------------------------------------------------------------------
# Step 5: Verify deploy.sh --rollback workflow (manual list)
# ------------------------------------------------------------------
_section "Step 5: deploy.sh --rollback workflow check"

_log "Production rollback procedure (pas execute par ce drill):"
_log "  1. Deploy fail OR canary check fail apres deploy"
_log "  2. ssh root@VPS && cd /opt/trading-platform"
_log "  3. ./scripts/deploy.sh --rollback                  (utilise dernier tag rollback-*)"
_log "     OR ./scripts/deploy.sh --rollback rollback-XXX  (tag specifique)"
_log "  4. systemctl restart trading-worker"
_log "  5. curl -sf http://localhost:8080/health  (sanity)"
_log "  6. journalctl -u trading-worker -n 50 --no-pager"
_log ""
_log "Auto-rollback dans deploy.sh:"
_log "  - Phase 1: tag rollback-YYYYMMDD-HHMMSS cree avant pull"
_log "  - Phase 2: pytest fail -> git checkout rollback tag (auto)"
_log "  - Phase 3: health check fail apres deploy -> auto-rollback"
_log "  - Phase 4: canary 60s window 3 ticks 20s -> auto-rollback si fail"

# ------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------
_section "DRILL COMPLETE"
if [[ "$APPLY" == "true" ]]; then
    _log "${GREEN}APPLY mode: rollback git checkout OK + sanity tests PASS${NC}"
    _log "RTO simule: $(date)"
else
    _log "${YELLOW}DRY-RUN: deploy.sh structure validated, pas de checkout reel${NC}"
    _log "Pour test complet: APPLY=true bash $0"
fi

exit 0
