# Core Revalidation After MCL Close — 2026-04-27

## Executive verdict

- `MCLZ6` live naked risk is resolved: the position was closed cleanly and the account is now flat.
- `gold_oil_rotation` **holds its grade on fresh data**.
- `cross_asset_momentum` is now **explicitly assumed as a `48h` runtime product**. Under that assumption it validates only as a **B-grade live product**, not as the old `A`-grade `20d` hold thesis.
- Expansion remains **NO-GO tonight**. We should not plug `eth_range_longonly_20` or any other new sleeve until we resolve the live operating mismatches that this incident exposed.

## 1. MCL close status

Reference audit: [mcl_close_audit_2026-04-27.json](/C:/Users/barqu/trading-platform/reports/checkup/mcl_close_audit_2026-04-27.json)

- Account: `U25023333`
- Instrument: `MCLZ6`
- Action: `SELL Market 1`
- Fill: `78.32`
- Fill time: `2026-04-27 17:45:47 UTC`
- Entry avg cost: `77.4677`
- Realized outcome: about `+$85.23` gross, about `+$84` net
- Post-trade account state: flat on all symbols, local state synced empty, live kill switch still armed

This removes the immediate operational risk. The live account is no longer carrying a naked crude position while the kill switch is active.

## 2. GOR revalidation on fresh data

Command used:

```powershell
$env:PYTHONIOENCODING='utf-8'; python scripts/wf_gold_oil_rotation.py
```

Fresh-data walk-forward result:

- OOS profitable windows: `5/5`
- OOS mean Sharpe: `7.16`
- OOS total PnL: `$16,722`
- Gate `3/5 OOS profitable`: `PASS`
- Gate `OOS Sharpe > 0.3`: `PASS`
- Overall: `PASS`

Interpretation:

- `gold_oil_rotation` remains a very strong research object on the restored data pipeline.
- The stale-data incident did **not** reveal a hidden collapse in `GOR` the way it did for `gold_trend_mgc`.
- Quant verdict: **grade confirmed**.

## 3. CAM revalidation on fresh data

Artifacts:

- Existing intended-product compare: [compare_summary.json](/C:/Users/barqu/trading-platform/tmp/backtest_cam_trailing/compare_summary.json)
- Fresh runtime mismatch replay: [cam_runtime_reality_2026-04-27.json](/C:/Users/barqu/trading-platform/data/research/cam_runtime_reality_2026-04-27.json)

### 3.1 Intended CAM product still works

Using the current futures CAM signal family (`20d` lookback, `2%` min momentum, `20d` rebalance, `3%` SL, `8%` TP), the fresh-data intended-product replay still looks healthy:

- Trades: `62`
- Win rate: `54.8%`
- Sharpe: `1.37`
- CAGR: `22.82%`
- Max DD: `17.64%`
- Exit mix: `28 SL`, `18 TP`, `16 REBAL`
- Existing compare WF: `4/5` profitable windows, average OOS Sharpe about `0.99`

Interpretation:

- As a **research product**, `CAM` still looks like a valid `A`-grade candidate.
- The stale-data fix did **not** invalidate the basic momentum thesis.

### 3.2 Live runtime CAM is a different product

The current live runtime caps futures positions at roughly `48h`, which materially changes the product.

Fresh-data replay under that runtime assumption:

#### `48h` time-exit only

- Trades: `64`
- Win rate: `59.4%`
- Sharpe: `0.28`
- CAGR: `1.54%`
- Max DD: `13.3%`
- Exit mix: `64 / 64 TIME_48H`

#### `48h` time-exit + nominal `3% / 8%` bracket

- Trades: `64`
- Win rate: `59.4%`
- Sharpe: `0.52`
- CAGR: `3.27%`
- Max DD: `9.78%`
- Exit mix: `57 TIME_48H`, `6 SL`, `1 TP`

That means:

- even with a normal bracket, about `89.1%` of fresh-data trades still exit via `TIME_48H`
- the broker-side `SL/TP` is already secondary in the runtime product
- on the actual `MCLZ6` live position we just closed, the `CL=F -> deferred MCLZ6` mismatch made the bracket even less likely to fire than the nominal `3% / 8%` replay

Interpretation:

- `CAM` is not failing because of fresh data.
- The real issue was product identity. That question is now resolved by decision:
  - we explicitly assume the live product is the `48h` runtime version
  - that version is materially weaker than the old `20d` thesis

Quant verdict:

- intended `20d` research object still looks like an `A`
- assumed live `48h` object is now the canonical one and should be treated as **grade B**

## 4. What this means for expansion

Tonight's verdict is:

- `GOR`: **GO as validated core research**
- `CAM`: **now aligned by decision, but only as a B-grade 48h product**
- desk expansion: **NO-GO tonight**

Why expansion stays blocked:

1. the kill switch is still armed
2. we just had a live futures position go naked before closing it
3. paper state desync is still unresolved
4. `CAM` has now been honestly reclassified as a `48h` product, but the desk still has unresolved operational issues around kill switch, bracket disappearance, and paper state desync

That combination is not a good moment to add `eth_range_longonly_20`, `pair_xle_xlk_ratio`, or any other new sleeve.

## 5. Recommended next sequence

1. Keep the live kill switch armed until the Binance loss attribution and live policy decision are explicit.
2. Investigate the bracket disappearance and paper state desync before any new expansion.
3. Revisit the `MCL Z6 vs CL=F` proxy mismatch as a separate analytical debt.
4. Decide later whether `CAM` deserves to remain `live_core` at grade `B`, or whether it should be demoted until the product is strengthened.
5. Only after those points are clean: reopen one new plug, not several.

## Bottom line

We are out of the immediate danger zone, because the naked `MCL` position is gone and the stale-data pipeline is fixed.

But we are not back in expansion mode yet.

`GOR` survived the fresh-data revalidation cleanly. `CAM` is now honest: it is a `48h` product, and that product is weaker than the old story. The remaining blocker is operational reliability, not ambiguity about what `CAM` is.
