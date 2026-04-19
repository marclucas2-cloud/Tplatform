# Hetzner VPS hardening — Phase 20 XXL plan (2026-04-19)

VPS: 178.104.125.74 (Hetzner Frankfurt). Operateur unique. SSH only access.

## Checklist hardening

### SSH
```bash
# /etc/ssh/sshd_config minimum:
PermitRootLogin prohibit-password   # PAS yes (key-only)
PasswordAuthentication no
PubkeyAuthentication yes
ChallengeResponseAuthentication no
UsePAM yes
X11Forwarding no
PermitEmptyPasswords no
ClientAliveInterval 300
ClientAliveCountMax 2
```
Restart: `systemctl restart sshd`

### Firewall (ufw)
```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp                       # SSH (consider port change)
ufw allow from <your-home-ip> to any port 4002 proto tcp  # IBKR Gateway
ufw allow from <your-home-ip> to any port 4003 proto tcp  # IBKR Paper
# Pas d'open public 4002/4003 ! IBKR Gateway sur localhost only en interne
ufw enable
ufw status verbose
```

### fail2ban
```bash
apt install -y fail2ban
# /etc/fail2ban/jail.local
[sshd]
enabled = true
maxretry = 3
findtime = 600
bantime = 3600
systemctl restart fail2ban
```

### Auto-updates security
```bash
apt install -y unattended-upgrades
dpkg-reconfigure --priority=low unattended-upgrades
# /etc/apt/apt.conf.d/50unattended-upgrades : enable security only
```

### Time sync (critical pour broker timestamps)
```bash
apt install -y systemd-timesyncd
timedatectl set-ntp true
timedatectl status
```

### Disk monitoring
```bash
# /etc/cron.daily/disk-check
df -h | awk '$5 ~ /^[89][0-9]%/ || $5 ~ /^100%/ {print}' | mail -s "Disk full $(hostname)" you@email
```

### Audit logging
```bash
apt install -y auditd
systemctl enable auditd
# /etc/audit/rules.d/audit.rules :
-w /etc/passwd -p wa -k passwd
-w /etc/ssh/sshd_config -p wa -k sshd
-w /opt/trading-platform/.env -p rw -k secrets
augenrules --load
```

### Logging worker
```bash
# /etc/systemd/journald.conf.d/trading.conf :
[Journal]
SystemMaxUse=2G
SystemKeepFree=10G
RuntimeMaxUse=512M
```

## Etat actuel (a verifier sur VPS)

Operateur, sur le VPS:
```bash
# 1. SSH config
grep -E "^(PermitRootLogin|PasswordAuthentication|PubkeyAuthentication)" /etc/ssh/sshd_config

# 2. Firewall
ufw status verbose

# 3. fail2ban
systemctl status fail2ban
fail2ban-client status sshd

# 4. Auto-updates
apt-config dump APT::Periodic::Unattended-Upgrade

# 5. Time sync
timedatectl status | grep -E "(NTP|System clock)"

# 6. Disk
df -h /

# 7. Audit logs
systemctl is-active auditd
```

## Score post-Phase 20

- Doc hardening complete: **9/10** (cette doc)
- SSH hardened: **?** (a verifier sur VPS, recommandation prete)
- Firewall (ufw): **?** (a verifier)
- fail2ban: **?** (a verifier)
- Auto-updates: **?** (a verifier)
- Disk monitoring: **5/10** (recommande, pas wire)
- Audit logging: **5/10** (recommande, pas wire)

**Note**: Phase 20 livre la documentation. Verification + application sur VPS
necessite acces SSH + intervention manuelle operateur (out of scope automation).
