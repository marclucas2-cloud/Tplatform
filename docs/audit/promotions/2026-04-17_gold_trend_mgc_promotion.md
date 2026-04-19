# Promotion request — gold_trend_mgc

**Date** : 2026-04-17
**Submitted by** : Marc
**Target status** : live_probation
**Current status** : paper

## Doctrine projet (gates obligatoires)

- [ ] **Backtest reproductible** : path + seed + commit hash
- [ ] **Walk-forward 5 windows** : >= 3/5 OOS profitable
- [ ] **Monte Carlo 1000 sims** : P(DD>30%) < 15%
- [ ] **Stress tests** : 2018-19, 2020-21, 2022, 2023-24, 2025-26
- [ ] **Cost model + slippage** : explicite + sensibilite
- [ ] **Capacity check** : capital cible compatible avec liquidite
- [ ] **Correlation portfolio** : delta Sharpe + delta MaxDD vs baseline
- [ ] **Budget capital + drawdown** approuve dans risk_registry.yaml
- [ ] **Paper run >= 30 jours** sans divergence > 2 sigma vs backtest
- [ ] **Reconciliation OK** sur duree paper
- [ ] **kill_criteria** definis (consec_losses, sharpe_min, dd_max)

## Evidence refs

- Backtest : scripts/research/backtest_gold_trend_sl_variants.py (V1 SL 0.4% TP 0.8%)
- WF/MC : TODO (cf scripts/research/wf_mc_*.py)
- Paper run : TODO (cf logs/portfolio/*.jsonl)
- Scorecard : TODO

## Risques identifies

1.
2.
3.

## Decision (committee)

- **Verdict** : APPROVE / REQUEST_REVISIONS / REJECT
- **Approved by** : Marc + (Claude / PO subagent)
- **Conditions** :
- **Re-review date** :

## Apres approval

- [ ] Edit `config/live_whitelist.yaml` : status -> live_probation
- [ ] Update `config/strategies_registry.yaml` : status -> live (si live)
- [ ] Commit + push + redeploy worker
- [ ] Monitoring 7 jours premier deploy avec sizing reduit (1/2)
