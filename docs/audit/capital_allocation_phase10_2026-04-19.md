# Audit Capital Allocation — Phase 10 XXL plan (2026-04-19)

## Current state (live whitelist 19 avril)

| Book              | Capital nominal | Live core | Paper only | Used capital |
|-------------------|-----------------|-----------|------------|--------------|
| ibkr_futures      | EUR 9.9K        | 2 strats  | 5 strats   | ~ EUR 1K (CAM + gold_oil_rotation, 5% risk_budget chacun) |
| binance_crypto    | $8.7K           | 0         | 13 strats  | ~ $0 actif (paper + earn passif) |
| ibkr_eu           | 0 (paper)       | 0         | 6 strats   | $0 |
| alpaca_us         | $100K paper     | 0         | 2 strats   | $0 (paper) |
| ibkr_fx           | disabled        | 0         | 0          | $0 |
| **Total**         | **EUR 18.6K**   | **2**     | **26**     | **~EUR 1K (5%)** |

**Capital occupancy reel: ~5% du capital deployable.**
(Memo previous: 13% — encore plus bas aujourd'hui post-demotes 16/18 avril.)

## Risk budget framework (decision PO 15 avril)

- Cap par strat futures live = 5% risk-if-stopped (pas par contract count)
- Sur EUR 9.9K IBKR: max EUR 500 risk per position
- 2 strats live_core actuellement -> max EUR 1K total risk-if-stopped
- Reste ~EUR 8.9K (90%) en cash IBKR + ~$8.7K Binance non utilise

## Findings

### Findings positifs
- Risk discipline RESPECTEE: tous les ordres futures live passent par
  risk_budget_5pct sizing (cf live_whitelist.yaml:59).
- DD limits explicites (limits_live.yaml): daily 5%, weekly 8%, monthly 12%,
  level_3 dd 6%.
- pre_order_guard 7 checks (cf Phase 5 audit).

### Findings negatifs
1. **Sous-utilisation capital**: ~95% du capital dort. Les benefices d'avoir
   2 brokers + EUR 18.6K sont nuls si seulement EUR 1K travaille.
2. **Faux diversification**: la "diversification" sur 28 strats ne se traduit
   pas en utilisation reelle (seulement 2 actives).
3. **Pas de framework occupancy target** explicite. Quelle est la cible ?
   30% ? 60% ? Pas documente.
4. **Decorrelation work** (memoire decorrelation_wp1_wp5) avait identifie
   5 candidats Tier 1 mais aucun n'a ete promote live a ce jour.

## Recommendations Phase 10 (manuelle, hors scope code)

1. **Definir occupancy target**: e.g. 30-50% capital travaille en live, le reste
   en reserve / earn passif.
2. **Promotion list 30j**: identifier 3-5 strats paper avec >= 30j data + WF
   solid -> candidats pour live_probation via promotion_gate (Phase 7).
3. **Cap test live promotion**: max 1 nouvelle strat par semaine pour controler
   risque correlate.
4. **Re-WF event-driven** des paper strats binance qui sont REJECTED (cf
   audit P0.2 18 avril) avant toute reactivation live.

## Score post-Phase 10

- Allocation framework defined: **9/10** (configs existent + risk_budget_5pct)
- Allocation execution discipline: **9/10** (live ordres respectent les caps)
- Capital occupancy: **3/10** (5% utilise, gap enorme)
- Diversification reelle: **3/10** (2 strats live, faux diversification)
- Promotion path automatique: **8/10** (promotion_gate Phase 7 livrable)

**Action operateur (hors code Phase 10):**
Decider du target occupancy + lancer les checks promotion_gate sur les
candidats paper >= 30j.
