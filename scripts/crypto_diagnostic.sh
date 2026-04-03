#!/bin/bash
# Fix #7: Crypto-specific diagnostic — run on VPS
# Usage: bash scripts/crypto_diagnostic.sh

echo "=== CRYPTO DIAGNOSTIC ==="
echo "Date: $(date)"
echo ""

echo "--- Worker crypto cycle ---"
grep -i "crypto" logs/worker.log 2>/dev/null | tail -20 || echo "No worker.log found"
echo ""

echo "--- Candles received ---"
grep -i "candle\|kline\|ohlcv\|BTCUSDC\|ETHUSDC" logs/worker.log 2>/dev/null | tail -10 || echo "No candle data in logs"
echo ""

echo "--- Signals crypto ---"
grep -i "signal.*crypto\|crypto.*signal" logs/worker.log 2>/dev/null | tail -10 || echo "No crypto signals in logs"
echo ""

echo "--- Rejections crypto ---"
grep -i "crypto.*reject\|crypto.*block\|crypto.*skip\|crypto.*kill" logs/worker.log 2>/dev/null | tail -10 || echo "No rejections found"
echo ""

echo "--- FUNNEL crypto ---"
grep "FUNNEL.*crypto" logs/worker.log 2>/dev/null | tail -20 || echo "No FUNNEL logs (funnel_logger not yet wired)"
echo ""

echo "--- Kill switch crypto ---"
cat data/crypto_kill_switch_state.json 2>/dev/null || echo "No crypto kill switch state file"
echo ""

echo "--- DD state crypto ---"
cat data/crypto_dd_state.json 2>/dev/null || echo "No crypto DD state file"
echo ""

echo "--- Regime state ---"
cat data/regime_state.json 2>/dev/null || echo "No regime state file"
echo ""

echo "--- Binance connection test ---"
python3 -c "
import os, sys
sys.path.insert(0, '.')
try:
    from core.broker.binance_broker import BinanceBroker
    print('Binance module: OK')
except Exception as e:
    print(f'Binance module: FAIL — {e}')

api_key = os.environ.get('BINANCE_API_KEY', '')
live = os.environ.get('BINANCE_LIVE_CONFIRMED', 'false')
print(f'API key: {\"present\" if api_key else \"MISSING\"} | Live: {live}')
" 2>&1 || echo "Python check failed"
echo ""

echo "--- Capital allocation check ---"
python3 -c "
import yaml
try:
    with open('config/crypto_allocation.yaml') as f:
        cfg = yaml.safe_load(f)
    wallets = cfg.get('wallets', {})
    total = sum(wallets.values())
    print(f'Total capital: {total} EUR')
    for w, v in wallets.items():
        print(f'  {w}: {v} EUR')
    kelly = cfg.get('phases', {}).get('week_1', {}).get('kelly_fraction', 0.125)
    print(f'Kelly fraction: {kelly}')
    n_strats = len(cfg.get('regime_allocations', {}).get('BULL', {}))
    print(f'Active strats: {n_strats}')
    print(f'Avg position: {total * kelly / n_strats:.0f} EUR')
except Exception as e:
    print(f'Config read failed: {e}')
" 2>&1 || echo "Config check failed"

echo ""
echo "=== END CRYPTO DIAGNOSTIC ==="
