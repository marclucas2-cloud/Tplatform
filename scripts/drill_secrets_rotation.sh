#!/bin/bash
# Secrets rotation drill — checklist + verification staleness des cles (R4 residuel).
#
# Phase R4 residuel post-XXL (2026-04-19). A faire QUARTERLY (90 jours).
#
# Ce script ne ROTATE PAS les secrets (impossible automatiquement, depend des
# UI Binance/BotFather/Alpaca). Il :
#   1. Liste les secrets configures + leur derniere rotation connue
#   2. Verifie que les patterns sensibles ne sont PAS dans git tracked files
#   3. Affiche checklist des etapes manuelles
#
# Usage:
#   bash scripts/drill_secrets_rotation.sh

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ROTATION_LOG="$REPO_ROOT/data/governance/secrets_rotation.log"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[0;33m'; NC='\033[0m'

_log() { echo "[$(date -u +%FT%TZ)] $*"; }
_section() { echo ""; echo -e "${GREEN}========== $1 ==========${NC}"; }

cd "$REPO_ROOT"

_section "SECRETS ROTATION DRILL"

# ------------------------------------------------------------------
# Step 1: Inventory secrets in .env
# ------------------------------------------------------------------
_section "Step 1: Inventory .env"
if [[ -f .env ]]; then
    _log ".env file: present ($(stat -c %y .env 2>/dev/null || stat -f %Sm .env))"
    _log "Variables defined (names only, no values):"
    grep -E "^[A-Z_]+=" .env | cut -d'=' -f1 | sed 's/^/  /' || _log "  (no exported vars)"
else
    _log "${YELLOW}WARN: .env not found at $REPO_ROOT/.env${NC}"
fi

# ------------------------------------------------------------------
# Step 2: Verify .gitignore protection
# ------------------------------------------------------------------
_section "Step 2: .gitignore protection"
PROTECT_PATTERNS=(".env" ".env.*" "*.env" "**/secrets/" "*_credentials*" "*_token*" "*.key" "*.pem")
for p in "${PROTECT_PATTERNS[@]}"; do
    if grep -qF "$p" .gitignore 2>/dev/null; then
        echo -e "  ${GREEN}OK${NC} pattern in .gitignore: $p"
    else
        echo -e "  ${RED}MISSING${NC} pattern: $p"
    fi
done

# ------------------------------------------------------------------
# Step 3: Scan for committed secrets (regression check)
# ------------------------------------------------------------------
_section "Step 3: Scan committed files for secrets"
LEAKED=0
if git ls-files | xargs grep -lE "(BINANCE_API_KEY|BINANCE_API_SECRET|TELEGRAM_BOT_TOKEN)\s*=\s*['\"][a-zA-Z0-9]{15,}" 2>/dev/null | head -5; then
    echo -e "  ${RED}LEAK DETECTED${NC} dans les fichiers ci-dessus"
    LEAKED=1
fi
if git ls-files | xargs grep -lE "password\s*=\s*['\"][a-zA-Z0-9!@#$%^&*]{8,}" 2>/dev/null | head -5; then
    echo -e "  ${RED}HARDCODED PASSWORD${NC} dans les fichiers ci-dessus"
    LEAKED=1
fi
if git log --all --full-history --source -- "**/*.env" 2>/dev/null | head -1 | grep -q "commit"; then
    echo -e "  ${RED}LEAK${NC}: .env file appears in git history"
    LEAKED=1
fi
if [[ $LEAKED -eq 0 ]]; then
    echo -e "  ${GREEN}Scan OK: no secret patterns detected in tracked files or history${NC}"
fi

# ------------------------------------------------------------------
# Step 4: Last rotation log
# ------------------------------------------------------------------
_section "Step 4: Rotation log"
mkdir -p "$(dirname "$ROTATION_LOG")"
if [[ -f "$ROTATION_LOG" ]]; then
    _log "Last 10 rotation entries:"
    tail -10 "$ROTATION_LOG" | sed 's/^/  /'
else
    _log "Rotation log empty: $ROTATION_LOG"
    _log "Premier drill, ce log sera maintenu manuellement post-rotation."
fi

# ------------------------------------------------------------------
# Step 5: Checklist (manual actions)
# ------------------------------------------------------------------
_section "Step 5: Manual rotation checklist (90j cadence)"

cat <<'EOF'
[ ] BINANCE
    Binance dashboard > API Management > Create new API key
    - Label: trading-platform-YYYYMMDD
    - Restrict IP : ajouter VPS Hetzner IP only
    - Permissions : margin + spot ONLY (PAS withdraw)
    Update .env sur VPS:
      ssh root@178.104.125.74
      nano /opt/trading-platform/.env
      # Update BINANCE_API_KEY, BINANCE_API_SECRET
      systemctl restart trading-worker
    Verifier Telegram heartbeat (BINANCE: $X,XXX dans les 30 min)
    Revoquer ANCIENNE cle dans Binance dashboard
    Ajouter une ligne dans data/governance/secrets_rotation.log :
      echo "$(date -u +%FT%TZ) BINANCE rotated (signer marc)" >> data/governance/secrets_rotation.log

[ ] TELEGRAM BOT TOKEN
    BotFather > /token > generate new
    Update .env sur VPS et local + restart worker
    Old token invalidated automatiquement

[ ] IBKR PASSWORD (annuel ou si leak)
    IBKR Client Portal > Settings > Login Password > Change
    Mettre a jour login GUI Gateway sur VPS
    systemctl restart ibgateway

[ ] SSH KEY HETZNER (annuel)
    Local: ssh-keygen -t ed25519 -f ~/.ssh/id_hetzner_new
    Add new key dans /root/.ssh/authorized_keys (apres login avec ancienne)
    Test login avec nouvelle, supprimer ancienne
    Update ~/.ssh/config local pour pointer sur nouvelle

[ ] ALPACA paper (low risk, annuel suffit)
    Alpaca dashboard > Generate new API keys
    Update .env paper

EOF

# ------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------
_section "DRILL COMPLETE"
_log "Inventory + leak scan + checklist OK"
_log "Action operateur: cocher la checklist + executer les rotations + log entries"
_log "Cadence rappel: 90 jours pour BINANCE (vraies fonds), annuel pour le reste"
_log ""
_log "Next drill recommande: $(date -d "+90 days" +%Y-%m-%d 2>/dev/null || date -v+90d +%Y-%m-%d)"

exit 0
