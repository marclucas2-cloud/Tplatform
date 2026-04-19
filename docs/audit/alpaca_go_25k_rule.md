# Alpaca Go / No-Go 25K — Regle de promotion

**Date** : 2026-04-19
**Objectif** : decider objectivement si le depot +$25K Alpaca (pour PDT waiver + live us_sector_ls_40_5) est justifie, base sur des metriques machine-readable du paper trading.

---

## 1. Pourquoi $25K

Alpaca (et brokers US equities en general) applique le **Pattern Day Trader rule** (PDT) sur comptes retail :
- Compte < $25K equity : max 3 day-trades / 5 jours glissants. Au-dela : flag PDT -> compte freeze 90j.
- Compte >= $25K equity : **waive PDT** -> day-trades illimites.

Pour les strats Alpaca daily+intraday (us_sector_ls_40_5, us_stocks_daily), le PDT rule peut bloquer des re-balances ou rotations. Depot $25K = **prerequis technique**.

**Cout** : $25K capital immobilise. A justifier par edge et trade frequency.

---

## 2. Criteres de go (machine-readable)

### Regle GO_25K (toutes conditions ET)

1. **Paper stable** : >= 30 jours calendaires continus en mode paper sans crash runtime.
2. **Trade count** : >= 12 trades fermes (entries + exits complets) sur la periode paper.
3. **PnL net positif** : `paper_pnl_net_after_costs >= 0` sur la periode.
4. **Drawdown max paper** : `max_dd_paper <= 6%` (plus strict que kill_criteria standard -10%).
5. **Divergence vs backtest <= 1.5 sigma** : sur au moins **2 metriques** parmi (Sharpe, PnL cumule, WR, avg trade PnL).
6. **0 incident ouvert P0 / P1** : `data/incidents/*.jsonl` sans entry `status=open` severity in (P0, P1) sur la periode.
7. **Promotion gate vert** : `core.governance.promotion_gate.check(strategy_id="us_sector_ls_40_5", target_status="live_probation")` retourne `PASS`.

### Regle WATCH (au moins 1 NON dans GO mais pas BLOCK)

Criteres 1-4 OK mais divergence 1.5-2.5 sigma OU trade count 6-11 OU incident P2 ouvert.

**Action** : continuer paper 15 jours supplementaires. Re-evaluer.

### Regle NO_GO (bloquants)

- Paper < 30 jours.
- Divergence > 2.5 sigma sur >= 1 metric.
- Drawdown paper > 8%.
- Incident P0/P1 ouvert.
- Crash runtime > 2 sur periode.

**Action** : pas de deposit. Revue strat + re-WF si necessaire.

---

## 3. Metriques sources (machine-readable)

### Fichiers consommes

- `data/state/alpaca_us/paper_portfolio_state.json` : equity snapshot.
- `data/state/us_sector_ls/paper_journal.jsonl` : journal paper trades (**a creer** si n'existe pas).
- `data/research/wf_manifests/us_sector_ls_40_5_*.json` : baseline backtest (Sharpe, WR, PnL).
- `data/incidents/*.jsonl` : timeline P0/P1.

### Calculs

```python
def compute_alpaca_gate_metrics(journal_path: Path, backtest_manifest: dict) -> dict:
    trades = load_jsonl(journal_path)
    closed_trades = [t for t in trades if t.get("exit_price") is not None]

    paper_days = (today - start_date).days
    trade_count = len(closed_trades)
    paper_pnl_net = sum(t["pnl_after_cost"] for t in closed_trades)
    paper_sharpe = compute_sharpe(daily_returns(trades))
    paper_wr = len([t for t in closed_trades if t["pnl_after_cost"] > 0]) / max(1, trade_count)
    paper_max_dd = compute_max_dd(equity_curve(trades))

    # Divergence vs backtest (en sigmas)
    bt = backtest_manifest["baseline"]
    div_sharpe_sigma = abs(paper_sharpe - bt["sharpe"]) / bt["sharpe_std"]
    div_wr_sigma = abs(paper_wr - bt["wr"]) / bt["wr_std"]
    div_pnl_sigma = abs(paper_pnl_net - bt["pnl_expected_for_period"]) / bt["pnl_std"]

    return {
        "paper_days": paper_days,
        "trade_count": trade_count,
        "paper_pnl_net": paper_pnl_net,
        "paper_max_dd_pct": paper_max_dd,
        "paper_sharpe": paper_sharpe,
        "paper_wr": paper_wr,
        "div_sigmas": {
            "sharpe": div_sharpe_sigma,
            "wr": div_wr_sigma,
            "pnl": div_pnl_sigma,
        },
    }
```

### Evaluation gate

```python
def evaluate_alpaca_gate(metrics: dict, incidents_open_p0p1: int) -> str:
    # NO_GO conditions
    if metrics["paper_days"] < 30:
        return "NO_GO_paper_too_short"
    if metrics["paper_max_dd_pct"] > 8.0:
        return "NO_GO_drawdown_exceeded"
    if incidents_open_p0p1 > 0:
        return "NO_GO_incident_open"
    if max(metrics["div_sigmas"].values()) > 2.5:
        return "NO_GO_divergence_critical"

    # WATCH conditions
    if metrics["trade_count"] < 12:
        return "WATCH_trade_count_low"
    if max(metrics["div_sigmas"].values()) > 1.5:
        return "WATCH_divergence_elevated"

    # GO conditions (all must hold)
    go_checks = [
        metrics["paper_days"] >= 30,
        metrics["trade_count"] >= 12,
        metrics["paper_pnl_net"] >= 0,
        metrics["paper_max_dd_pct"] <= 6.0,
        max(metrics["div_sigmas"].values()) <= 1.5,
        incidents_open_p0p1 == 0,
    ]
    return "GO_25K" if all(go_checks) else "WATCH_mixed_signals"
```

---

## 4. Implementation proposee

**Fichier** : `scripts/alpaca_go_25k_gate.py` (a implementer).

```bash
python scripts/alpaca_go_25k_gate.py --strategy us_sector_ls_40_5 --min-days 30
# Output:
#   verdict: GO_25K | WATCH_* | NO_GO_*
#   metrics: {paper_days, trade_count, pnl, max_dd, sharpe, divergences}
#   recommendation: deposit $25K | continue paper 15 more days | halt + review
```

**Telegram integration** (optionnel) : command `/alpaca_gate` retourne verdict + metriques.

**Dashboard widget** (optionnel P3) : section "Alpaca 25K gate" sur `/overview` avec countdown jours restants + progress metriques.

---

## 5. Etat actuel (2026-04-19)

| Metrique | Valeur actuelle | Cible GO_25K | Status |
|---|---|---|---|
| Paper days | 1 (start 2026-04-18) | >= 30 | ❌ NO_GO (trop court) |
| Trade count ferme | 0 | >= 12 | ❌ |
| PnL net | n/a | >= 0 | — (pas de trades) |
| Max DD paper | n/a | <= 6% | — |
| Divergence Sharpe | n/a | <= 1.5 sigma | — |
| Incidents P0/P1 ouverts | 0 | 0 | ✅ |
| Journal paper existe | ❌ (pas vu VPS) | Oui | ❌ **P0 a verifier** |

**Verdict courant** : **NO_GO_paper_too_short** (attendu, debut paper 1 jour).

**Earliest GO_25K possible** : **2026-05-18** (+ 12 trades fermes + PnL ok + divergence contenue).

**Attention P0** : le paper journal `data/state/us_sector_ls/paper_journal.jsonl` n'est pas observe sur VPS actuellement. Si absence persiste apres 2026-04-21 (premiers cycles US weekday), le runner n'ecrit pas -> **blocker P0**.

---

## 6. Coupling avec IBKR futures roadmap

Le deposit $25K Alpaca est **independant** des decisions IBKR futures. Cependant :

**Priorite de funding si capital supplementaire dispo** (ordre decroissant) :
1. **EUR 3.6K IBKR** pour mib_estx50_spread (Sharpe 3.9 backtest, strat unique grade S).
2. **$25K Alpaca** pour PDT waiver (si us_sector_ls + us_stocks_daily validated paper gate).
3. **+$5K Binance** pour scaler alt_rel_strength post live_probation ok.

**Coherent avec directive** : ne **pas** scaler capital sans preuve machine-readable. Gate alpaca_go_25k est le mecanisme.

---

## 7. Decision template

Pour chaque re-evaluation (hebdo ou bi-mensuel) :

```markdown
### Alpaca 25K gate check — 2026-MM-DD

- Paper days : N
- Trade count : X
- PnL net : $Y
- Max DD : Z%
- Divergence max : W sigmas
- Incidents open : K
- Verdict : GO_25K | WATCH_* | NO_GO_*
- Recommendation : [action concrete]
- Next review : 2026-MM-DD
```

Historique tenu dans `docs/audit/alpaca_gate_history.md` (a creer apres premier check utile).
