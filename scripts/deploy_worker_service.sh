#!/bin/bash
# Deploy trading-worker.service on Hetzner VPS
# Usage: ssh root@178.104.125.74 'bash -s' < scripts/deploy_worker_service.sh

set -e

PLATFORM_DIR="/opt/trading-platform"
SERVICE_FILE="$PLATFORM_DIR/scripts/trading-worker.service"

echo "[1/4] Checking platform directory..."
if [ ! -d "$PLATFORM_DIR" ]; then
    echo "ERROR: $PLATFORM_DIR not found"
    exit 1
fi

echo "[2/4] Installing systemd service..."
cp "$SERVICE_FILE" /etc/systemd/system/trading-worker.service
systemctl daemon-reload

echo "[3/4] Enabling and starting service..."
systemctl enable trading-worker
systemctl start trading-worker

echo "[4/4] Verifying..."
sleep 3
systemctl status trading-worker --no-pager -l

echo ""
echo "=== trading-worker.service deployed ==="
echo "  Status:  systemctl status trading-worker"
echo "  Logs:    journalctl -u trading-worker -f"
echo "  Restart: systemctl restart trading-worker"
echo "  Stop:    systemctl stop trading-worker"
