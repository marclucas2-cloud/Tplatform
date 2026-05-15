# US Equity Event-Driven Backtester

Phase B module for US single-stock long/short research. The engine is pure
backtest code and is intentionally not wired to live runtime, order routing,
registry, or limits files.

## Limites data connues

Marc decision 2026-05-15: use free data first, accept noisy preliminary
verdicts, then revisit paid data only if a family deserves deeper validation.

These limitations must be copied into every later WF manifest:

1. Universe backtest = current S&P 500 / Russell 1000 members, so survivorship
   bias is active by design.
2. No delisted equities are present. Lehman, Bear Stearns, Worldcom-like
   failures and other delisted losers are missing.
3. Earnings timestamps from yfinance/free sources can be imprecise. BMO/AMC
   classification can be wrong or missing, so PEAD will be noisy.
4. Pre-2018 coverage is degraded for some symbols. yfinance backfilled prices
   are usually usable, but corporate actions can be restated or wrong.
5. Borrow cost and locate availability do not exist in yfinance. The engine
   uses fixed borrow and locate-fail proxies.
6. Free adjusted-price data can still contain dividend/corporate-action
   restatement errors.

The engine exposes the same list in
`BacktestOutput.metrics["metadata"]["data_limitations"]`.

## Cas que le moteur NE simule PAS

- Complex corporate action chains beyond adjusted OHLC and explicit dividend
  cash columns.
- Trading halts, LULD pauses, closing auctions, opening auction imbalance, and
  intraday liquidity.
- Fails-to-deliver mechanics, threshold securities, actual Reg SHO forced
  close-outs, or broker-specific locate workflow.
- Options exercise/assignment, warrants, spin-offs, tender offers, mergers, and
  special distributions.
- Dynamic borrow rates, hard-to-borrow recalls, and short sale restrictions.
- True point-in-time index membership or point-in-time fundamentals.

## Borrow cost methodology

Default borrow model:

- commission: `$0`
- slippage: `0.02%` of notional on entry and exit
- borrow: `1.5%` annualized on short notional, accrued per trading day
- locate fail: `3%` of short entry attempts, seeded and reproducible

The 1.5% rate is a deliberately rough free-data proxy for broad "general
collateral" stock borrow. It is not a live borrow quote. IBKR documents that
daily short-sale economics depend on stock borrow fee rates, collateral value,
and short proceeds interest; those rates are symbol/date specific and absent
from yfinance. Source reference:
[IBKR Short Sale Cost](https://www.interactivebrokers.com/en/pricing/short-sale-cost.php).

Re-calibrate when one of these becomes available:

- broker historical borrow/SLB rates by symbol and date
- hard-to-borrow flag history
- actual Alpaca/IBKR locate availability logs
- strategy universe filtered to confirmed easy-to-borrow large caps

## Comment ajouter une strategie au-dessus

Pass a strategy function to `EventDrivenBacktester.run(price_data, signal_func)`.
The function receives a `SignalContext`:

```python
def signal_func(ctx):
    # ctx.as_of is the execution date.
    # ctx.history[symbol] only contains rows through close[t-1].
    # ctx.earnings_history[symbol] only contains events known through t-1.
    close_prev = ctx.history["AAPL"]["close"].iloc[-1]
    close_prev_20 = ctx.history["AAPL"]["close"].iloc[-20]
    if close_prev / close_prev_20 - 1 > 0.05:
        return [SignalIntent("AAPL", "BUY", weight=0.03, hold_days=10)]
    return []
```

Accepted signal fields:

- `symbol`: ticker in the engine universe
- `side`: `BUY`/`LONG` or `SELL`/`SHORT`
- `weight`: fraction of starting capital used as target notional
- `hold_days`: max holding period in trading bars
- `reason`: optional audit text

Execution is always at `open[t]`. Signals cannot see `close[t]` because the
engine constructs the context from history ending at `t-1`.

## Sortie standard

`BacktestOutput.trades` contains one row per completed trade:

- entry/exit dates
- side
- quantity
- entry/exit open prices
- gross PnL
- slippage cost
- borrow cost
- net PnL
- hold days
- Reg SHO warning flag

`BacktestOutput.equity_curve` contains daily equity, realized net PnL, and open
position count.

`BacktestOutput.metrics` contains:

- net Sharpe
- profit factor
- max drawdown
- hit rate
- trade count
- average hold
- total cost as percent of absolute gross PnL
- borrow cost as percent of absolute gross PnL
- bull/bear/sideways regime breakdown
- metadata and data limitations

Regime breakdown currently covers:

- bull: 2017, 2019, 2021, 2023-2024
- bear: 2018Q4, 2020Q1, 2022
- sideways: 2011, 2015-2016, marked `DEGRADED`
