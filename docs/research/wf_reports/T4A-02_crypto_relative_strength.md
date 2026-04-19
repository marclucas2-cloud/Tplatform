# T4-A2 - Crypto relative strength

**Run** : 2026-04-18 15:50 UTC
**Simple universe** : ETH, SOL, BNB, XRP, ADA, DOGE, LINK, AVAX, DOT, NEAR, SUI
**Beta-adjusted universe** : ETH, SOL, BNB, XRP, ADA, LINK, AVAX, DOT, NEAR, SUI
**Data range simple** : 2024-01-01 -> 2026-03-28 (818 days)
**Data range beta** : 2024-01-01 -> 2026-03-28 (818 days)
**Cost model** : 0.26% round trip + 0.005%/day short borrow proxy

## Thesis

- a bull/bear-robust crypto sleeve should monetize dispersion, not market direction alone
- relative strength vs BTC and beta-adjusted alpha are natural candidates for that job
- weekly rebalancing keeps turnover manageable while preserving cross-sectional signal

## Variants

| Variant | Active Days | Total PnL | Sharpe | MaxDD | Verdict | Score | dSharpe | dMaxDD | Corr |
|---|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `crypto_ls_20_7_3` | 797 | $+1,180 | +0.30 | -12.5% | **PROMOTE_PAPER** | +0.198 | +0.003 | +3.55pp | +0.10 |
| `crypto_ls_20_7_2` | 797 | $+615 | +0.18 | -15.2% | **KEEP_FOR_RESEARCH** | +0.182 | -0.007 | +4.61pp | +0.07 |
| `alt_rel_strength_14_60_7` | 757 | $+4,105 | +1.11 | -7.8% | **PROMOTE_LIVE** | +0.351 | +0.186 | +0.00pp | -0.01 |
| `alt_rel_strength_14_90_7` | 727 | $+1,556 | +0.44 | -7.7% | **PROMOTE_PAPER** | +0.223 | +0.065 | +0.00pp | -0.04 |
| `alt_rel_strength_20_90_7` | 727 | $+758 | +0.22 | -11.0% | **PROMOTE_PAPER** | +0.192 | +0.032 | +0.00pp | -0.06 |

## Best candidate

- `alt_rel_strength_14_60_7`
- Verdict : **PROMOTE_LIVE**
- Marginal score : +0.351
- Delta Sharpe : +0.186
- Delta MaxDD : +0.00pp
- Corr to portfolio : -0.014

## Notes

- `crypto_ls_*` is the simple benchmark family: rank on raw alpha vs BTC
- `alt_rel_strength_*` is the closer production candidate: beta-adjusted and more aligned with STRAT-002 philosophy
- this batch is research-only and does not change live crypto config or strategy code