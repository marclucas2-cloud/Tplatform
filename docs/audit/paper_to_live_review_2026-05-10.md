# Paper → Live Review — 2026-05-10

Source : [config/quant_registry.yaml](../../config/quant_registry.yaml) (canonical),
state files paper, WF manifests `data/research/wf_manifests/`.

Doctrine appliquee (memoire `feedback_prove_profitability_first` +
`project_fast_track_doctrine` 2026-04-19) :
- **Strict A/S only** pour live_fast_track_probation
- **Min 30j paper** sans divergence > 1-2 sigma vs backtest (grade-dependent)
- **Runtime cable** (signal-emitting) verifie sur logs/journal
- **Capital + infra blockers** zero
- **Marc decide**, ce document propose

Date : 2026-05-10. Paper_start + 30j = earliest_live.

---

## Tableau de synthese — 13 paper / 3 frozen / 1 research

| # | Strategy | Broker/Asset | Grade | WF | Paper start | J+30 | Runtime cable ? | Trades observes | Verdict |
|---|----------|--------------|-------|----|-----------:|-----:|------|---:|---------|
| 1 | **mes_mr_vix_spike** | IBKR fut MES | **A** | 5/5 | 2026-04-23 | **2026-05-23** | OUI (journal `no_signal` quotidien) | 0 | **CANDIDATE** — promote 23 mai si pas de signal divergent. Runtime DEJA cable contrairement au registry. |
| 2 | **mes_estx50_divergence** | IBKR fut MES | **A** | 5/5 | 2026-04-23 | **2026-05-23** | NON (registry: `runner_wiring_pending_marc_decision`) | 0 | **WAIT** — wiring requis avant. Si tu cable + 30j paper a partir d'aujourd'hui, earliest = 2026-06-10. |
| 3 | **pead_long_only_v1** | Alpaca US | **A** | 5/5 | 2026-04-30 | 2026-05-30 | OUI (cycle 22h30 Paris) | 0 (state vide) | **BLOCKER ALPACA PDT** — paper US OK mais live US bloque jusqu'a depot $25K Alpaca. Doctrine dit ALPACA paper-only tant que PDT non finance. |
| 4 | mes_monday_long_oc | IBKR fut MES | B | 3/5 | 2026-04-16 | 2026-05-16 | ? (pas de state file) | ? | **WAIT** — Grade B + WF 3/5 + MC P(DD>30%) 9.8% = doctrine A/S strict NO. Reconsiderer si cohorte stable apres 60j. |
| 5 | mes_wednesday_long_oc | IBKR fut MES | B | 4/5 | 2026-04-16 | 2026-05-16 | ? | ? | **REJECT POUR LIVE** — MC P(DD>30%) **28.3%** "tres limite" (registry note). Pas live fast-track. |
| 6 | mcl_overnight_mon_trend10 | IBKR fut MCL | B | 4/5 | 2026-04-18 | 2026-05-18 | ? | ? | **BLOCKER INFRA** — `data_stale_mcl_1d_parquet` + `friday_trigger_re_wf_requis`. Pas live tant que data fix + re-WF. |
| 7 | mgc_mes_ratio_rotation | IBKR fut MGC/MES | B | 4/5 | 2026-04-23 | 2026-05-23 | NON (`runner_wiring_pending_marc_decision`) | 0 | **WAIT** — Grade B + DD -32% surveillance. Decorrelation parfaite (corr CAM/GOR ~0). Utile si desk veut diversifier mais pas urgent. |
| 8 | gold_q4_seasonality | IBKR fut MGC | B | 4/5 11Y | 2026-04-23 | n/a | NON (`low_cadence_1_trade_per_year`) | 0 | **NEVER LIVE-PROBATION** — 1 trade/an, cadence trop rare pour apprentissage paper. Reference historique seulement. |
| 9 | gold_trend_mgc | IBKR fut MGC | **REJECTED** | 0/5 (re-WF 2026-04-26) | 2026-04-16 | n/a | OUI mais... | 0 utile | **DO NOT PROMOTE** — Re-WF 2026-04-26 : Sharpe -0.03, MC P(DD>30%) 92.5%. Memoire fast-track obsolete (cite gold_trend V1 mais c'etait avant le re-WF). |
| 10 | alt_rel_strength_14_60_7 | Binance crypto | B | 3/5 | 2026-04-18 | 2026-05-18 | OUI (journal quotidien rebal hebdo) | 4 cycles, **cumul -$88** | **DO NOT PROMOTE** — Doctrine B exige WF >= 4/5 ou metrics paper > seuil. Paper 22j en negatif. Continuer paper, reconsiderer 30 mai si retour positif. |
| 11 | macro_top1_rotation | Alpaca US | B | 4/5 | 2026-04-24 | 2026-05-24 | OUI (cycle 16h30 Paris) | 1 rebal 24/04 (DBC) puis hold | **BLOCKER ALPACA PDT** + simulation locale (`paper_simulation_locale_pas_d_ordre_broker_reel`). |
| 12 | btc_asia_q80_long_only | Binance crypto | B | (already live_micro) | live since 2026-04-23 | n/a | OUI (cycle 10h30 Paris) | 17j live, **0 entry** signaux NONE quotidiens | **DEJA LIVE_MICRO** depuis 2026-04-23 mais 0 trade reel observe. Considerer revoir le seuil signal_thr ou laisser tourner pour valider non-trade comme regime defensive (pas signal = pas entree = OK). |
| 13 | low_vol_long_only_bottom5 | Alpaca US | B_decay | 5/5 mais Sharpe declinant | research | n/a | NON | n/a | **NE PAS PROMOUVOIR** — alpha decay observable W1 1.07 -> W5 0.31. A retester avec lookback alternatif. |
| F1 | mes_pre_holiday_long | IBKR fut MES | B | 5/5 perfect | FROZEN 2026-04-22 | n/a | n/a | n/a | **RE-ACTIVABLE** mais cadence 8-10 trades/an = pas 10 trades en 30j possible. Live = anomalie rare a deboucher. Garder frozen sauf si Marc veut overlay calendrier. |
| F2 | eu_relmom_40_3 | IBKR EU | B | 4/5 | FROZEN 2026-04-22 | n/a | n/a | n/a | **RE-ACTIVABLE** si re-WF long-only valide (shorts EU CFD/futures mini sans plan). Pas immediat. |
| F3 | **mib_estx50_spread** | IBKR EU | **S** | 4/5 | UNFROZEN 2026-05-10 (paper_only) | n/a | OUI (run_mib_estx50_spread_paper_cycle 17h45 Paris) | n/a | **UNFROZEN 2026-05-10** — capital $29K USD ~= 27K EUR > 13.5K EUR margin requis. Le "gap funding 3.6K" 2026-04-22 etait base sur equity pre-depot du 2026-04-30 (+$15K). Reprend paper lundi 17h45 Paris, J+30 = 2026-05-18 (paper_start_at 2026-04-18). |

---

## Top picks pour live (par ordre de priorite)

### 1. mes_mr_vix_spike — promote 2026-05-23 (J+30, dans 13 jours)

- **Pourquoi** : Grade **A** strict, WF 5/5 parfait sur 5Y MES + VIX, runtime DEJA cable
  (journal mes_mr_vix_spike/journal.jsonl montre des `no_signal` quotidiens
  depuis 2026-04-27, donc le cycle tourne). Sharpe backtest 0.72, DD -9.7%,
  12 trades/an. **Decorrelation quasi-parfaite** : corr CAM 0.055, GOR -0.014.
  Diversification reelle vs desk actuel.
- **Cap** : sizing fixed_1_contract MES, max_risk_usd 250 (1 SL = $125 valeur tick).
  Si position simultanee CAM + GOR + mes_mr_vix : risk-if-stopped total ~5%+5%+1%
  capital = 11% capital live. Sous le cap futures portfolio VaR 8% APRES correlation
  (commit `79e8c62 feat(risk): raise futures portfolio VaR cap from 5% to 8%`).
  Capital live IBKR $29K = OK.
- **Action si OK** :
  1. Verifier signal_emitting depuis 30+ jours (fait : journal 2026-04-27 -> 2026-05-08 = 11 entrees).
  2. Aucun trade reel paper, donc pas de divergence vs backtest a mesurer en live PnL.
     Verifier en revanche que le seuil consec=3 + vix>15 a bien declenche le bon
     nombre de signaux historiques sur la fenetre paper (faire un audit comparatif
     paper_signals vs backtest_signals avant le 23 mai).
  3. 23 mai : flip status `paper_only` -> `live_probation` dans live_whitelist.yaml +
     quant_registry.yaml. Sizing live_micro grade A = $300 risk cap.
  4. Watch 30j live : kill criteria DD -12% / Sharpe rolling 60d < -0.5.

### 2. mib_estx50_spread — UNFROZEN 2026-05-10 (correction Marc)

- **Pourquoi** : Grade **S** (le seul S du registre). +EUR 22.6K /
  12 trades / 24 mois OOS. WF 4/5. Source de truth :
  `reports/research/wf_mib_estx50_corrected.json` (2026-04-18, apres fix de 4
  bugs vs original Sharpe 14.35 buggy). Notional dollar-neutral spread (1 FIB +
  ~3 FESX).
- **Correction 2026-05-10** : le "blocker funding EUR 3.6K" etait FAUX —
  base sur equity IBKR pre-depot du 2026-04-30 (+$15K). Aujourd'hui $29K USD
  ~= 27K EUR > 13.5K EUR margin requis. Marc fait remarquer que c'etait deja
  funded depuis le 30 avril.
- **Action FAITE** : status frozen -> paper_only dans
  [config/quant_registry.yaml](../../config/quant_registry.yaml) +
  [config/live_whitelist.yaml](../../config/live_whitelist.yaml). Le runtime
  `run_mib_estx50_spread_paper_cycle` reprend lundi 17h45 Paris (mtime cache
  invalidation, pas de restart worker).
- **Earliest live** : paper_start_at 2026-04-18 + 30j = **2026-05-18** (mais
  effectif paper data = 8j seulement entre re-activation et J+30, donc plutot
  attendre **2026-06-09** = unfrozen + 30j paper observe).

### 3. mes_estx50_divergence — promote 2026-06-10 (si tu cables runtime aujourd'hui)

- **Pourquoi** : Grade **A** strict, WF 5/5 parfait, corr CAM -0.005 / GOR -0.102
  (anti-correlee, encore mieux que mes_mr_vix). Sharpe 0.95, DD -10.4%.
- **Blocker** : `runner_wiring_pending_marc_decision` (registry). Le runtime
  n'execute pas le cycle paper. Donc le compteur 30j ne tourne pas vraiment.
- **Action** : decider ce week-end si on cable le runner. Si oui : 30j paper
  a partir du wiring date, earliest live = wiring_date + 30j.

---

## Strats a ne PAS promouvoir (justifie)

- **gold_trend_mgc** : RE-WF 2026-04-26 verdict REJECTED (Sharpe -0.03, P(DD>30%)
  92.5%). Memoire fast-track obsolete sur cette strat.
- **alt_rel_strength** : -$88 cumul sur 22j paper, en divergence avec
  backtest (Sharpe +1.11 expected). Continuer paper, reconsiderer si retour.
- **mes_wednesday_long_oc** : MC P(DD>30%) 28.3% trop eleve. Doctrine non.
- **mes_monday_long_oc** : WF 3/5 sous-critique grade B + doctrine A/S strict.
- **mcl_overnight_mon_trend10** : data freshness blocker + re-WF Friday trigger
  requis (registry note).
- **mgc_mes_ratio_rotation** : DD -32% surveillance + grade B + non cable runtime.
- **gold_q4_seasonality** : 1 trade/an, jamais promotion live possible.
- **us_sector_ls_40_5** : REJECTED 2026-04-30 (re-WF ETF SPDR : 18/18 configs
  Sharpe negatif).
- **low_vol_long_only_bottom5** : alpha decay observe.
- **macro_top1_rotation, pead_long_only_v1** : Alpaca PDT bloque ($25K
  depot non fait), simulation locale pas ordres reels.

---

## Statut LIVE actuel (rappel)

| Strat | Status | Live since | Trades observes | Risk if stopped |
|-------|--------|-----------|----:|-------|
| cross_asset_momentum | live_core | 2026-04-07 | Plusieurs (CAM MNQ ferme manuellement 2026-05-08) | 5% (~$1,275) |
| gold_oil_rotation | live_core | 2026-04-08 | Signal dormant | 5% (~$1,275) |
| btc_asia_q80_long_only | live_micro | 2026-04-23 | **0** (17j sans entry) | $20 (notional $200) |

Total risk-if-stopped CAM + GOR = ~10% capital live ($29K), sous cap futures 8%
APRES decorrelation. Ajouter mes_mr_vix +1% = ~11% brut, decorrele = OK.

---

## Recommandations finales pour Marc — STATUT POST-CORRECTION 2026-05-10

1. ✅ **mes_estx50_divergence runtime cable** (FAIT 2026-05-10) — paper_start
   re-anchored a 2026-05-10, earliest live_micro = **2026-06-09**.
2. ✅ **mib_estx50_spread UNFROZEN** (FAIT 2026-05-10) — correction du faux
   blocker funding (le gap 3.6K etait base sur equity pre-depot 30 avr).
   Paper reprend lundi 17h45 Paris. Earliest live_probation **2026-05-18**
   (J+30 paper_start original) ou **2026-06-09** (J+30 effectif post-unfreeze
   pour observation propre).
3. **2026-05-23** : flip mes_mr_vix_spike paper_only -> live_probation
   (si pas de divergence detectee dans audit signaux). Ajout 3eme strat
   futures live diversifiee.
4. **Surveillance** : btc_asia_q80 live_micro depuis 17j sans 1 entry.
   Soit le seuil signal est trop strict (regime defensif voulu = OK), soit
   reviser. Pas critique tant que pas de pertes.

---
Genere par checkup 2026-05-10. Tableau base sur quant_registry.yaml + state files VPS.
