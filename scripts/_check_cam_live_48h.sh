#!/bin/bash
# Check 48h window: did CAM/GOR live produce an actionable signal,
# and did IBKR live (U25023333) receive a real order/fill?
# Verdicts: OK / BUG / WARN_BUDGET / UNCLEAR

set -u
cd /opt/trading-platform

NOW_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)
NOW_EPOCH=$(date -u +%s)
WINDOW_HOURS=48
SINCE_EPOCH=$((NOW_EPOCH - WINDOW_HOURS * 3600))
SINCE_HUMAN=$(date -u -d "@$SINCE_EPOCH" +%Y-%m-%dT%H:%M:%SZ)

LIVE_ACCOUNT="U25023333"
REPORT="reports/checkup/cam_live_check_$(date -u +%Y-%m-%d_%H%M).md"
LOGS="logs/worker/worker.log logs/worker/worker.log.1"

# Real worker log patterns:
# - actionable live futures signals: "Cross-Asset Mom (LIVE): BUY ..."
# - actionable GOR live signals:     "Gold-Oil Rotation (LIVE): BUY ..."
# - xmomentum analysis only:         "XMOMENTUM SIGNAL: ..."
# - budget block:                    "SKIP - risk budget exceeded ..."
LIVE_SIGNAL_PATTERN="Cross-Asset Mom \\(LIVE\\): (BUY|SELL)|Gold-Oil Rotation \\(LIVE\\): (BUY|SELL)|FUTURES LIVE:.*\\b(BUY|SELL)\\b"
XMOM_SIGNAL_PATTERN="XMOMENTUM SIGNAL"
BUDGET_BLOCK_PATTERN="SKIP.*risk budget exceeded|risk budget exceeded|budget saturat|RISK_BUDGET"
BUDGET_STATUS_PATTERN="FUTURES LIVE: risk budget|risk budget \\$[0-9]"

mkdir -p reports/checkup

awk_filter() {
  awk -v since="$SINCE_EPOCH" '
    {
      ts = $1 " " $2
      gsub(",.*", "", ts)
      cmd = "date -u -d \"" ts "\" +%s 2>/dev/null"
      cmd | getline epoch
      close(cmd)
      if (epoch >= since) print $0
    }
  ' "$@"
}

count_lines() {
  if [ -z "$1" ]; then
    echo 0
  else
    echo "$1" | wc -l | tr -d ' '
  fi
}

# 1) Actionable LIVE futures signals (CAM / GOR)
LIVE_SIGNAL_LINES=$(grep -E "$LIVE_SIGNAL_PATTERN" $LOGS 2>/dev/null | awk_filter)
LIVE_SIGNAL_COUNT=$(count_lines "$LIVE_SIGNAL_LINES")

# 2) XMOMENTUM analysis signals (not the live futures executor)
XMOM_SIGNAL_LINES=$(grep -E "$XMOM_SIGNAL_PATTERN" $LOGS 2>/dev/null | awk_filter)
XMOM_SIGNAL_COUNT=$(count_lines "$XMOM_SIGNAL_LINES")

# 3) Real fills on canonical live account
LIVE_FILLS=$(grep -E "acctNumber='${LIVE_ACCOUNT}'" $LOGS 2>/dev/null | grep -E "side='(BOT|SLD)'" | awk_filter)
LIVE_FILL_COUNT=$(count_lines "$LIVE_FILLS")

# 4) Worker live order / signal lines
LIVE_ORDERS=$(grep -E "$LIVE_SIGNAL_PATTERN" $LOGS 2>/dev/null | awk_filter)
LIVE_ORDER_COUNT=$(count_lines "$LIVE_ORDERS")

# 5) Risk budget blocks
BUDGET_BLOCKS=$(grep -E "$BUDGET_BLOCK_PATTERN" $LOGS 2>/dev/null | awk_filter)
BUDGET_BLOCK_COUNT=$(count_lines "$BUDGET_BLOCKS")

# 6) Last risk budget status line
LAST_BUDGET=$(grep -E "$BUDGET_STATUS_PATTERN" $LOGS 2>/dev/null | awk_filter | tail -1)

# 7) Current live position state
LIVE_POS=$(cat data/state/futures_positions_live.json 2>/dev/null || echo "{}")

# 8) Live kill switch status
KILL_LIVE=$(python3 -c "import json; d=json.load(open('data/kill_switch_state.json')); print('ACTIVE' if d.get('active') else 'inactive')" 2>/dev/null || echo "unknown")

# === VERDICT ===
VERDICT=""
DETAIL=""

if [ "$LIVE_SIGNAL_COUNT" -eq 0 ] && [ "$XMOM_SIGNAL_COUNT" -eq 0 ]; then
  VERDICT="OK"
  DETAIL="No live actionable signal in the last 48h (normal silence for signal-driven sleeves)."
elif [ "$LIVE_FILL_COUNT" -gt 0 ]; then
  VERDICT="OK"
  DETAIL="Live signal present and a real fill on $LIVE_ACCOUNT confirms the live executor is working."
elif [ "$BUDGET_BLOCK_COUNT" -gt 0 ] && [ "$LIVE_SIGNAL_COUNT" -gt 0 ]; then
  VERDICT="WARN_BUDGET"
  DETAIL="Live signal present but blocked by risk budget. This looks like a sizing / budget issue, not an executor hole. ($LAST_BUDGET)"
elif [ "$LIVE_SIGNAL_COUNT" -gt 0 ] && [ "$LIVE_FILL_COUNT" -eq 0 ]; then
  VERDICT="BUG"
  DETAIL="Live futures signal present (CAM/GOR) but zero fill on $LIVE_ACCOUNT. Probable live executor hole."
elif [ "$XMOM_SIGNAL_COUNT" -gt 0 ] && [ "$LIVE_FILL_COUNT" -eq 0 ] && [ "$LIVE_SIGNAL_COUNT" -eq 0 ]; then
  VERDICT="BUG"
  DETAIL="XMOMENTUM emitted signals but no CAM/GOR live signal appeared. Possible missing live cycle or missing routing."
else
  VERDICT="UNCLEAR"
  DETAIL="Ambiguous state. Read the captured lines below."
fi

cat > "$REPORT" <<EOF
# CAM LIVE CHECK 48h - $NOW_UTC

## Verdict: **$VERDICT**

$DETAIL

## Window
- Since: $SINCE_HUMAN
- Until: $NOW_UTC
- Logs scanned: worker.log + worker.log.1

## Counters
| Metric | Count |
|--------|-------|
| Live CAM/GOR signals detected | $LIVE_SIGNAL_COUNT |
| XMOMENTUM analysis signals | $XMOM_SIGNAL_COUNT |
| Worker live order / intent lines | $LIVE_ORDER_COUNT |
| Real fills on live account $LIVE_ACCOUNT | $LIVE_FILL_COUNT |
| Risk budget block mentions | $BUDGET_BLOCK_COUNT |

## Current state
- Live kill switch: $KILL_LIVE
- Last risk budget line: \`$LAST_BUDGET\`
- Live position state: \`$LIVE_POS\`

## Captured lines

### Live CAM/GOR signals
\`\`\`
$LIVE_SIGNAL_LINES
\`\`\`

### XMOMENTUM signals
\`\`\`
$(echo "$XMOM_SIGNAL_LINES" | head -30)
\`\`\`

### Worker live order / intent lines
\`\`\`
$LIVE_ORDERS
\`\`\`

### Live fills on $LIVE_ACCOUNT
\`\`\`
$LIVE_FILLS
\`\`\`

### Risk budget blocks
\`\`\`
$BUDGET_BLOCKS
\`\`\`

## Next action by verdict
- **OK**: nothing to do, live silence is coherent
- **WARN_BUDGET**: review live futures risk budget sizing before assuming an executor bug
- **BUG**: investigate CAM live cycle / GOR live cycle / routing to IBKR live
- **UNCLEAR**: inspect the raw lines above
EOF

echo "Report: $REPORT"
echo "Verdict: $VERDICT"
echo "$DETAIL"
