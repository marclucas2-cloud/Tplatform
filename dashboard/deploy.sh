#!/bin/bash
# Deploy Trading Dashboard to Hetzner VPS
# Usage: bash dashboard/deploy.sh
set -e

VPS="root@178.104.125.74"
REMOTE_DIR="/opt/trading-platform"
SSH_KEY="$HOME/.ssh/id_hetzner"
SSH="ssh -i $SSH_KEY $VPS"

echo "=== Trading Dashboard Deploy ==="

# 1. Build frontend locally
echo "[1/6] Building frontend..."
cd dashboard/frontend
npm install --silent
npm run build
cd ../..

# 2. Sync codebase to VPS
echo "[2/6] Syncing to VPS..."
rsync -avz --delete \
  --exclude='.git' \
  --exclude='node_modules' \
  --exclude='__pycache__' \
  --exclude='.env' \
  --exclude='venv' \
  --exclude='*.pyc' \
  --exclude='data_cache' \
  -e "ssh -i $SSH_KEY" \
  . "$VPS:$REMOTE_DIR/"

# 3. Install Python deps
echo "[3/6] Installing Python dependencies..."
$SSH "cd $REMOTE_DIR && venv/bin/pip install -q fastapi uvicorn python-dotenv pydantic 2>/dev/null || true"

# 4. Add dashboard password to .env if missing
echo "[4/6] Checking .env..."
$SSH "grep -q DASHBOARD_PASSWORD $REMOTE_DIR/.env || echo '
# Dashboard auth
DASHBOARD_USER=marc
DASHBOARD_PASSWORD=CHANGE_ME
DASHBOARD_JWT_SECRET=$(openssl rand -hex 32)
' >> $REMOTE_DIR/.env"

# 5. Setup nginx
echo "[5/6] Configuring nginx..."
$SSH "
  # Install nginx if needed
  which nginx >/dev/null 2>&1 || apt-get install -y nginx

  # Rate limit zone (add to http block if not present)
  grep -q 'limit_req_zone.*login' /etc/nginx/nginx.conf || \
    sed -i '/http {/a\\    limit_req_zone \$binary_remote_addr zone=login:10m rate=5r/m;' /etc/nginx/nginx.conf

  # Copy site config
  cp $REMOTE_DIR/dashboard/nginx/trading.conf /etc/nginx/sites-available/trading
  ln -sf /etc/nginx/sites-available/trading /etc/nginx/sites-enabled/trading
  nginx -t && systemctl reload nginx
"

# 6. Setup systemd service
echo "[6/6] Setting up dashboard service..."
$SSH "
  cp $REMOTE_DIR/dashboard/trading-dashboard.service /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable trading-dashboard
  systemctl restart trading-dashboard
  sleep 2
  systemctl status trading-dashboard --no-pager -l
"

echo ""
echo "=== Deploy complete ==="
echo "Dashboard: https://trading.aucoeurdeville-laval.fr"
echo ""
echo "IMPORTANT: Run these on VPS if first deploy:"
echo "  1. Set DASHBOARD_PASSWORD in /opt/trading-platform/.env"
echo "  2. sudo certbot --nginx -d trading.aucoeurdeville-laval.fr"
echo "  3. Add DNS A record: trading.aucoeurdeville-laval.fr -> 178.104.125.74"
