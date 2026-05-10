# Fast-Track P0 Code Spec — prêt à exécuter post-lundi 2026-04-20

**As of** : 2026-04-19T19:35Z
**Doctrine** : [fast_track_promotion_policy.md](fast_track_promotion_policy.md)
**Plan** : [fast_track_launch_plan.md](fast_track_launch_plan.md) §2.1
**État** : spec figée, **aucune modif code/config tant que user ne donne pas go explicite**.
**Dependency** : gate user post-vérif paper runners lundi 2026-04-20 + décision mib_estx50 funding.

---

## 0. Contrat de cette spec

Chaque item P0 ci-dessous spécifie :
- **Fichier cible** (1 à 3 fichiers max)
- **Schéma exact** (lignes YAML / signatures fonction)
- **Validation ajoutée** (core/governance)
- **Tests requis** (pytest)
- **Impact invariants** (doit rester 0 sur I1-I12)
- **Définition de "done"** (runtime_audit exit 0 + tests pass)

**Ligne rouge** : la spec ne couvre QUE les 3 items P0 (§2.1 plan). Items P1 (pre_order_guard check 6, kill_switch fast_track) = différés post-arming.

---

## Item P0-1 : champ `probation_mode` dans `live_whitelist.yaml`

### Fichier schéma
`core/governance/live_whitelist.py` (module `LiveWhitelist`)

### Ajout schéma

```python
# core/governance/live_whitelist.py — extension class StrategyEntry
@dataclass(frozen=True)
class StrategyEntry:
    strategy_id: str
    book: str
    status: str  # Existant: disabled|paper_only|live_probation|live_core
    runtime_entrypoint: str
    sizing_policy: dict
    kill_criteria: dict
    # NOUVEAU champ optionnel :
    probation_mode: Optional[Literal["fast_track", "standard"]] = None
    notes: Optional[str] = None
```

### Règles de validation (à ajouter dans `LiveWhitelist._validate_entry()`)

```python
# Règle V1 : probation_mode valide uniquement si status == live_probation
if entry.probation_mode is not None and entry.status != "live_probation":
    raise ValueError(
        f"{entry.strategy_id}: probation_mode={entry.probation_mode!r} "
        f"requires status=live_probation, got {entry.status!r}"
    )

# Règle V2 : si status=live_probation et probation_mode absent -> default "standard"
if entry.status == "live_probation" and entry.probation_mode is None:
    entry.probation_mode = "standard"  # non-breaking default
```

### Tests requis

`tests/test_live_whitelist.py` — 3 nouveaux tests :

```python
def test_probation_mode_fast_track_requires_live_probation():
    # Doit lever si status=paper_only + probation_mode=fast_track
    ...

def test_probation_mode_standard_default_when_live_probation():
    # Si live_probation sans mode -> mode=standard
    ...

def test_probation_mode_enum_rejects_unknown():
    # probation_mode="express" -> lève
    ...
```

### Impact invariants

**Aucun** cassé. I1-I12 restent verts. Le champ est optionnel, enum strict via `Literal`, défaut rétrocompatible.

### Done when
- `pytest tests/test_live_whitelist.py -v` pass (existants + 3 nouveaux)
- `python scripts/runtime_audit.py --strict` exit 0 local + VPS
- 0 strat de `live_whitelist.yaml` a besoin d'être modifiée (champ optionnel)

### Coût estimé
~20 min code + 10 min tests = **30 min**.

---

## Item P0-2 : champ `fast_track_start_at` dans `quant_registry.yaml`

### Fichier schéma
`core/governance/quant_registry.py` (module `QuantRegistry`)

### Ajout schéma

```python
# core/governance/quant_registry.py — extension class QuantEntry
@dataclass(frozen=True)
class QuantEntry:
    strategy_id: str
    book: str
    status: str
    paper_start_at: Optional[str]     # YYYY-MM-DD
    live_start_at: Optional[str]      # YYYY-MM-DD
    wf_manifest_path: Optional[str]
    grade: str
    last_wf_run_at: Optional[str]
    is_live: bool
    infra_gaps: list
    # NOUVEAU champ optionnel :
    fast_track_start_at: Optional[str] = None  # YYYY-MM-DD
    wf_exempt_reason: Optional[str] = None
    notes: Optional[str] = None
```

### Règles de validation (à ajouter dans `QuantRegistry._validate_entry()`)

```python
# Règle V3 : fast_track_start_at uniquement si status=live_probation
if entry.fast_track_start_at is not None and entry.status != "live_probation":
    raise ValueError(
        f"{entry.strategy_id}: fast_track_start_at={entry.fast_track_start_at!r} "
        f"requires status=live_probation, got {entry.status!r}"
    )

# Règle V4 : si fast_track_start_at renseigné, live_start_at doit l'être aussi (même date ou plus ancien)
if entry.fast_track_start_at is not None:
    if entry.live_start_at is None:
        raise ValueError(
            f"{entry.strategy_id}: fast_track_start_at set but live_start_at is None"
        )
    if entry.fast_track_start_at < entry.paper_start_at:
        raise ValueError(
            f"{entry.strategy_id}: fast_track_start_at < paper_start_at"
        )

# Règle V5 : fast_track_start_at >= paper_start_at + 14 jours
if entry.fast_track_start_at is not None:
    from datetime import date
    paper = date.fromisoformat(entry.paper_start_at)
    ft = date.fromisoformat(entry.fast_track_start_at)
    if (ft - paper).days < 14:
        raise ValueError(
            f"{entry.strategy_id}: fast_track_start_at={ft} requires >= 14 days after paper_start={paper}"
        )
```

### Tests requis

`tests/test_quant_registry.py` — 4 nouveaux tests :

```python
def test_fast_track_start_at_requires_live_probation():
    ...

def test_fast_track_start_at_requires_live_start_at():
    ...

def test_fast_track_start_at_must_be_14d_after_paper_start():
    # paper_start=2026-04-16, fast_track=2026-04-25 -> lève (9j < 14)
    # paper_start=2026-04-16, fast_track=2026-04-30 -> OK (14j)
    ...

def test_fast_track_start_at_optional_default_none():
    # Absence du champ -> None, pas d'erreur
    ...
```

### Impact invariants

**I9** (live_* ⇒ live_start_at NOT NULL) renforcé par règle V4 : si `fast_track_start_at` renseigné → `live_start_at` obligatoire. Cohérent avec invariant existant.

### Done when
- `pytest tests/test_quant_registry.py -v` pass (existants + 4 nouveaux)
- `python scripts/runtime_audit.py --strict` exit 0 local + VPS
- 0 strat de `quant_registry.yaml` a besoin d'être modifiée (champ optionnel)

### Coût estimé
~25 min code + 15 min tests = **40 min**.

---

## Item P0-3 : `promotion_check.py --mode fast_track --min-paper-days 14`

### Fichier cible
`scripts/promotion_check.py` (extension, pas nouveau script)

### Signature CLI à étendre

```bash
python scripts/promotion_check.py <strategy_id> [--mode {standard,fast_track}] [--min-paper-days N]
```

- `--mode` : défaut `standard` (comportement existant). `fast_track` → applique gate doctrine §3.
- `--min-paper-days` : défaut 30 pour `standard`, 14 pour `fast_track`. Peut être override explicite.

### Logique mode=fast_track

Exécute dans l'ordre, exit 0 si toutes passent, exit N si N-ième échoue :

```python
# scripts/promotion_check.py — nouveau fonction fast_track_gate()
def fast_track_gate(strat_id: str, min_paper_days: int = 14) -> int:
    """Retourne 0 si GO, code d'échec >= 1 sinon."""

    # Gate §3.1 quant
    entry = quant_registry.get(strat_id)
    if entry.grade not in ("S", "A"):
        log_fail("grade_not_S_or_A", entry.grade)
        return 1
    if not os.path.exists(entry.wf_manifest_path or ""):
        log_fail("wf_manifest_missing", entry.wf_manifest_path)
        return 2
    if entry.wf_exempt_reason:
        log_fail("wf_exempt_not_allowed_in_fast_track", entry.wf_exempt_reason)
        return 3

    # TODO V2: parse manifest pour OOS %, DSR p-value, MC — stubbed ici
    # (Gate §3.1 règle 4 MC < 15% nécessite lecture manifest WF)

    if entry.paper_start_at is None:
        log_fail("paper_start_at_null")
        return 4
    paper_days = (date.today() - date.fromisoformat(entry.paper_start_at)).days
    if paper_days < min_paper_days:
        log_fail("insufficient_paper_days", paper_days, min_paper_days)
        return 5

    # Gate §3.1 règle 6-7 paper PnL + divergence → source live_pnl_tracker
    # Stubbed à V1 : warning si script indispo, exit code distinct
    pnl_net = live_pnl_tracker.paper_pnl_net(strat_id, since=entry.paper_start_at)
    if pnl_net is None:
        log_fail("paper_pnl_tracker_unavailable")
        return 6
    if pnl_net < 0:
        log_fail("paper_pnl_net_negative", pnl_net)
        return 7

    divergence = live_pnl_tracker.divergence_sigma(strat_id, since=entry.paper_start_at)
    if divergence is not None and abs(divergence) > 1.0:
        log_fail("divergence_gt_1_sigma", divergence)
        return 8

    # Gate §3.2 runtime
    book = entry.book
    book_health_status = book_health.check(book)
    if book_health_status != "GREEN":
        log_fail("book_not_green", book, book_health_status)
        return 9

    # §3.2 règle 11 incidents P0/P1 7j
    recent_incidents = incident_jsonl.count(book=book, severity=["P0", "P1"], within_days=7)
    if recent_incidents > 0:
        log_fail("recent_p0_p1_incidents", recent_incidents)
        return 10

    # §3.2 règle 12 kill switch inactif
    if kill_switch_state.is_active_for_book(book):
        log_fail("kill_switch_active", book)
        return 11

    # §3.2 règle 13 infra_gaps vide
    if entry.infra_gaps:
        log_fail("infra_gaps_not_empty", entry.infra_gaps)
        return 12

    # Gate §3.3 stratégie-spécifique
    # §3.3 règle 14 fréquence >= 4 trades en 14j
    trades_count = live_pnl_tracker.paper_trades_count(strat_id, since=entry.paper_start_at)
    if trades_count < 4:
        log_fail("insufficient_trades", trades_count)
        return 13

    # §3.3 règle 15 corrélation — DÉFÉRÉ V2 (nécessite marginal_contribution.py)
    # Warning seulement en V1

    # Gate §3.4 slots
    other_fast_track_same_book = live_whitelist.count_fast_track(book=book)
    if other_fast_track_same_book >= 1:
        log_fail("slot_occupied_book", book, other_fast_track_same_book)
        return 14
    total_fast_track_desk = live_whitelist.count_fast_track_total()
    if total_fast_track_desk >= 2:
        log_fail("slot_occupied_desk", total_fast_track_desk)
        return 15

    # §3.4 règle 17 capital at-risk total
    cap_at_risk_total = positions_live.total_capital_at_risk()
    cap_deployable = equity_state.total_deployable()
    if cap_at_risk_total / cap_deployable > 0.05:
        log_fail("capital_at_risk_gt_5pct", cap_at_risk_total, cap_deployable)
        return 16

    log_success(strat_id, "fast_track_gate_passed")
    return 0
```

### Règles comportementales

1. **Sortie stdout** : 1 ligne par check, format `[check_N] PASS|FAIL: reason`. Fin avec `FAST_TRACK_GATE_RESULT: GO` ou `NO_GO(exit={N})`.
2. **Sortie JSONL** : écrire `data/audit/promotion_checks.jsonl` ligne par exécution (timestamp, strat, mode, exit_code, reasons).
3. **Dry-run** : `--dry-run` ajouté → exécute tous les checks, n'écrit pas JSONL, exit 0 toujours.
4. **Exit code >= 100** : erreur technique (registry non lisible, etc.). Exit code 1-16 = gate fail par raison numéro.

### Tests requis

`tests/test_promotion_check_fast_track.py` — **nouveau fichier**, 16 tests (1 par check + 1 happy-path) :

```python
def test_fast_track_fails_on_grade_b(): ...
def test_fast_track_fails_on_missing_manifest(): ...
def test_fast_track_fails_on_exempt_reason(): ...
def test_fast_track_fails_on_null_paper_start_at(): ...
def test_fast_track_fails_on_insufficient_paper_days(): ...
def test_fast_track_fails_on_paper_pnl_unavailable(): ...
def test_fast_track_fails_on_paper_pnl_negative(): ...
def test_fast_track_fails_on_divergence_gt_1sigma(): ...
def test_fast_track_fails_on_book_not_green(): ...
def test_fast_track_fails_on_recent_p0_p1(): ...
def test_fast_track_fails_on_kill_switch_active(): ...
def test_fast_track_fails_on_infra_gaps_not_empty(): ...
def test_fast_track_fails_on_insufficient_trades(): ...
def test_fast_track_fails_on_slot_occupied_book(): ...
def test_fast_track_fails_on_slot_occupied_desk(): ...
def test_fast_track_fails_on_capital_at_risk_gt_5pct(): ...
def test_fast_track_passes_happy_path(): ...
```

Utiliser fixtures pour mocker quant_registry, live_whitelist, book_health, live_pnl_tracker, positions_live, equity_state, kill_switch_state, incident_jsonl.

### Impact invariants

**Aucun** modifié. Le script consomme les registries existants, n'écrit dans aucun YAML canonique.

### Done when
- `pytest tests/test_promotion_check_fast_track.py -v` : 17 tests pass
- Exécution manuelle sur gold_trend_mgc en date simulée 2026-04-30 → exit 0 si tous mocks cohérents
- `python scripts/promotion_check.py gold_trend_mgc --mode fast_track --min-paper-days 14 --dry-run` fonctionne sans erreur technique

### Coût estimé
~1h code + 45 min tests = **1h45**.

---

## Total charge P0

| Item | Coût |
|---|---|
| P0-1 `probation_mode` schéma | 30 min |
| P0-2 `fast_track_start_at` schéma | 40 min |
| P0-3 `promotion_check.py --mode fast_track` | 1h45 |
| **Total** | **~2h55** |

+ 15 min commit + push + vérif VPS deploy = **~3h10 bout-en-bout**.

---

## Séquence d'exécution (si go lundi soir 2026-04-20)

1. Branche `git checkout -b feat/fast-track-p0-schemas` (optionnel, ou direct main comme d'habitude)
2. Item P0-1 : schéma + tests + pytest local → commit
3. Item P0-2 : schéma + tests + pytest local → commit
4. Item P0-3 : script + tests + dry-run manuel → commit
5. `python scripts/runtime_audit.py --strict` local → exit 0 attendu (aucun YAML modifié)
6. `git push origin main` (ou merge branche)
7. VPS : `git pull && systemctl restart trading-worker && python scripts/runtime_audit.py --strict`
8. Si exit 0 VPS → P0 livré. Ready pour gate arming 2026-04-30.

---

## Items P1 différés (post-arming)

Rappel, **non couverts par cette spec** :

- `pre_order_guard.check_6` lit `sizing_cap_fast_track` → ajouté en P1 post-arming (sizing aujourd'hui est dans `sizing_policy` simple, pas besoin immédiat).
- `kill_switch_live.fast_track_criteria` strict → ajouté en P1 post-arming (kill_criteria standard existant suffit en première approximation).
- Post-mortem template → P2 backlog général.

**Rationale** : ces items peuvent être livrés **pendant** la fenêtre fast-track 2026-04-30 → 2026-05-14 sans bloquer l'arming. Le risk-if-stopped MGC faible ($100-130) + revue 2×/semaine compensent le délai.

---

## Conditions de réactivation de cette spec

Cette spec devient **active** (autorisation d'exécuter) si et seulement si :

1. Lundi 2026-04-20 : vérif paper runners fire effectivement (sinon priorité = debug runtime, pas code fast-track).
2. Décision mib_estx50 funding prise (si + EUR 3.6K → impact allocation ; si NON → reste paper, pas d'impact fast-track).
3. User donne **go explicite** sur cette spec (pas reco, pas "peut-être" — directive claire).

Tant que ces 3 conditions ne sont pas réunies, la spec reste figée en doc.

---

**Fin spec P0.** Aucune action code tant que go explicite.
