#!/bin/bash
# scripts/deploy.sh — Deploy with automatic rollback
#
# Usage:
#   ./scripts/deploy.sh              # Deploy to shadow first
#   ./scripts/deploy.sh --promote    # Promote shadow to live
#   ./scripts/deploy.sh --rollback   # Rollback to previous version

set -euo pipefail

WORKER_SERVICE="trading-worker"
SHADOW_SERVICE="trading-shadow"
HEALTH_ENDPOINT="http://localhost:8080/health"
PROJECT_DIR="/opt/trading-platform"
ROLLBACK_TAG=""

cd "$PROJECT_DIR"

case "${1:-}" in
    --promote)
        echo "=== PROMOTING SHADOW TO LIVE ==="
        systemctl restart "$WORKER_SERVICE"
        sleep 5
        if curl -sf "$HEALTH_ENDPOINT" > /dev/null 2>&1; then
            echo "Live worker restarted successfully"
        else
            echo "WARNING: Health check failed after promote"
        fi
        exit 0
        ;;

    --rollback)
        TAG="${2:-}"
        if [ -z "$TAG" ]; then
            # Find the latest rollback tag
            TAG=$(git tag -l "rollback-*" --sort=-version:refname | head -1)
            if [ -z "$TAG" ]; then
                echo "No rollback tag found"
                exit 1
            fi
        fi
        echo "=== ROLLING BACK TO $TAG ==="
        git checkout "$TAG"
        systemctl restart "$WORKER_SERVICE"
        systemctl restart "$SHADOW_SERVICE" 2>/dev/null || true
        sleep 5
        if curl -sf "$HEALTH_ENDPOINT" > /dev/null 2>&1; then
            echo "Rollback successful"
        else
            echo "WARNING: Health check failed after rollback"
        fi
        exit 0
        ;;

    *)
        echo "=== DEPLOY STARTED ==="

        # 1. Tag the current commit as rollback point
        ROLLBACK_TAG="rollback-$(date +%Y%m%d-%H%M%S)"
        git tag "$ROLLBACK_TAG"
        echo "Rollback point: $ROLLBACK_TAG"

        # 2. Pull new code
        git pull origin main

        # 3. Run tests
        echo "Running tests..."
        if ! python -m pytest tests/ -x -q --timeout=300; then
            echo "TESTS FAILED. Rolling back."
            git checkout "$ROLLBACK_TAG"
            exit 1
        fi

        # 4. Run pre-deploy checks
        echo "Running pre-deploy checks..."
        if ! python scripts/pre_deploy_check.py 2>/dev/null; then
            echo "WARNING: Pre-deploy checks had issues (non-blocking)"
        fi

        # 5. Deploy to shadow first
        echo "Deploying to shadow worker..."
        if systemctl is-active --quiet "$SHADOW_SERVICE"; then
            systemctl restart "$SHADOW_SERVICE"
        else
            echo "Shadow service not configured — deploying directly to live"
            systemctl restart "$WORKER_SERVICE"
        fi

        sleep 5

        # 6. Health check (Phase 18 XXL: auto-rollback if failed)
        if curl -sf "$HEALTH_ENDPOINT" > /dev/null 2>&1; then
            echo "Health check OK"
        else
            echo "ERROR: Health check FAILED. Auto-rollback to $ROLLBACK_TAG"
            git checkout "$ROLLBACK_TAG"
            systemctl restart "$WORKER_SERVICE"
            sleep 5
            if curl -sf "$HEALTH_ENDPOINT" > /dev/null 2>&1; then
                echo "Rollback successful — back to $ROLLBACK_TAG"
            else
                echo "CRITICAL: rollback ALSO failed health check. Manual intervention."
            fi
            exit 1
        fi

        # 7. Phase 18 XXL: 60s canary monitoring window
        echo "Canary monitoring window (60s)..."
        for i in 1 2 3; do
            sleep 20
            if ! curl -sf "$HEALTH_ENDPOINT" > /dev/null 2>&1; then
                echo "ERROR: Health check failed during canary window (tick $i/3). Auto-rollback."
                git checkout "$ROLLBACK_TAG"
                systemctl restart "$WORKER_SERVICE"
                exit 1
            fi
            echo "  canary tick $i/3 OK"
        done
        echo "Canary window CLEAN."

        echo ""
        echo "Deploy complete."
        echo "  Rollback:  ./scripts/deploy.sh --rollback $ROLLBACK_TAG"
        echo "  Promote:   ./scripts/deploy.sh --promote"
        ;;
esac
