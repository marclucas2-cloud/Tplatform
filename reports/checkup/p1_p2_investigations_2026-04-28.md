# P1 + P2 Investigations — 2026-04-28

## P1 #1 — Bracket OCA_MCL disparition

### Findings

Le bracket watchdog logue **OK MCL all protected en CONTINU** :
- 07:08 UTC ✅
- 08:21 UTC ✅
- ... continu ...
- 16:44 UTC ✅
- 17:01 UTC ✅
- 17:18 UTC ✅
- **17:30:13 UTC ✅** (dernière trace bracket OK avant mon investigation 17:30)
- **17:36 UTC** : `core.risk.emergency_close_all: IBKR permanently down apres 5 tentatives sur 127.0.0.1:4002`
- **17:41:58 UTC ✅** (bracket re-vu OK après IBGW back)
- 17:45:47 UTC : ma SELL clôture la position

### Conclusion

**Le bracket n'a JAMAIS disparu côté broker.** Mon investigation à 17:30 UTC avec `clientId=205` retournait "0 open trades" parce que :
- soit IBGW était en pré-restart (entre 17:30 et 17:36 il y a eu un disconnect "permanently down")
- soit ma session clientId temporaire ne voyait pas les orders placés par d'autres clientIds (worker = 70-79, 78, 313, etc.)

**Faux positif de ma part.** La position MCL était protégée tout du long. La fermeture +$84 net était une sortie technique en gain, pas un évitement de risque réel.

### Implications

- Pas de bug runtime. Bracket système OK.
- Bug de visibilité de mon investigation : `ib.openTrades()` filtre par clientId/session, donc une nouvelle session ne voit pas les orders placés en sessions antérieures **par d'autres clientIds**. Note pour futurs audits : utiliser `ib.reqAllOpenOrders()` ou cross-check via `reqExecutions` + position filter.

## P1 #2 — State desync paper MNQ/MES "position gone" 2 min après fill

### Findings

Cycle 27/04 14:00 UTC :
- 14:00:35 : BUY MNQ paper fillé @ 27,399.75 OCA_MNQ_bada8a52
- 14:00:53 : BUY MES paper fillé @ 7,195.00 OCA_MES_b08604f0
- 14:02:48 : `FUTURES SL CHECK: MNQ position gone — removing from state`
- 14:02:48 : `FUTURES SL CHECK: MES position gone — removing from state`

### Cause racine probable

Le cycle FUTURES SL CHECK probablement tourne sur **port LIVE 4002 (canonical U25023333)** mais les fills paper sont sur **port 4003 (DUP573894)**. Le SL CHECK ne voit pas les positions paper canoniquement → marque "gone" → remove from state.

C'est un **bug d'architecture paper vs live** : le SL CHECK n'est pas paper-aware.

### Sévérité

- **Pas de risque PnL réel** : positions paper, fictives.
- **Mais comptabilité paper broken** : on perd la trace des fills paper, donc plus de mesure de PnL paper sur les sleeves CAM/mes_monday/etc.
- Impact direct : les sleeves paper futures **ne peuvent pas accumuler de track record** observable tant que ce bug n'est pas fixé.

### Fix recommandé (hors scope mission, à programmer)

- Faire que `FUTURES SL CHECK` requete IBKR sur **les deux ports** (4002 live + 4003 paper) selon le mode de la position dans le state file
- Ou alternative : lire le state file et **ne pas remove from state si la position est marquée mode=PAPER** et qu'on ne tourne que sur 4002

## P1 #3 — Origine perte crypto / déclenchement kill switch live

### Findings

Live risk cycle 24/04 fenêtre 14:05-14:32 UTC :
| Time | Equity IBKR | Daily PnL |
|---|---|---|
| 14:05 | $11,270 | -0.14% |
| 14:10 | $11,257 | -0.26% |
| 14:16 | $11,278 | -0.07% |
| 14:21 | $11,274 | -0.10% |
| 14:26 | $11,281 | -0.04% |

À 14:49 UTC : `KILL SWITCH TRIGGERED: Daily loss -11.39% exceeds -5.0%`.

Equity Binance pendant la même fenêtre : oscille entre $10,727 et $10,775 (variation max -0.45%, jamais proche -11%).

### Conclusion

**Faux positif kill switch.** Aucune mesure observable ne montre -11.39% de perte réelle :
- IBKR daily_pnl entre -0.04% et -0.26% jusqu'à 14:32 UTC
- Binance total entre $10,727 et $10,775 (var ±0.5%)
- Combined NAV $21,280 stable

### Hypothèse cause racine

Entre 14:32 et 14:49 UTC (17 min), un calcul de daily_pnl a explosé. Causes plausibles (à confirmer en lisant le code `core/kill_switch_live.py:171`) :
- Baseline daily_anchor recalculé sur un mauvais snapshot (NAV combiné multi-broker mal synchro)
- Conversion devise EUR↔USD erronée (IBKR retourne en EUR, code attend USD ou inverse)
- Mark-to-market `volatile_earn` Binance recalculé avec un nouveau ratio
- Inclusion d'une position fantôme (DUP573894) dans le calcul DD

### Implications

- **Kill switch live actif depuis 3.5 jours sur faux positif.**
- CAM/GOR LIVE bloqués pour rien.
- Le déclenchement crypto LEVEL 3 emergency samedi est probablement aussi conséquence (cascade depuis le kill switch live).

### Fix recommandé (hors scope mission, à programmer)

- Logger explicitement les composants du daily_pnl à chaque check (NAV, baseline, currency rate)
- Ajouter un sanity check sur le delta de daily_pnl entre 2 ticks consécutifs (si delta > 5% en 1 tick, throw warning au lieu de trigger)

## Décision reset kill switch

**Recommandation : RESET maintenant** — le déclenchement était un faux positif, aucune perte réelle, pas de risque à débloquer.

**Pré-requis avant reset** :
1. Vérifier que la cause racine du calcul -11.39% est identifiée et reproductible (pas un bug aléatoire qui peut re-déclencher demain)
2. Re-vérifier que U25023333 est flat (déjà fait, +$84 net)
3. Décider si on garde GOR/CAM LIVE actifs ou on les laisse en standby le temps du fix

**Sans le fix racine du faux positif, un reset peut juste re-trigger demain à un autre moment.** Donc ma vraie reco : creuser le code `core/kill_switch_live.py` pour identifier le bug avant reset. Ce n'est pas urgent (live freeze a coût d'opportunité limité, on a perdu probablement quelques rotations CAM 48h sur 4 jours = max ~$50-100 PnL paper).

## P2 #1 — CAM CL=F vs MCLZ6

Déjà documenté dans :
- `config/quant_registry.yaml` : `infra_gaps: ["front_month_proxy_vs_deferred_contract_mcl"]`
- `config/live_whitelist.yaml` notes CAM : "Remaining analytical debt: front-month CL=F proxy vs deferred MCL contract"
- Rapport audit MCL Z6 vs CL=F (dimanche 26/04) : ratio 2.5-3× sur vol, base CL=F-MCLZ6 mean $22.12 std $8.74

**État de la dette** : identifiée et acceptée comme limitation connue. CAM grade B 48h reflète déjà cette limitation. Pas de fix urgent.

**Si on veut traiter** : remplacer le ticker yfinance `CL=F` par un ticker continuous deferred (probablement nécessite recalibration full du momentum CAM, donc nouveau re-WF). Mini-mission ~1 jour, à programmer après reset kill switch.

## P2 #2 — Modifs trailing stop BacktesterV2 ChatGPT

### Etat

Worktree non-tracké local :
- `core/backtester_v2/engine.py` : +229/-29 lignes
- `core/backtester_v2/engine_helpers.py` : +11 lignes
- `core/backtester_v2/types.py` : +2 lignes (probablement nouveau attribut `trailing_stop_pct` sur Signal)
- `tests/test_backtester_v2_trailing_stop.py` : nouveau test 3 cas, **3/3 pass**

### Évaluation

- Feature ajoutée : trailing stop sur backtester V2
- Scope : **backtester only**, pas runtime live → faible risque PnL
- Mais 258 lignes engine.py = non trivial. Reviewer le diff complet recommandé avant commit.
- Tests 3/3 pass = code fonctionne sur les cas testés

### Reco

**Pas commit unilatéral de ma part** — Marc doit décider. Options :
- **A. Commit tel quel** si tu fais confiance au scope research-only de ChatGPT
- **B. Code review du diff engine.py** (258 lignes) avant commit
- **C. Laisser tel quel** dans le worktree non-tracké en attendant un usage concret du trailing stop par une stratégie qui veut l'utiliser

## Synthèse exécutable

| P# | Sujet | Statut | Action recommandée |
|---|---|---|---|
| P1 #1 | Bracket disparu | ✅ Faux positif | Aucune action runtime |
| P1 #2 | State desync paper | ⚠️ Bug réel | Fix architecture SL CHECK paper-aware |
| P1 #3 | Kill switch -11.39% | ⚠️ Faux positif | Identifier cause racine calcul, puis reset |
| P2 #1 | CAM CL=F vs MCLZ6 | 📄 Documenté | Mini-mission après reset kill switch |
| P2 #2 | Trailing stop BT_v2 | ⚠️ 258 lignes worktree | Décision Marc : commit, review, ou laisser |

**Reset kill switch** : ma reco = creuser cause racine `core/kill_switch_live.py` avant reset, sinon risque de re-trigger demain. ~30 min de code review.

**Pas d'expansion / câblage** tant que kill switch toujours actif.
