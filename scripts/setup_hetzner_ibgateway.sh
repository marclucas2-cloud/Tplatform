#!/bin/bash
# =============================================================================
# SETUP IB GATEWAY + IBC sur Hetzner VPS
# Usage: scp ce fichier sur le VPS puis: bash setup_hetzner_ibgateway.sh
# =============================================================================
set -e

echo "=========================================="
echo "  IB Gateway + IBC Setup — Hetzner VPS"
echo "=========================================="

# --- Config utilisateur (A REMPLIR AVANT D'EXECUTER) ---
IB_USERNAME="${IB_USERNAME:?Settez IB_USERNAME avant d'executer}"
IB_PASSWORD="${IB_PASSWORD:?Settez IB_PASSWORD avant d'executer}"
# Usage: IB_USERNAME=xxx IB_PASSWORD=yyy bash setup_hetzner_ibgateway.sh

IBC_VERSION="3.19.0"
GATEWAY_DIR="/opt/ibgateway"
IBC_DIR="/opt/ibc"
LOG_DIR="/var/log/ibgateway"

# --- 1. Dependencies ---
echo "[1/7] Installing dependencies..."
apt update -qq
apt install -y -qq openjdk-17-jre-headless unzip xvfb wget socat > /dev/null 2>&1
echo "  OK — Java, Xvfb, socat installed"

# --- 2. IB Gateway (stable, headless) ---
echo "[2/7] Downloading IB Gateway stable..."
mkdir -p $GATEWAY_DIR
cd /tmp
if [ ! -f ibgateway-stable-standalone-linux-x64.sh ]; then
    wget -q https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh
fi
chmod +x ibgateway-stable-standalone-linux-x64.sh
echo "  Installing IB Gateway (silent)..."
./ibgateway-stable-standalone-linux-x64.sh -q -dir $GATEWAY_DIR 2>/dev/null || true
echo "  OK — IB Gateway installed to $GATEWAY_DIR"

# --- 3. IBC (auto-login, auto-restart) ---
echo "[3/7] Installing IBC $IBC_VERSION..."
mkdir -p $IBC_DIR
cd /tmp
if [ ! -f IBCLinux-${IBC_VERSION}.zip ]; then
    wget -q https://github.com/IbcAlpha/IBC/releases/download/${IBC_VERSION}/IBCLinux-${IBC_VERSION}.zip
fi
unzip -o -q IBCLinux-${IBC_VERSION}.zip -d $IBC_DIR
chmod +x $IBC_DIR/*.sh
echo "  OK — IBC installed to $IBC_DIR"

# --- 4. IBC Config — LIVE (port 4001) ---
echo "[4/7] Configuring IBC for LIVE (port 4001) + PAPER (port 7497)..."
mkdir -p $LOG_DIR

cat > $IBC_DIR/config-live.ini << EOFLIVE
# IBC Config — LIVE mode
LogToConsole=no
FIXLoginId=
IbLoginId=$IB_USERNAME
IbPassword=$IB_PASSWORD
PasswordEncrypted=no
FIXPasswordEncrypted=no
TradingMode=live
IbDir=$GATEWAY_DIR
AcceptIncomingConnectionAction=accept
AcceptNonBrokerageAccountWarning=yes
AllowBlindTrading=yes
DismissPasswordExpiryWarning=yes
DismissNSEComplianceNotice=yes
ExistingSessionDetectedAction=primary
OverrideTwsApiPort=4001
ReadOnlyLogin=no
MinimizeMainWindow=yes
StoreSettingsOnServer=no
EOFLIVE

# --- 5. IBC Config — PAPER (port 7497) ---
cat > $IBC_DIR/config-paper.ini << EOFPAPER
# IBC Config — PAPER mode
LogToConsole=no
FIXLoginId=
IbLoginId=$IB_USERNAME
IbPassword=$IB_PASSWORD
PasswordEncrypted=no
FIXPasswordEncrypted=no
TradingMode=paper
IbDir=$GATEWAY_DIR
AcceptIncomingConnectionAction=accept
AcceptNonBrokerageAccountWarning=yes
AllowBlindTrading=yes
DismissPasswordExpiryWarning=yes
DismissNSEComplianceNotice=yes
ExistingSessionDetectedAction=secondary
OverrideTwsApiPort=7497
ReadOnlyLogin=no
MinimizeMainWindow=yes
StoreSettingsOnServer=no
EOFPAPER

echo "  OK — LIVE config (port 4001) + PAPER config (port 7497)"

# --- 6. Systemd services ---
echo "[5/7] Creating systemd services..."

cat > /etc/systemd/system/ibgateway-live.service << 'EOFSVC1'
[Unit]
Description=IB Gateway LIVE (port 4001)
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=root
Environment=DISPLAY=:10
ExecStartPre=/usr/bin/Xvfb :10 -screen 0 1024x768x24 &
ExecStart=/opt/ibc/gatewaystart.sh -inline \
    --gateway \
    --ibc-path /opt/ibc \
    --ibc-ini /opt/ibc/config-live.ini \
    --java-path /usr/bin/java \
    --mode live
Restart=always
RestartSec=30
StandardOutput=append:/var/log/ibgateway/live.log
StandardError=append:/var/log/ibgateway/live-error.log

[Install]
WantedBy=multi-user.target
EOFSVC1

cat > /etc/systemd/system/ibgateway-paper.service << 'EOFSVC2'
[Unit]
Description=IB Gateway PAPER (port 7497)
After=network.target ibgateway-live.service
Wants=network-online.target

[Service]
Type=simple
User=root
Environment=DISPLAY=:11
ExecStartPre=/usr/bin/Xvfb :11 -screen 0 1024x768x24 &
ExecStart=/opt/ibc/gatewaystart.sh -inline \
    --gateway \
    --ibc-path /opt/ibc \
    --ibc-ini /opt/ibc/config-paper.ini \
    --java-path /usr/bin/java \
    --mode paper
Restart=always
RestartSec=30
StandardOutput=append:/var/log/ibgateway/paper.log
StandardError=append:/var/log/ibgateway/paper-error.log

[Install]
WantedBy=multi-user.target
EOFSVC2

systemctl daemon-reload
echo "  OK — ibgateway-live.service + ibgateway-paper.service created"

# --- 7. Firewall (UFW) ---
echo "[6/7] Configuring firewall..."
apt install -y -qq ufw > /dev/null 2>&1
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment "SSH"
ufw allow 4001/tcp comment "IB Gateway LIVE"
ufw allow 7497/tcp comment "IB Gateway PAPER"
echo "y" | ufw enable
echo "  OK — UFW: SSH + ports 4001 + 7497 ouverts"
echo ""
echo "  IMPORTANT: dans la console Hetzner > Firewalls,"
echo "  restreindre 4001+7497 a l'IP Railway uniquement !"

# --- 8. Health check script ---
echo "[7/7] Creating health check..."
cat > /opt/ibc/check_gateways.sh << 'EOFCHECK'
#!/bin/bash
# Quick health check for both gateways
echo "=== IB Gateway Health ==="
echo -n "LIVE  (4001): "
(echo "" | socat - TCP:localhost:4001,connect-timeout=2) 2>/dev/null && echo "OK" || echo "DOWN"
echo -n "PAPER (7497): "
(echo "" | socat - TCP:localhost:7497,connect-timeout=2) 2>/dev/null && echo "OK" || echo "DOWN"
echo ""
echo "Services:"
systemctl is-active ibgateway-live.service
systemctl is-active ibgateway-paper.service
EOFCHECK
chmod +x /opt/ibc/check_gateways.sh

echo ""
echo "=========================================="
echo "  INSTALLATION TERMINEE"
echo "=========================================="
echo ""
echo "Pour demarrer :"
echo "  systemctl start ibgateway-live"
echo "  systemctl start ibgateway-paper"
echo ""
echo "Pour activer au boot :"
echo "  systemctl enable ibgateway-live ibgateway-paper"
echo ""
echo "Pour verifier :"
echo "  /opt/ibc/check_gateways.sh"
echo ""
echo "Logs :"
echo "  tail -f /var/log/ibgateway/live.log"
echo "  tail -f /var/log/ibgateway/paper.log"
echo ""
echo "IMPORTANT :"
echo "  1. La premiere connexion peut demander une validation 2FA"
echo "     → ssh sur le VPS et surveiller les logs"
echo "  2. Restreindre les ports 4001+7497 dans le firewall Hetzner"
echo "     → Console Hetzner > Firewalls > Allow TCP 4001,7497 from Railway IP only"
echo ""
