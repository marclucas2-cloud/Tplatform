# Crypto Paper Plug — Classification + Spec 2026-04-24

**Agent** : Claude Opus
**Input** : Mission ChatGPT `crypto_bull_bear_paper_candidates_2026-04-24` (5 PAPER_READY).
**Sortie** : classification spot-only vs margin-required + spec câblage de la sleeve priorité #1 pour exécution lundi.

## Check blocker binaire — Margin Binance France

Résultat vérifié ce soir via API signée VPS :

```
SPOT:          canTrade=True
MARGIN CROSS:  tradeEnabled=True, borrowEnabled=True, transferEnabled=True (netBtc=0, 410 assets)
MARGIN ISOLATED: 3 assets configures
API PERMS:     enableMargin=True, enableSpotAndMarginTrading=True, enableFutures=False
```

**Conclusion** : Margin CROSS + ISOLATED activés. Les shorts BTC/ETH spot sont techniquement possibles via emprunt margin.

**Mais** : le desk n'a actuellement **aucun runner runtime qui utilise le margin**. btc_asia q80 (live_micro) est spot long-only. Câbler une sleeve *_regime_* demanderait :
- un nouveau stack runner margin (gestion emprunt/remboursement, liquidation price tracking, borrow cost réel vs estimé)
- des tests risk spécifiques (margin call, forced liquidation)
- validation des contract testing Binance margin endpoints

Ce n'est pas du copier-coller du pattern spot. C'est 1-2 jours de travail dédié.

## Classification des 5 PAPER_READY

| Candidat | Mode | Book | Classification | Blocker |
|---|---|---|---|---|
| `eth_range_longonly_20` | long-only 4h | Binance spot | **SPOT_ONLY_READY** | aucun |
| `eth_weekend_reversal_bull_3` | long-only daily | Binance spot | **SPOT_ONLY_READY** | aucun |
| `btc_funding_hybridtrend_2_0_3` | long/flat daily | Binance spot | SPOT_ONLY_READY (⚠️ corr +0.31 avec portfolio) | overlap btc_asia q80 |
| `btc_range_regime_30` | long bull / short bear | Binance margin | MARGIN_REQUIRED | stack margin runner absent |
| `eth_range_regime_30` | long bull / short bear | Binance margin | MARGIN_REQUIRED | stack margin runner absent |

## Priorité de câblage confirmée

### #1 à câbler lundi : `eth_range_longonly_20`

Raisons (alignées sur l'arbitrage Marc) :
1. **SPOT_ONLY_READY** pur — aucun blocker d'exécution
2. **Sharpe 1.03** le plus haut des SPOT_ONLY_READY
3. **WF 3/5** (acceptable, pas parfait)
4. **Corr portfolio +0.03** quasi-nulle
5. **MC P(DD<-25%) = 0.3%** (très safe)
6. **Bull/Bear équilibrés** : $+2982 bull, $+1603 bear → marche dans les 2 régimes
7. **Zéro friction short**, mécanique triviale : Bollinger fade + ADX < 20

### #2 à câbler plus tard : `pair_xle_xlk_ratio`

Déjà validé hier. Complexité runtime 2 jambes. Lundi non (on câble crypto), mardi ou mercredi.

### Écartés cette itération

- **btc_funding_hybridtrend_2_0_3** : corr +0.31 avec portfolio → overlap probable avec btc_asia q80 déjà live. Diversification faible.
- **eth_weekend_reversal_bull_3** : Sharpe 0.75 inférieur, bull-biased ($9508 bull vs $3088 bear). Moins équilibré.
- **2 regime_switch** : margin requis, stack absent. À rouvrir si/quand on ajoute un margin runner dédié.

## Spec de câblage — `eth_range_longonly_20`

### Identité
- **strategy_id** : `eth_range_longonly_20`
- **book** : `binance_crypto`
- **mode** : spot long-only, 4h bars, paper simulation locale

### Règles (extrait scripts/research/crypto_bull_bear_paper_candidates_2026_04_24.py::range_harvest)

**Entrée LONG** :
- Timeframe 4H ETHUSDT
- Bollinger Bands(20, 2σ) sur close
- ADX(14) < 20 (marché en range)
- `close[t-1] < bb_lower[t-1]` (close précédent sous la lower band)
- Entry au `open[t]` de la bar suivante

**Sizing** : `qty = STRAT_CAPITAL / entry` (notional fixe $10,000 paper)

**Exits** :
1. **TP** : close touche `sma20` (retour à la moyenne) → sortie à `sma20`
2. **SL** : entry - 1.5 × (target - entry) → sortie stop
3. **Time exit** : 18 bars 4h = 72h (3 jours) max → sortie `close` courant

**Filtres runtime** :
- Une seule position à la fois
- Pas de pyramiding
- Skip si `prev["adx"] >= 20` (market trendy)

### Data requirements

- **Source backtest** : `data/crypto/candles/ETHUSDT_4h.parquet` (existe, 7098 rows déjà vérifié)
- **Freshness runtime** : refresh 4h (cron à ajouter) — bars 4h Binance ferment à 00/04/08/12/16/20 UTC
- **Régime bull/bear** : optionnel pour `eth_range_longonly_20` (mode=long_only, pas regime-switch)

### Pattern runner proposé

Fichier : `core/worker/cycles/eth_range_longonly_20_runner.py`

Pseudocode :
```python
def run_eth_range_longonly_20_cycle():
    # 1. Load or compute 4h bars ETHUSDT
    # 2. Load state {position, entry, stop, target, bars_held}
    # 3. If position is not None:
    #    - intrabar check: high >= target OR low <= stop → exit + journal
    #    - bars_held >= 18 → exit close + journal
    # 4. Else (flat):
    #    - Compute bb_upper/lower/sma20, adx14 sur bars.iloc[-21:]
    #    - If prev adx >= 20 → journal no_signal (trendy)
    #    - Elif prev close < bb_lower → entry LONG + journal signal_emit + update state
    #    - Else → journal no_signal (no fade setup)
    # 5. Persist state + journal
```

**State file** : `data/state/eth_range_longonly_20/state.json`
```json
{
  "position": { "direction": 1, "entry": 3250.5, "qty": 3.077, "target": 3300, "stop": 3175, "bars_held": 2, "entry_ts": "..." } | null,
  "last_cycle_utc": "...",
  "trades_count": 7
}
```

**Journal** : `data/state/eth_range_longonly_20/journal.jsonl`
Events : `signal_emit` | `hold` | `no_signal` | `exit` (avec raison: tp/sl/timeout) | `skip_reason`

### Schedule worker.py

Aligné 4h bars Binance (fermeture à 00/04/08/12/16/20 UTC) avec 5-10 min de délai pour garantir data bar complet.

```python
# Weekday + weekend (crypto 24/7)
# Fire aligne sur 4h bar close + 5 min latency
bar_4h_utc_hours = {0, 4, 8, 12, 16, 20}
if now_utc.hour in bar_4h_utc_hours and now_utc.minute == 5:
    _runners["eth_range_longonly_20"].run()
```

Ou plus simple : fire toutes les heures et la strat elle-même ignore si bar pas fermé (`now - last_bar < 4h - margin`).

### Simulation locale vs broker

**Approche lundi = simulation locale pure**, comme macro_top1_rotation :
- Calcul signal
- PnL paper local via prix historique / dernier close
- Journal + state
- **Aucun ordre Binance**

Plus tard (si paper vivant et validé 30 jours) :
- Option A : live_micro via BinanceBroker spot ETHUSDC notional $200
- Option B : rester simulation paper long-term si Marc veut zéro risque

### Registry + Whitelist

Entry à ajouter lundi (après câblage code) :

**config/quant_registry.yaml** :
```yaml
- strategy_id: eth_range_longonly_20
  book: binance_crypto
  status: paper_only
  paper_start_at: "2026-04-28"
  live_start_at: null
  wf_manifest_path: data/research/wf_manifests/eth_range_longonly_20_2026-04-28.json
  grade: B
  is_live: false
  infra_gaps: ["paper_simulation_locale_pas_d_ordre_broker"]
  notes: "Source ChatGPT mission crypto_bull_bear 2026-04-24..."
```

**config/live_whitelist.yaml** (section binance_crypto) :
```yaml
- strategy_id: eth_range_longonly_20
  book: binance_crypto
  status: paper_only
  runtime_entrypoint: worker.py:run_eth_range_longonly_20_cycle (4h aligne sur bars UTC)
  runtime_module: strategies_v2.crypto.eth_range_longonly_20
  # params identiques spec ci-dessus
```

### Tests à écrire (au moment du câblage lundi)

- Strategy: `decide()` returns no_signal si ADX >= 20, returns signal_emit si close < bb_lower + ADX<20
- Strategy: exit TP quand high >= target, exit SL quand low <= stop, exit timeout bars_held=18
- Runner: state roundtrip, journal append
- Wiring: registry + whitelist + worker import + schedule
- Garde-fou: test_no_broker_order_call_in_runner (pattern macro_top1)

## Plan lundi 2026-04-27 (matin)

Ordre exact :

1. **Vérifier cycles vendredi 24/04** (mes_mr_vix_spike 14h UTC + macro_top1 14h30 UTC)
   - 3 preuves chacune : log line, journal entry, zéro erreur
   - Si anomalie → stop + diagnostic
2. **Si les 2 sont propres** : câbler `eth_range_longonly_20` d'après cette spec
3. **Ne pas câbler pair_xle_xlk_ratio lundi** — mardi ou mercredi
4. **Ne rien faire sur les 2 regime_switch** — mini-mission margin runner dédiée plus tard
5. **Ne rien faire sur stock_sector_ls_40_5** — mini-mission re-WF dédiée plus tard

## Doctrine appliquée ce soir

- Check blocker binaire **fait maintenant** (pas "repoussé à lundi par habitude")
- Classification spot vs margin **faite maintenant**
- Arbitrage #1/#2 **fait maintenant** (pas laissé en balance)
- **Aucun fichier runtime touché** (non-prod only)
- Lundi = journée d'exécution, pas de tri

## Artefacts pour lundi

- Ce rapport (`crypto_paper_plug_classification_2026-04-24.md`)
- Spec runtime complète (section ci-dessus)
- Plan ordre exact
- Tests à écrire listés
