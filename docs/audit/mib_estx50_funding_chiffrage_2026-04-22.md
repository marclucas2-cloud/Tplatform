# Chiffrage mib_estx50_spread — decision funding EUR
**Date** : 2026-04-22
**Demande Marc** : "chiffre" le cout/edge du funding EUR pour debloquer mib_estx50_spread
**Decision attendue** : GO / NO_GO depot €3,600 EUR additionnel IBKR

---

## TL;DR en 3 lignes

**NO_GO court terme**, malgre grade S backtest et Sharpe 3.91 headline. Une seule periode WF de 5 mois (window 4, mai-oct 2025) a perdu €8,437 avec MaxDD €14,395 > totalite du capital EUR dispo. Attendre 30j PnL live positif sur CAM/GOR avant d'engager €3,600 supplementaires sur une strat ou un seul mauvais trimestre peut brûler tout le book EUR.

---

## 1. Capital

| Poste | Montant EUR | Montant USD (1.07) | Source |
|---|---|---|---|
| Margin requis (mib + estx50 spread, notional dollar-neutral) | €13,500 | ~$14,450 | `quant_registry infra_gaps` |
| EUR dispo IBKR actuellement | €9,900 | ~$10,600 | audit H10 T10 |
| **Gap a funder** | **€3,600** | **~$3,850** | calcul direct |
| Option : reallocation USD → EUR (conversion FX) | 3,850 | -3,850 (perdu USD) | — |
| Option : depot external EUR → IBKR | +3,600 | 0 (neutre USD) | virement bancaire |

---

## 2. Edge attendu (WF 24mo OOS, 5 windows)

Source : `reports/research/wf_mib_estx50_corrected.json` (apres fix Sharpe MtM + hedge ratio notional + slippage 0.5 ticks/leg).

| Window | Periode | PnL EUR | Sharpe | WR | MaxDD EUR | Trades |
|---|---|---|---|---|---|---|
| 1 | 2024-02 → 2024-07 | +5,627 | 2.31 | 66.7% | -2,032 | 3 |
| 2 | 2024-07 → 2024-12 | +12,225 | 4.59 | 100% | -3,339 | 3 |
| 3 | 2024-12 → 2025-05 | +1,548 | 0.42 | 50% | -7,794 | 2 |
| **4** | **2025-05 → 2025-10** | **-8,437** | **-1.63** | **33.3%** | **-14,395** | **3** |
| 5 | 2025-10 → 2026-03 | +11,675 | 13.87 | 100% | -790 | 1 |
| **Total** | **24 mois** | **+22,638** | **avg 3.91** | — | — | **12** |

### Observations critiques

- **Headline Sharpe 3.91** = moyenne des windows. Trompeur : **window 4 a un Sharpe -1.63** (losing period ~5 mois).
- **MaxDD window 4 (€14,395) > total EUR disponible (€13,500 margin)** → margin call quasi certain si reproduction de cette periode.
- **WF ratio 4/5** : borderline (seuil promotion 50% minimum, reel = 80%).
- **n_trades = 12 sur 24 mois** = 0.5 trade/mois = low frequency, variance elevee sample-to-sample.

### ROC annualise (EUR)

| Scenario | PnL EUR/an | ROC / margin (€13,500) | USD equiv | ROC / $20K portfolio |
|---|---|---|---|---|
| **Backtest brut 24mo** | +11,319 | +83.8% | +$12,100 | +60.5% |
| Haircut -50% (realiste post-fix) | +5,660 | +42% | +$6,060 | +30% |
| Haircut -75% (pessimiste, window 4 weight) | +2,830 | +21% | +$3,030 | +15% |
| Worst case window 4 replay 5 mois | -8,437 | -62% | -$9,027 | -45% |

---

## 3. Cout d'opportunite des €3,600

Alternatives au funding mib_estx50 :

| Alternative | Rendement attendu | Liquidite | Risque |
|---|---|---|---|
| Garder en USD MMF IBKR (4.5% actuel) | +$174/an (+4.5%) | T+0 | 0 |
| Risk budget CAM/GOR +5% = $1,000 marge supplementaire | +$100-150/an (haircut) | T+0 | borne DD CAM/GOR |
| Buffer scale Binance si crypto paper passe gate 30j | 0 a court terme, +$500/an si promo | T+0 | dependance validation paper |
| **Funding mib_estx50 (haircut -50%)** | **+$6,060/an theorique** | **margin bloquee** | **-$9,027 window 4 replay** |

**Ratio edge/risk** : +$6,060 upside / -$9,027 downside worst case = 0.67. **< 1 = asymetrie defavorable** sur le scenario pessimiste.

---

## 4. Conditions minimales pour GO

Liste exigeante, pas negociable sans re-chiffrage :

1. **CAM + GOR live PnL net > 0 sur 30j minimum** (au 2026-05-07 rebal CAM), preuve que l'infra execution live est robuste.
2. **Aucun incident P0/P1 ouvert pendant 14j avant funding** (gate runtime_audit clean).
3. **Paper mib_estx50_spread ≥ 60j sans divergence > 1 sigma vs backtest** (actuellement 4j, besoin ~56j supplementaires → earliest 2026-06-18).
4. **Re-WF mib_estx50 sur periode incluant Q4 2025 (la periode losing)** pour verifier que les fixes (Sharpe MtM, hedge ratio notional, slippage) tiennent sur marche adverse.
5. **Confirmation margin €13,500 par test preflight IBKR reel** (pas juste estimation registry).
6. **Plan kill explicite** : si DD > €4,500 (33% de margin) en 30j, unwind systematique.

---

## 5. Recommandation operationnelle

| Horizon | Decision | Rationale |
|---|---|---|
| **Aujourd'hui 2026-04-22** | **NO_GO funding** | 4j paper, 0 preuve live CAM/GOR, window 4 replay = margin call |
| 2026-05-07 (post rebal CAM + 30j live) | Re-evaluer si CAM/GOR PnL live > 0 | Point de decision #1 |
| 2026-06-18 (60j paper mib_estx50) | GO conditionnel si tous les 6 criteres §4 cleared | Point de decision #2 |
| Q3 2026 (post 90j live multi-sleeves) | GO definitif si ROC live demontre > 5% annualise | Point de decision final |

---

## 6. Si GO eventuel : sizing prudent

Plutot que d'engager le full €13,500 margin immediatement :

- **Phase 1 (funding €3,600 → margin €13,500 dispo mais utilise qu'a 30%)** : 1 seule position spread, size = margin €4,500 (soit 33% du budget), kill si DD > €1,500.
- **Phase 2 (apres 3 mois phase 1 OK)** : scale a margin €9,000 (66%).
- **Phase 3 (apres 6 mois total live OK)** : full €13,500 margin.

Cette approche reduit le downside worst case de -$9,027 a -$3,000 sur phase 1, transformant le ratio edge/risk en 2x au lieu de 0.67.

---

## 7. Conclusion chiffree

**Cout immediat** : €3,600 (~$3,850) + margin EUR 13,500 bloquee
**Upside realiste** : +$3,000-6,000/an (scenario conservative a realiste)
**Downside realiste** : -$9,000 sur 5 mois (window 4 replay)
**Break-even time** : 2-3 ans de performance haircut-50% pour amortir 1 seul window 4
**Verdict** : **NO_GO maintenant**. Attendre 30j preuve live CAM/GOR + 60j paper mib_estx50, puis sizing prudent.

**Prochaine revue** : 2026-05-07 apres rebal CAM (si le 1er TP live confirme l'edge brut). Avant cette date, le €3,600 reste plus utile comme reserve USD MMF que funding EU.

---

**Rapport genere par chiffrage mib_estx50 — 2026-04-22**
**Auteur** : analyse demandee par Marc post-audit ChatGPT 2026-04-21
