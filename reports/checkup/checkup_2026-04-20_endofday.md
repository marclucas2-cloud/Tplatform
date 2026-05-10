# CHECKUP 24h end-of-day — 2026-04-20 (lundi) fin de journée + nuit 20→21

**Période** : lundi 2026-04-20 05:30 UTC (fin du checkup matinal) → mardi 2026-04-21 05:30 UTC.
**Complément** : [checkup_2026-04-20.md](checkup_2026-04-20.md) (matinal, couvre le TP MCL dimanche soir).
**Verdict** : 🟢 **OK** — 0 nouveau trade live, 5 bugs ops détectés, 0 incident P0/P1.

---

## Delta equity vs checkup matinal

| Broker | Equity matin 20/04 05:30 UTC | Equity 21/04 05:30 UTC | Delta jour lundi |
|---|---|---|---|
| IBKR live U25023333 | $11,277.37 | **$11,274.93** | **-$1.90** (flat) |
| Binance live | $10,496 (spot 2k + earn 7.7k + margin 759) | **$9,864.65** (spot 2k + earn 7.86k, **margin 0**) | **-$631** (margin 759 clôturée, reste flat) |
| Alpaca paper | $99,495.42 | **$99,380.22** | -$115.20 (unrealized) |
| **Total live** | $21,773 | **$21,139.58** | **-$633** (dominé par margin Binance clôturée) |

**Diagnostic Binance margin -$759 → 0** : le flag `margin_borrowed_btc: 0.0` et `margin_level: 999.0` dans le state file actuel signale que la position margin (si elle existait) est clôturée. À **vérifier** : était-ce un résidu du bucket A drain (2026-04-19) qui a mis 2j à se dénouer ? Ou un trade discret ? Les logs Binance 24h montrent `crypto cycle: 0 trade` sur tous les cycles → pas d'order exécuté côté worker. **À investiguer** : `ssh vps "grep -iE 'margin.*repay|margin.*borrow|redeem' logs/worker/worker.log | tail -20"`.

---

## Positions nouvelles détectées (non dans checkup matinal)

### IBKR paper DUP573894 — position MES short -1
Détectée à 14:00:08 UTC 2026-04-20 par le cycle paper futures :
```
Position(account='DUP573894', symbol='MES', position=-1.0, avgCost=35723.13, marketPrice=7155.00, unrealizedPNL=-51.87)
```
- `DUP573894` = **compte paper IBKR** (port 4003 / paper gateway), **≠ live U25023333**.
- avgCost $35,723.13 / multiplier 5 → entry price ~$7,144.63 sur MES.
- **Origine** : probablement une position paper résiduelle non nettoyée (legacy avant drain). Pas un trade live.

### Impact ops : bug paper runner
Cette position paper DUP573894 est détectée par le paper runner futures et **bloque 7 strats MES paper** à 14:00:12 UTC :

| Strat paper | Signal | Action |
|---|---|---|
| MES Trend | SELL @ 6833.25 | SKIP — IBKR real position exists |
| MES 3-Day Stretch | SELL @ 6833.25 | SKIP |
| mes_wednesday_long_oc | BUY @ 6833.25 | SKIP |
| Overnight MES V2 (SL60/TP120/EMA50) | BUY @ 6833.25 | SKIP |
| TSMOM MES | SELL @ 6833.25 | SKIP |
| Multi-TF Mom MES | BUY @ 6833.25 | SKIP |
| RS MES/MNQ rotate | BUY MNQ | SKIP (MNQ aussi) |

**Le paper runner ne distingue pas le compte paper DUP573894 du compte live U25023333.** Bug P2 sérieux car il **fausse le paper validation** de 7 strats (on croit qu'elles ne tirent pas signal alors qu'elles en tiraient 7 sur 7 lundi à l'ouverture US).

### Alpaca paper — 11 positions divergentes

Détectées par reconciliation dès 22:42 UTC lundi :

| Ticker | Qty | Market value |
|---|---|---|
| APA | +137 | $4,923.78 |
| CF | +7 | $812.00 |
| DOW | +22 | $808.94 |
| LYB | +12 | $822.96 |
| MPC | +3 | $642.36 |
| CNC | -21 | -$804.51 |
| EL | -10 | -$778.50 |
| HOOD | -45 | -$4,105.35 |
| MKC | -15 | -$792.75 |
| NCLH | -41 | -$830.66 |
| SMCI | -29 | -$835.20 |

**State local** dit seulement SPY → **divergence complète**. Origine probable : `us_sector_ls_40_5` paper runner a pris ces positions sector-ls chez Alpaca directement (pas tracké dans state local). Comme book mode=paper_only → reconciliation warning, pas d'impact live. Mais **le state local ne reflète pas la réalité Alpaca paper** → bug visibilité P2.

---

## Trades exécutés (lundi reste de journée + nuit)

**Aucun trade live exécuté** après le TP MCL dimanche soir (couvert par checkup matinal).

Détail paper :
- `us_sector_ls_40_5 paper: init @ 2026-04-17 pnl $+0.67` — paper runner tourne, PnL paper modeste.
- 7 signaux MES paper → tous SKIP (bug DUP573894 ci-dessus).
- 0 signal paper gold_trend_mgc V1 visible dans le log (cycle paper futures tourne 1×/jour à 14:00 UTC, pas de log individuel strat). **À investiguer** pour l'arming fast-track 2026-04-30.

---

## Cycles worker 24h (delta vs matinal)

| Cycle | Runs lundi journée | Erreurs | Status |
|---|---|---|---|
| `crypto_cycle` | ~96 (toutes les ~15 min) | 0 | **OK** mais STRAT-005 btc_dominance DISABLED invoquée 96× (pollution) |
| `futures_paper_cycle` | 1 (14:00 UTC) | 1 eu_relmom | DÉGRADÉ |
| `futures_live_cycle` | 1 (14:00 UTC) | 0 | **OK** |
| `paper_cycles` (EU) `eu_relmom_40_3` | 1 tentative | **`cannot reindex on an axis with duplicate labels`** à `eu_relmom.py:76 load_eu_returns` | **KO** depuis 16:00 UTC |
| `paper_cycles` (EU) `mib_estx50_spread` | N fois | `yfinance returned empty data` | DÉGRADÉ (probable férié Easter Monday EU) |
| `cycle_fx_paper` | ~40+ | **"no current event loop in thread"** + IBKR paper port 4003 not connected | DÉGRADÉ (FX désactivé ESMA, 0 impact live) |
| `reconciliation_cycle` | continu | Warnings only | **OK** |
| IB Gateway `ibgateway.service` | systemd active | **port 4002 ConnectionRefused depuis 04:46 UTC** | **Nocturne normal** (2FA attend matin) |
| Runtime audit strict | 1 run | Exit 0 | **OK** |

---

## Bugs ops détectés (nouveau vs matinal)

### 🚨 P1 — `live_pnl/summary.json` affiche un DD fantôme -52.65 %

```json
{"n_days":2, "start_date":"2026-04-19", "end_date":"2026-04-20",
 "start_equity_usd":20854.37, "end_equity_usd":9874.54,
 "pnl_usd":-10979.83, "cum_return_pct":-52.65%, "max_dd_pct":-52.65%}
```

Diagnostic : `end_equity_usd=9874.54` ≈ binance seul (state actuel $9,864.65). Le tracker **écrase le total desk avec binance.equity** au lieu de sommer brokers.

**Impact** :
- Dashboard et reporting business affichent un DD fantôme de **-52.65 %** qui n'existe pas (vrai total live = $21,139).
- Kill switch logic lit les state files canoniques (pas ce summary) → **pas de risque live**.
- **Risque visibilité** : si on pilote les promotions/fast-track sur des faux DD, on prend de mauvaises décisions.

**À corriger** : `scripts/live_pnl_tracker.py` section somme brokers.

### 🟡 P2 — 4 bugs paper runners

| Bug | Fichier | Impact |
|---|---|---|
| `eu_relmom` cycle error `cannot reindex on an axis with duplicate labels` | `strategies_v2/eu/eu_relmom.py:76 load_eu_returns` | Paper runner eu_relmom **KO depuis lundi 16:00 UTC** |
| Paper runner futures confond compte paper `DUP573894` et live `U25023333` | `core/worker/cycles/paper_cycles.py` (check `IBKR real position exists`) | **7 strats MES paper SKIP à tort** → fausse le paper validation avant promotion |
| `mes_monday_long_oc: pas un jour pattern` un **lundi** | `strategies_v2/futures/mes_monday_long_oc.py` (détection weekday) | mes_monday en paper reste **silencieux les lundis** → pas d'observation → promotion standard 2026-05-16 non informée |
| `STRAT-005 btc_dominance_rotation_v2` invoquée 96×/24h (DISABLED REJECTED) | `worker.py` cycle crypto (filtrage `status=disabled`) | Pollution logs, pas de trade mais bruit |

### 🟢 P3 — cosmétique

- `cycle_fx_paper: no current event loop` (~40 warnings/24h) — FX désactivé ESMA, 0 impact business.
- `MIB/ESTX50 yfinance empty data` lundi 20/04 — probable Easter Monday EU (confirmer et ajouter guard `is_eu_holiday()`).

---

## Risk state (inchangé vs matinal)

| Check | Valeur | Status |
|---|---|---|
| Kill switch live futures | `active=false, armed=true` | **OK** |
| Kill switch crypto | `active=false` (reset 2026-04-19 fix_dc16858) | **OK** |
| DD live daily 2026-04-20 | start $11,276.83 → actuel $11,274.93 = **-0.017 %** | **OK** (< -2.5 % level_1) |
| DD crypto daily/weekly/monthly | flat, peak tenu | **OK** |
| Safety mode | inactif | **OK** |
| Runtime audit strict | Exit 0 | **OK** |

---

## Pourquoi on n'a pas trade de nouveau signal

| Strat | Raison |
|---|---|
| `cross_asset_momentum` | TP hit, attend prochain rebal CAM (~2026-05-07 fenêtre 20j) |
| `gold_oil_rotation` | Signal dormant (spread gold-oil < 2 %) |
| 7 strats MES paper | **Bloquées à tort** par position DUP573894 paper (bug P2) |
| `eu_relmom_40_3` paper | **Cycle en erreur** depuis 16:00 UTC (bug P2) |
| `mib_estx50_spread` paper | yfinance empty (Easter Monday probable) |
| `alt_rel_strength_14_60_7` paper | Fréquence structurelle ~4 trades/30j, rien en 1j |
| `btc_asia_mes_leadlag` paper | `target 2026-04-19 not in daily` (dimanche absent) |
| `gold_trend_mgc V1` paper | Cycle paper futures 1×/jour, pas de log individuel |

---

## Décisions user en attente (lundi non-tranchées)

- [ ] **Funding `mib_estx50_spread`** +EUR 3.6K (grade S). Pas de décision visible dans les logs lundi. À trancher avant calendrier mib.
- [ ] **Fast-track `gold_trend_mgc V1`** arming earliest 2026-04-30 : vérifier d'ici là paper_pnl_net, nombre de trades paper observables (≥ 4 en 14j), divergence. Aujourd'hui J+4 paper (16 → 20).

---

## Actions requises

### 🔴 P1
1. **Corriger `scripts/live_pnl_tracker.py`** pour sommer IBKR + Binance correctement → regénérer `data/live_pnl/summary.json`. **Bloque le pilotage business honnête.**

### 🟡 P2
2. **Débugger `eu_relmom_40_3`** `cannot reindex on an axis with duplicate labels` → dédupliquer index dans `load_eu_returns`.
3. **Fixer paper runner futures** : check `IBKR real position exists` doit filtrer sur `account_id ∈ whitelist live` (pas paper DUP573894). Sinon 7 strats paper sont silencées à tort.
4. **Investiguer `mes_monday_long_oc: pas un jour pattern` un lundi** — soit filtre externe (VIX/SPY MA50) soit bug détection weekday.
5. **Filtrer STRAT-005 btc_dominance** du cycle crypto (status=disabled → skip sans log).

### 🟢 P3
6. **`cycle_fx_paper: no current event loop`** (~40 warnings/24h). FX ESMA désactivé, bruit uniquement.
7. **Ajouter guard `is_eu_holiday()`** pour `MIB/ESTX50` paper runner (Easter Monday).

### ❓ À investiguer
8. **Binance margin -$759 → 0 entre 05:30 UTC matin et 21/04 05:30 UTC** : margin clôturée ? Investiguer via `grep -iE 'margin.*repay|margin.*borrow|redeem' logs/worker/worker.log`. Si trade discret non attribué = incident P1.
9. **Alpaca paper 11 positions non tracked local** : vérifier si `us_sector_ls_40_5` paper runner écrit son state. Sinon bug visibilité.

---

## Résumé en 3 lignes

1. **Rien de nouveau lundi côté live** : 0 trade exécuté, 0 signal live déclenché, equity flat. Le TP CAM du dimanche soir reste le seul événement business de la période.
2. **Bug P1 sur `live_pnl/summary.json`** affichant un DD fantôme -52.65 % — **à corriger avant de piloter les promotions de fin mai**.
3. **4 bugs P2 paper runners** (eu_relmom KO, 7 MES paper silencées à tort, mes_monday pas un jour pattern un lundi, STRAT-005 pollution) — non bloquants live mais cassent la lisibilité paper.

---

**Rapport généré par /checkup — 2026-04-21 05:35 UTC (07:35 Paris)**
**Archive** : `reports/checkup/checkup_2026-04-20_endofday.md`
