# T2-A — Futures crisis alpha / vol overlay

**Run** : 2026-04-16 06:22 UTC
**Methodologie** : short MES a proxy long vol (VIX calm ou VIX breakout).
**Gate special** : PROMOTE_LIVE_SMALL possible meme si dSharpe negatif, SI dMaxDD >= +2pp
(crisis hedge convexe).

## Scorecards

| Variant | Verdict | Score | dSharpe | dMaxDD | Corr | Crisis hedge? |
|---|---|---:|---:|---:|---:|---|
| `short_mes_vix_breakout_130` | **DROP** | -0.262 | -0.077 | -10.76pp | -0.06 | - |
| `short_mes_vix_breakout_150` | **DROP** | -0.303 | -0.048 | -8.93pp | -0.07 | - |
| `short_mes_vix_lt_15` | **DROP** | -0.384 | -0.198 | -68.30pp | -0.03 | - |
| `short_mes_vix_lt_13` | **DROP** | -0.529 | -0.107 | -44.04pp | -0.00 | - |
| `short_mes_vix_lt_18` | **DROP** | -0.606 | -0.255 | -62.17pp | -0.07 | - |
| `short_mes_vix_breakout_120` | **DROP** | -0.806 | -0.140 | -14.53pp | -0.06 | - |

## Crisis hedge candidates (dMaxDD >= +2pp)

(none)
