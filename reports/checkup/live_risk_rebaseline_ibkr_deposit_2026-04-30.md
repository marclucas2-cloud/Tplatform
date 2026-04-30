# Live Risk Rebaseline — 2026-04-30

## Why

IBKR live equity increased materially after a fresh `+$15K` capital injection.
Without a same-day rebaseline, `data/live_risk_dd_state.json` would keep the old
`daily_start_equity` from the pre-deposit regime and overstate `daily_pnl_pct`
for the rest of the UTC day.

## Scope

- Broker: `IBKR live`
- Runtime file: `data/live_risk_dd_state.json` on the VPS
- Reason: normalize same-day live risk metrics after capital deposit

## Before

- `daily_start_equity`: `$11,359.51`
- `date`: `2026-04-30`

## After

- `daily_start_equity`: `$26,353.84`
- `peak_equity`: `$26,353.84`
- `date`: `2026-04-30`

## Notes

- `_baselines_synced` is a crypto DD concept and does **not** apply to the live
  IBKR futures `live_risk_dd_state.json` schema.
- No worker restart is required. The next `run_live_risk_cycle()` reads the file
  and uses the new baseline.
- This is an operational state correction, not a strategy or sizing change.
