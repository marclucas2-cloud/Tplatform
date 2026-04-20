# FEAT-CAM-TRAIL — Trailing SL conditionnel pour Cross-Asset Momentum

**Créé** : 2026-04-19 (session MCL gap géopolitique Iran)
**Owner** : Marc
**Priorité** : P2 (feature amélioration, pas hotfix)
**Status** : **CLOSED — KEEP H0** (backtest 2026-04-19, résultats ci-dessous)
**Follow-up** : H4 en backlog (priorité basse, critères go/no-go stricts)

## Contexte déclencheur

Dimanche 19 avril 2026, position CAM long MCL à +$295 unrealized. Weekend
whipsaw géopolitique (Iran referme Hormuz samedi, tanker attaqué, ceasefire
expire mercredi 22 avril) → gap up attendu dimanche 18h ET (+6 à +9% base).

Le TP CAM est hardcodé à `winner_close * 1.08` ([strategies_v2/futures/cross_asset_momentum.py:127](../../strategies_v2/futures/cross_asset_momentum.py#L127)).
Sur un gap qui dépasse +8%, la stratégie prend le TP et laisse l'alpha résiduel
sur la table. Un asset manager pro recalibrerait sur event non-modélisé.

Override manuel refusé par PO (governance, précédent dangereux). Solution
propre : intégrer un trailing SL conditionnel **dans la stratégie**, backtesté
et validé, pas patché un dimanche soir.

## Question à trancher

La stratégie CAM doit-elle embarquer un trailing SL qui :
- s'active **uniquement si** le TP fixe (+8%) est franchi par un gap ou un
  burst directionnel non-modélisé ?
- laisse courir tant que la structure momentum tient ?
- ou reste strictement one-shot TP/SL comme aujourd'hui ?

## Verdict (2026-04-19)

**KEEP H0** (baseline live CAM inchangée).

- **H1** = candidate de recherche, **pas** candidate de merge (DD meilleur mais Sharpe/CAGR inférieurs).
- **H2** = **écartée** (perd sur toutes les métriques sauf AvgPostTP, exit trop lent).
- **H4** (trailing conditionné à momentum fort) = backlog priorité basse.

### Décision PO/user

| Métrique prioritaire | Winner | Écart vs H0 |
|---|---|---|
| Sharpe (priorité 1) | H0 (1.33) | H1 –0.26, H2 –0.73 |
| CAGR (priorité 2)   | H0 (22.8%) | H1 –3.7pp, H2 –6.7pp |
| MaxDD (priorité 3)  | H1 (11.65%) | **H1 –6pp vs H0** |
| WF robustesse       | H1 (5/5) | H0 et H2 à 4/5 |
| AvgPostTP           | H2 (9.61%) | H1 8.61%, H0 7.95% |

Raisonnement : H0 garde l'avantage sur Sharpe + CAGR, les 2 métriques prioritaires
ici. H1 améliore le DD de 34% mais au prix de –20% de Sharpe et –16% de CAGR.
L'idée trailing **n'est pas morte** (H1 a un vrai signal : DD plus bas + WF 5/5),
mais elle n'a pas **encore** gagné. Le gain post-TP existant (+0.66pp par trade
TP-triggered) ne justifie pas l'override de la doctrine TP fixe actuelle.

### Résultats backtest complet (5.2Y, 2021-01-04 → 2026-03-30)

- Script : [scripts/bt_cam_trailing_compare.py](../../scripts/bt_cam_trailing_compare.py)
- Outputs : [tmp/backtest_cam_trailing/](../../tmp/backtest_cam_trailing/)
  - `REPORT.md`, `compare_summary.json`
  - `H0_trades.json` / `H1_trades.json` / `H2_trades.json`
- Univers : MES, MNQ, M2K, MGC, MCL (5 micros)
- Coûts : 5 bps round-trip (slip + fees IBKR micros)

| Variante | Trades | Sharpe | CAGR | MaxDD | WR | PF | AvgPostTP | WF prof. |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **H0** (baseline : TP fixe +8%) | 62 | **1.33** | **22.8%** | 17.64% | 54.8% | 2.35 | 7.95% | 4/5 |
| **H1** (ATR ×2 trail post-TP)   | 50 | 1.07 | 19.11% | **11.65%** | 44.0% | 2.18 | 8.61% | **5/5** |
| **H2** (chandelier ×3 close)    | 40 | 0.60 | 16.07% | 13.66% | 40.0% | 2.36 | 9.61% | 4/5 |

Exit reasons :
- **H0** : TP 18 / SL 28 / REBAL 16
- **H1** : TRAIL 21 / SL 28 / REBAL_NO_TP 1
- **H2** : TRAIL 12 / SL 22 / REBAL_NO_TP 2 / TIME 4

### H4 (backlog, priorité basse)

**Hypothèse** : activer le trailing uniquement sur trades avec
`momentum_at_entry > 5%` (vs 2% min actuel). Intuition : le trailing capture
l'alpha surtout sur trends forts où le momentum persiste au-delà de +8%.

**Critères stricts go/no-go pour H4** (ne pas relancer si l'un manque) :
1. H4 doit **améliorer Sharpe OU CAGR** vs H0 (pas seulement DD)
2. H4 doit **ne pas dégrader fortement DD/WF** (ex. DD worse than H0 + 3pp = STOP)
3. WF >= 50% fenêtres profitables obligatoire (règle projet)

**Quand relancer ?** : seulement si on décide activement de rouvrir la recherche
sur CAM trailing. Pas un item scheduled. Trigger possible : si on observe sur
live que plus de N trades/trimestre touchent le TP avec momentum_at_entry > 5%,
on peut vouloir tester.

## Hypothèses backtestées (archive)

### H1 — Trailing ATR post-TP
- Déclenchement : quand prix ≥ entry × 1.08 (TP atteint)
- Mécanique : remplacer TP fixe par trailing SL = max(entry × 1.08, high - 2×ATR(14))
- Exit : trailing touché OU signal de rebalance CAM (rebal_days atteint)

### H2 — Trailing chandelier (close-based)
- Déclenchement : idem H1
- Mécanique : trailing SL = max(entry × 1.08, plus_haut_close - 3×ATR(14))
- Exit : close sous trailing (pas intraday, évite whipsaw)

### H3 — Pas de trailing, accepter le manque à gagner
- Baseline : garder le TP fixe +8%
- Justification : si la 10Y backtest montre que trailing dégrade Sharpe/DD,
  le TP fixe est le bon choix par design

## Critères de validation (DoD)

- [ ] Backtest 10Y sur univers CAM actuel (MCL, MES, MGC, M2K, etc.)
- [ ] Métriques comparées H0 (baseline TP fixe) vs H1 vs H2 :
  - Sharpe ratio
  - Max drawdown
  - CAGR
  - Profit factor
  - Nombre de trades / winrate
  - **Gain moyen post-TP** (= ce qu'on laisse aujourd'hui sur la table)
- [ ] Walk-forward : ≥ 50% fenêtres OOS profitables (règle projet)
- [ ] Monte Carlo 10K sims : P(DD > -28.6%) documentée (baseline V2 portfolio)
- [ ] Si H1 ou H2 validé : merge dans `strategies_v2/futures/cross_asset_momentum.py`
- [ ] Si H0 reste le best : log décision dans `docs/research/dropped_hypotheses.md`,
  fermer ticket
- [ ] Tests unitaires ajoutés pour le trailing logic
- [ ] `config/quant_registry.yaml` bumped version si merge

## Non-objectifs

- **Pas** un trailing SL pour toutes les strats futures (scope CAM seulement)
- **Pas** un override manuel ad-hoc (c'était la demande initiale, refusée PO)
- **Pas** un hedge event-driven séparé (voir ticket POL-EVENT-FLAG pour policy event)

## Fichiers touchés (prévisionnel)

- `strategies_v2/futures/cross_asset_momentum.py` (L122-129 : Signal dataclass → ajouter `trailing_config`)
- `core/execution/position_state_machine.py` (si trailing géré côté PSM)
- `core/broker/ibkr_bracket.py` (si trailing natif IBKR préféré à in-strategy)
- `tests/strategies_v2/futures/test_cross_asset_momentum.py`

## Décision architecturale à prendre

**Où le trailing vit-il ?**
- Option A : dans la stratégie (Signal étend avec `trailing_cfg`)
- Option B : dans la PSM (position state machine gère le trailing post-fill)
- Option C : bracket natif IBKR (trail amount en ticks)

Recommandation à challenger : Option B, car la PSM voit déjà les fills et gère
les transitions OPEN → CLOSED. Découple la logique stratégie (alpha) de la logique
exécution (trailing). Mais Option A reste valable si on veut que la stratégie
reste auto-contenue et testable isolément.

## Risques

- **Overfitting** : trailing calibré sur 2024-2026 peut dégrader 2014-2020
- **Complexité** : trailing ajoute un état qui peut désynchroniser strat ↔ broker
  en cas de reboot worker (cf. [feedback_baselines_persistence_bug.md](../../C:/Users/barqu/.claude/projects/c--Users-barqu-trading-platform/memory/feedback_baselines_persistence_bug.md))
- **Fausse bonne idée** : le test MCL weekend 18-19 avril est **1 échantillon**.
  Ne pas conclure sur N=1.

## Références

- Position déclenchante : CAM long MCL +$295 unrealized, 19 avril 2026
- PO verdict : NO-GO override manuel, ticket backlog obligatoire
- Doctrine : [project_fast_track_doctrine.md](../../C:/Users/barqu/.claude/projects/c--Users-barqu-trading-platform/memory/project_fast_track_doctrine.md)
- Baseline CAM : [project_bear_capable_strats.md](../../C:/Users/barqu/.claude/projects/c--Users-barqu-trading-platform/memory/project_bear_capable_strats.md)
