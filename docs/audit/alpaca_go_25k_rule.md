# Alpaca Go / No-Go 25K — Regle de promotion

**As of** : 2026-04-19T14:33Z
**Source executable** : `scripts/alpaca_go_25k_gate.py` (exit 0 GO / 1 WATCH / 2 NO_GO).
**Regle** : le script est la verite executable. Ce markdown doit s'aligner exactement sur le script. Toute divergence = bug documentation.

---

## 1. Pourquoi $25K

Alpaca (brokers US equities retail) applique le **Pattern Day Trader rule (PDT)** :
- Compte < $25K equity : max 3 day-trades / 5 jours glissants. Au-dela : PDT flag + 90j restriction.
- Compte >= $25K equity : **waive PDT** -> day-trades illimites.

Pour les strats `us_sector_ls_40_5`, `us_stocks_daily` qui peuvent rebalancer infrajournalier, depot $25K = **prerequis technique**.

**Cout** : $25K capital immobilise. A justifier par edge + trade frequency.

---

## 2. Ordre d'evaluation du gate (authoritatif = script)

Le script `_evaluate()` verifie dans cet ordre strict. **Premier match = verdict**.

### Phase NO_GO (exit 2)

1. **`NO_GO_paper_journal_missing`** — si aucun paper_journal.jsonl trouve pour la strat.
   - Recommendation : "Paper journal absent. Verifier que le paper runner ecrit bien sur VPS."
2. **`NO_GO_paper_too_short`** — paper_days < min_days (default 30).
   - Recommendation : "Continuer paper {N}j supplementaires."
3. **`NO_GO_drawdown_exceeded`** — paper_max_dd_pct > 8.0%.
   - Recommendation : "Pas de deposit. Revue strat + re-WF."
4. **`NO_GO_incident_open`** — incidents_open_p0p1 > 0 (filtres par window since paper_start + book=alpaca_us).
   - Recommendation : "Fermer incidents P0/P1 avant re-evaluation."
5. **`NO_GO_divergence_critical`** — max divergence > 2.5 sigma (parmi sharpe / pnl / wr).
   - Recommendation : "Paper diverge du backtest. Revue strat."

### Phase WATCH (exit 1)

6. **`WATCH_trade_count_low`** — trade_count < 12.
   - Recommendation : "Continuer paper, attendre {N} trades fermes supplementaires."
7. **`WATCH_divergence_elevated`** — max divergence > 1.5 sigma (entre 1.5 et 2.5).
   - Recommendation : "Divergence paper/backtest elevee. Continuer 15j supplementaires."
8. **`WATCH_pnl_negative`** — paper_pnl_net < 0.
   - Recommendation : "PnL paper negatif. Continuer 15j, revue edge si persiste."
9. **`WATCH_drawdown_elevated`** — paper_max_dd_pct > 6.0% (entre 6% et 8%).
   - Recommendation : "Drawdown paper superieur a cible. Surveillance accrue."

### Phase GO (exit 0)

10. **`GO_25K`** — toutes les conditions GO tenues simultanement :
    - paper_days >= min_days (30)
    - trade_count >= 12
    - paper_pnl_net >= 0
    - paper_max_dd_pct <= 6.0%
    - max divergence <= 1.5 sigma
    - incidents_open == 0
    - Recommendation : "Depot $25K Alpaca recommande. Promotion us_sector_ls_40_5 vers live_probation autorisee."

### Condition NON verifiee par le script (documentation uniquement)

- **`promotion_gate` vert** : le script `alpaca_go_25k_gate.py` **ne verifie PAS** formellement le passage de `core.governance.promotion_gate`. Cette condition est **operationnelle** (a verifier manuellement au moment du depot capital via `python -c "from core.governance.promotion_gate import check; ..."`). A integrer Phase 2 si critique.

---

## 3. Metriques sources (machine-readable)

### Fichiers consommes par le script

| Fichier | Usage |
|---|---|
| `config/quant_registry.yaml` | lire `paper_start_at` + `book` pour filter incidents |
| `data/state/{strategy}/paper_journal.jsonl` (ou `us_sector_ls`, `alpaca_us/{strategy}_journal.jsonl`) | paper trades |
| `data/research/wf_manifests/{strategy}_*.json` | baseline backtest (sharpe, pnl, wr + std) |
| `data/incidents/*.jsonl` | incidents P0/P1/CRITICAL filtrees depuis paper_start + book |

### Calculs implementes

- `paper_days` = `date.today() - paper_start_at.date()`
- `trade_count` = count entries with `exit_price` present OR `action == "close"`
- `paper_pnl_net` = sum `pnl_after_cost` OR `realized_pnl_usd`
- `paper_wr` = win rate sur closed trades
- `paper_max_dd_pct` = max drawdown sur equity curve cumule
- `paper_sharpe` = annualised mean(daily_return) / std * sqrt(252), requires >= 5 trades + manifest
- `div_sigmas` = divergence vs manifest baseline (sharpe_std + pnl_std + wr_std)

### Calculs NON implementes (gap documente)

- **paper vs broker reconciliation** : le script n'audite pas que le broker Alpaca a effectivement flag les positions simulees.
- **WF re-run freshness** : pas de check si manifest date > N jours.

---

## 4. Etat courant reel (script run 2026-04-19T14:33Z)

### Commande executee

```
python scripts/alpaca_go_25k_gate.py --strategy us_sector_ls_40_5
```

### Output brut (verbatim)

```
========================================================================
  Alpaca Go / No-Go 25K Gate   strategy=us_sector_ls_40_5
========================================================================
  Paper start    : 2026-04-18
  Paper days     : 1
  Trades ferme   : 0
  PnL net paper  : $0.00
  Max DD paper   : 0.00%
  WR paper       : 0.0%
  Incidents P0/P1: 0
------------------------------------------------------------------------
  Verdict        : NO_GO_paper_journal_missing
  Reasons        : paper_journal_missing
  Recommendation : Paper journal absent. Verifier que le paper runner ecrit bien sur VPS.
========================================================================
exit=2
```

### Verdict courant reel

**`NO_GO_paper_journal_missing`** (exit code 2)

### Blocage exact

Le script n'a trouve **aucun** paper_journal pour `us_sector_ls_40_5` dans les chemins candidates :
1. `data/state/us_sector_ls_40_5/paper_journal.jsonl`
2. `data/state/us_sector_ls/paper_journal.jsonl`
3. `data/state/alpaca_us/us_sector_ls_40_5_journal.jsonl`

Localement : aucun paper journal (attendu sur dev Windows).

VPS : a verifier lundi 2026-04-20 apres premier cycle `run_us_sector_ls_paper_cycle` (schedule 23h30 Paris weekday). Le paper runner devrait creer le journal lors du 1er fire.

### Prochaine condition necessaire pour progresser

**Pour sortir de NO_GO_paper_journal_missing** :
- Confirmer qu'une execution weekday de `run_us_sector_ls_paper_cycle` a tourne sur VPS (lundi 2026-04-20 23h30 Paris = apres close US). Source : `logs/worker/worker.log` VPS.
- Si journal n'existe toujours pas apres premier lundi ouvre → bug scheduler OU data us_stocks stale.

**Pour atteindre GO_25K** (toutes conditions) :
1. paper_journal.jsonl existe (debloque NO_GO_paper_journal_missing)
2. >= 30 calendar days paper (earliest = 2026-05-18)
3. >= 12 trades fermes (hebdo ~5 trades/cycle => >= 3 cycles complets)
4. paper_pnl_net >= 0
5. max_dd_paper <= 6.0%
6. max divergence vs backtest <= 1.5 sigma
7. 0 incident P0/P1 ouvert filtree window [paper_start, now] + book alpaca_us

**Earliest GO_25K theorique** : **2026-05-18** + >= 12 trades observes + metriques stables.

---

## 5. Script interface — exit codes & flags

```
python scripts/alpaca_go_25k_gate.py [--strategy STRATEGY_ID] [--min-days N] [--json]

Exit codes:
  0 = GO_25K (depot recommande)
  1 = WATCH_* (continuer paper)
  2 = NO_GO_* (blocage)

Flags:
  --strategy : default "us_sector_ls_40_5"
  --min-days : default 30 (DoD paper minimum)
  --json     : output JSON (machine-readable) vs texte lisible
```

---

## 6. Coupling avec IBKR futures roadmap

Le deposit $25K Alpaca est **independant** des decisions IBKR futures. Priorite de funding si capital user supplementaire disponible (ordre decroissant) :

| Rang | Action | Conditions | Upside estimé |
|---|---|---|---|
| 1 | **+EUR 3.6K IBKR** pour mib_estx50_spread | mib_estx50 grade S, capital gap fixe | +$1,750/an (conservative haircut) |
| 2 | **+$25K Alpaca** pour PDT waiver | Gate GO_25K obtenu | +$300-800/an probation |
| 3 | **+$5K Binance** pour scaler alt_rel_strength post live_probation | alt_rel_strength 30j paper OK | +$150-300/an marginal |

Coherent avec directive : **pas de scale sans preuve machine-readable**. Gate `alpaca_go_25k_gate.py` est le mecanisme declaratif pour Alpaca.

---

## 7. Template decision (re-check hebdo ou bi-mensuel)

```markdown
### Alpaca 25K gate check — 2026-MM-DD

- Commande : python scripts/alpaca_go_25k_gate.py --strategy us_sector_ls_40_5
- Exit code: N (0 / 1 / 2)
- Verdict : GO_25K | WATCH_* | NO_GO_*
- Paper days : N
- Trade count : X
- PnL net : $Y
- Max DD : Z%
- Divergence max : W sigmas
- Incidents open : K
- Recommendation : [action concrete]
- Next review : 2026-MM-DD
```

Historique : `docs/audit/alpaca_gate_history.md` (a creer apres premier check avec journal present).

---

## 8. Honnêtete auditeur — divergences script vs anciennes versions doc

Pre-correction iter3-fix2 (ce document) :
- Ancien doc listait "paper < 30j" en 1er NO_GO — **FAUX**, script evalue `journal_missing` avant.
- Ancien doc affichait "Verdict courant: NO_GO_paper_too_short" — **FAUX**, script sort `NO_GO_paper_journal_missing`.
- Ancien doc implicait "crash runtime > 2 = NO_GO" — **FAUX**, aucun check crash runtime dans le script.
- Ancien doc suggerait "promotion_gate vert" comme condition GO — **IMPLICITE, non verifie par script**, desormais clarifie section 2.

Ce document est maintenant **aligne 100%** sur l'implementation `scripts/alpaca_go_25k_gate.py`.
