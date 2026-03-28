#!/bin/bash
# Reminder script for Binance API key rotation
# Run every 90 days to check if keys need rotation

echo "=== Binance API Key Rotation Check ==="
echo "Date: $(date)"
echo ""

# Check when .env was last modified
if [ -f ".env" ]; then
    LAST_MOD=$(stat -c %Y .env 2>/dev/null || stat -f %m .env 2>/dev/null)
    NOW=$(date +%s)
    DAYS_AGO=$(( (NOW - LAST_MOD) / 86400 ))
    echo ".env last modified: $DAYS_AGO days ago"

    if [ $DAYS_AGO -gt 90 ]; then
        echo "WARNING: API keys may need rotation (>90 days old)"
        echo ""
        echo "Steps:"
        echo "  1. Log into Binance"
        echo "  2. Go to API Management"
        echo "  3. Create new API key"
        echo "  4. Update .env with new keys"
        echo "  5. Delete old API key on Binance"
        echo "  6. Verify: python -c 'from core.broker.binance_broker import BinanceBroker; b=BinanceBroker(); print(b.authenticate())'"
    else
        echo "OK: Keys are ${DAYS_AGO} days old (rotation at 90 days)"
    fi
else
    echo "ERROR: .env file not found"
fi
