#!/bin/bash
# Hetzner VPS hardening installer (R3 residuel post-XXL).
#
# Usage (sur le VPS, en root):
#   sudo bash /opt/trading-platform/scripts/install_hardening_hetzner.sh status
#   sudo bash /opt/trading-platform/scripts/install_hardening_hetzner.sh install
#   sudo bash /opt/trading-platform/scripts/install_hardening_hetzner.sh ssh
#   sudo bash /opt/trading-platform/scripts/install_hardening_hetzner.sh ufw
#   sudo bash /opt/trading-platform/scripts/install_hardening_hetzner.sh fail2ban
#   sudo bash /opt/trading-platform/scripts/install_hardening_hetzner.sh updates
#   sudo bash /opt/trading-platform/scripts/install_hardening_hetzner.sh time
#
# 'install' enchaine: updates -> time -> ufw -> fail2ban -> ssh (ssh en dernier
# pour garder une session valide jusqu'a la fin si tu test depuis SSH).
#
# Idempotent. Skip silencieusement les actions deja faites.
# DRY RUN dispo via: DRY_RUN=true sudo bash ... install (ne modifie rien).

set -euo pipefail

DRY_RUN="${DRY_RUN:-false}"
ACTION="${1:-status}"

# Colors for readability
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[0;33m'; NC='\033[0m'

# Paths Debian/Ubuntu (Hetzner default)
SSHD_CONFIG="/etc/ssh/sshd_config"
SSHD_BACKUP="/etc/ssh/sshd_config.backup-$(date +%Y%m%d-%H%M%S)"
UFW_BIN="$(command -v ufw || echo /usr/sbin/ufw)"
FAIL2BAN_LOCAL="/etc/fail2ban/jail.local"

require_root() {
    if [[ $EUID -ne 0 ]]; then
        echo -e "${RED}ERROR: this script must run as root (sudo).${NC}" >&2
        exit 1
    fi
}

_run() {
    # Wrapper for command execution with dry-run support
    if [[ "$DRY_RUN" == "true" ]]; then
        echo -e "${YELLOW}[DRY-RUN]${NC} $*"
        return 0
    fi
    "$@"
}

_log() {
    echo -e "[$(date -u +%FT%TZ)] $*"
}

_section() {
    echo ""
    echo -e "${GREEN}========== $1 ==========${NC}"
}

# ------------------------------------------------------------------
# 1. Auto-updates security
# ------------------------------------------------------------------

do_updates() {
    require_root
    _section "AUTO-UPDATES SECURITY"
    if dpkg -l unattended-upgrades 2>/dev/null | grep -q '^ii'; then
        _log "unattended-upgrades already installed"
    else
        _log "Installing unattended-upgrades..."
        _run apt-get update -qq
        _run apt-get install -y unattended-upgrades apt-listchanges
    fi
    # Enable auto-config (security only)
    if [[ -f /etc/apt/apt.conf.d/20auto-upgrades ]]; then
        _log "20auto-upgrades already configured"
    else
        _log "Enabling daily auto-upgrades..."
        if [[ "$DRY_RUN" != "true" ]]; then
            cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
        else
            echo -e "${YELLOW}[DRY-RUN]${NC} Write /etc/apt/apt.conf.d/20auto-upgrades"
        fi
    fi
    _log "Auto-updates: ${GREEN}OK${NC}"
}

# ------------------------------------------------------------------
# 2. Time sync (critical for broker timestamps)
# ------------------------------------------------------------------

do_time() {
    require_root
    _section "TIME SYNC (systemd-timesyncd)"
    if ! systemctl is-enabled systemd-timesyncd >/dev/null 2>&1; then
        _log "Enabling systemd-timesyncd..."
        _run systemctl enable systemd-timesyncd
    fi
    if ! systemctl is-active systemd-timesyncd >/dev/null 2>&1; then
        _run systemctl start systemd-timesyncd
    fi
    _run timedatectl set-ntp true
    if [[ "$DRY_RUN" != "true" ]]; then
        timedatectl status | grep -E "(NTP|System clock|Time zone)" || true
    fi
    _log "Time sync: ${GREEN}OK${NC}"
}

# ------------------------------------------------------------------
# 3. UFW Firewall
# ------------------------------------------------------------------

do_ufw() {
    require_root
    _section "FIREWALL (ufw)"
    if ! command -v ufw >/dev/null 2>&1; then
        _log "Installing ufw..."
        _run apt-get install -y ufw
    fi
    _log "Configuring default policies (deny incoming, allow outgoing)..."
    _run ufw --force default deny incoming
    _run ufw --force default allow outgoing
    _log "Allowing SSH (port 22) — required to keep session alive..."
    _run ufw allow 22/tcp comment 'SSH'
    # IBKR Gateway ports — listening localhost only via worker, NEVER expose publicly
    _log "IBKR Gateway 4002/4003: bound to 127.0.0.1 by gateway config (NOT exposed externally)"
    _log "Enabling ufw..."
    _run ufw --force enable
    if [[ "$DRY_RUN" != "true" ]]; then
        ufw status verbose
    fi
    _log "Firewall: ${GREEN}OK${NC}"
}

# ------------------------------------------------------------------
# 4. fail2ban
# ------------------------------------------------------------------

do_fail2ban() {
    require_root
    _section "FAIL2BAN"
    if ! command -v fail2ban-client >/dev/null 2>&1; then
        _log "Installing fail2ban..."
        _run apt-get install -y fail2ban
    fi
    if [[ ! -f "$FAIL2BAN_LOCAL" ]]; then
        _log "Creating $FAIL2BAN_LOCAL with sshd jail (3 retries / 1h ban)..."
        if [[ "$DRY_RUN" != "true" ]]; then
            cat > "$FAIL2BAN_LOCAL" <<'EOF'
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 5
backend = systemd

[sshd]
enabled = true
maxretry = 3
findtime = 600
bantime = 3600
EOF
        else
            echo -e "${YELLOW}[DRY-RUN]${NC} Write $FAIL2BAN_LOCAL"
        fi
    else
        _log "$FAIL2BAN_LOCAL already exists, skip overwrite"
    fi
    _run systemctl enable fail2ban
    _run systemctl restart fail2ban
    sleep 2
    if [[ "$DRY_RUN" != "true" ]]; then
        fail2ban-client status sshd 2>/dev/null || _log "fail2ban not yet ready, check 'fail2ban-client status sshd' in 1 min"
    fi
    _log "fail2ban: ${GREEN}OK${NC}"
}

# ------------------------------------------------------------------
# 5. SSH hardening (LAST - keep session alive)
# ------------------------------------------------------------------

do_ssh() {
    require_root
    _section "SSH HARDENING (sshd_config)"
    if [[ ! -f "$SSHD_CONFIG" ]]; then
        _log "${RED}ERROR: $SSHD_CONFIG not found${NC}"
        return 1
    fi

    # CRITICAL safety: verify key-based access works BEFORE locking out password auth
    _log "Pre-check: at least 1 SSH key in /root/.ssh/authorized_keys ?"
    if [[ ! -s /root/.ssh/authorized_keys ]]; then
        _log "${RED}ABORT: /root/.ssh/authorized_keys is empty or missing.${NC}"
        _log "${RED}Add your SSH public key first, otherwise you will be LOCKED OUT.${NC}"
        return 1
    fi
    _log "  -> Found $(wc -l < /root/.ssh/authorized_keys) key(s) in authorized_keys"

    _log "Backing up sshd_config to $SSHD_BACKUP..."
    _run cp "$SSHD_CONFIG" "$SSHD_BACKUP"

    if [[ "$DRY_RUN" != "true" ]]; then
        # Apply hardening: comment existing then add canonical config
        sed -i 's/^#*PermitRootLogin.*/PermitRootLogin prohibit-password/' "$SSHD_CONFIG" || true
        sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' "$SSHD_CONFIG" || true
        sed -i 's/^#*ChallengeResponseAuthentication.*/ChallengeResponseAuthentication no/' "$SSHD_CONFIG" || true
        sed -i 's/^#*PubkeyAuthentication.*/PubkeyAuthentication yes/' "$SSHD_CONFIG" || true
        sed -i 's/^#*X11Forwarding.*/X11Forwarding no/' "$SSHD_CONFIG" || true
        sed -i 's/^#*PermitEmptyPasswords.*/PermitEmptyPasswords no/' "$SSHD_CONFIG" || true
        # ClientAlive (idle session timeout)
        if ! grep -q "^ClientAliveInterval" "$SSHD_CONFIG"; then
            echo "ClientAliveInterval 300" >> "$SSHD_CONFIG"
        fi
        if ! grep -q "^ClientAliveCountMax" "$SSHD_CONFIG"; then
            echo "ClientAliveCountMax 2" >> "$SSHD_CONFIG"
        fi
        _log "Validating sshd_config syntax..."
        sshd -t || { _log "${RED}sshd -t FAILED, restore backup${NC}"; cp "$SSHD_BACKUP" "$SSHD_CONFIG"; return 1; }
        _log "Reloading sshd (existing session preserved)..."
        systemctl reload sshd
    else
        echo -e "${YELLOW}[DRY-RUN]${NC} Apply sshd_config edits + sshd -t + systemctl reload sshd"
    fi
    _log "SSH hardened: ${GREEN}OK${NC} (backup at $SSHD_BACKUP)"
    _log "${YELLOW}TEST: open NEW SSH session in another terminal to verify access works.${NC}"
}

# ------------------------------------------------------------------
# Status overview
# ------------------------------------------------------------------

show_status() {
    _section "HARDENING STATUS"

    echo "1. Auto-updates security:"
    if dpkg -l unattended-upgrades 2>/dev/null | grep -q '^ii'; then
        echo -e "   unattended-upgrades: ${GREEN}installed${NC}"
        if [[ -f /etc/apt/apt.conf.d/20auto-upgrades ]]; then
            echo -e "   20auto-upgrades config: ${GREEN}present${NC}"
        else
            echo -e "   20auto-upgrades config: ${RED}missing${NC}"
        fi
    else
        echo -e "   unattended-upgrades: ${RED}not installed${NC}"
    fi

    echo "2. Time sync:"
    if systemctl is-active systemd-timesyncd >/dev/null 2>&1; then
        echo -e "   systemd-timesyncd: ${GREEN}active${NC}"
        timedatectl status 2>/dev/null | grep -E "(NTP|System clock)" | sed 's/^/      /'
    else
        echo -e "   systemd-timesyncd: ${RED}inactive${NC}"
    fi

    echo "3. Firewall (ufw):"
    if command -v ufw >/dev/null 2>&1; then
        if ufw status 2>/dev/null | grep -q "Status: active"; then
            echo -e "   ufw: ${GREEN}active${NC}"
            ufw status numbered 2>/dev/null | head -10 | sed 's/^/      /'
        else
            echo -e "   ufw: ${YELLOW}installed but inactive${NC}"
        fi
    else
        echo -e "   ufw: ${RED}not installed${NC}"
    fi

    echo "4. fail2ban:"
    if command -v fail2ban-client >/dev/null 2>&1; then
        if systemctl is-active fail2ban >/dev/null 2>&1; then
            echo -e "   fail2ban: ${GREEN}active${NC}"
            fail2ban-client status sshd 2>/dev/null | sed 's/^/      /' || echo "      (jail sshd not yet ready)"
        else
            echo -e "   fail2ban: ${YELLOW}installed but inactive${NC}"
        fi
    else
        echo -e "   fail2ban: ${RED}not installed${NC}"
    fi

    echo "5. SSH hardening:"
    if [[ -f "$SSHD_CONFIG" ]]; then
        for key in PermitRootLogin PasswordAuthentication PubkeyAuthentication X11Forwarding; do
            val=$(grep -E "^$key" "$SSHD_CONFIG" 2>/dev/null | head -1 | awk '{print $2}')
            echo "      $key: ${val:-default}"
        done
    fi
}

case "$ACTION" in
    install)
        require_root
        do_updates
        do_time
        do_ufw
        do_fail2ban
        do_ssh
        echo ""
        echo -e "${GREEN}=== HARDENING COMPLETE ===${NC}"
        show_status
        ;;
    ssh)        do_ssh ;;
    ufw)        do_ufw ;;
    fail2ban)   do_fail2ban ;;
    updates)    do_updates ;;
    time)       do_time ;;
    status)     show_status ;;
    *)
        echo "Usage: $0 {status|install|ssh|ufw|fail2ban|updates|time}"
        echo ""
        echo "Actions:"
        echo "  status   : show current hardening state (no changes)"
        echo "  install  : run all hardening steps in safe order (last = sshd)"
        echo "  ssh      : harden sshd_config only"
        echo "  ufw      : enable + configure firewall only"
        echo "  fail2ban : install + configure fail2ban only"
        echo "  updates  : enable unattended-upgrades only"
        echo "  time     : enable systemd-timesyncd only"
        echo ""
        echo "DRY-RUN: prefix avec DRY_RUN=true (test sans modifier le systeme)"
        echo ""
        echo "Defaut (no arg): status"
        exit 1
        ;;
esac
