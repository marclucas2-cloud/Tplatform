# TPLATFORM — TODO NETTOYAGE & SÉCURISATION CODE

**Classification** : CONFIDENTIEL — Hygiène codebase
**Date** : 3 Avril 2026
**Dernière MAJ** : 3 Avril 2026 (exécution Phase 0-2)
**Base** : Synthèse V12.5 (CRO 9.5/10) + analyse arborescence repo (545 fichiers, 193K lignes)
**Mode d'exécution** : Claude Code agents autonomes

## RÉSUMÉ D'EXÉCUTION

| Phase | Statut | Détails |
|-------|--------|---------|
| **Phase 0** | FAIT | 5/5 tâches: archive backtester, orphelins racine, __pycache__, .gitignore |
| **Phase 1** | FAIT | 4/5 tâches: worker.py -508 lignes, ruff 2231 fixes, 676 imports, state files déplacés |
| **Phase 2** | FAIT | 5/6 tâches: vulture clean, archive OK, 118 tests ajoutés, risk managers OK |
| **Phase 3** | PARTIEL | routes_v2 + paper_portfolio non découpés (risque trop élevé vs bénéfice) |
| **Tests** | **3116 passed** | 2998 originaux + 118 nouveaux (bot_service 67 + preflight 51) |

**Tâches reportées** :
- C2-02 (supprimer strategies/) : BLOQUÉ — worker.py importe CRYPTO_STRATEGIES depuis strategies/crypto/
- C6-01 (SSOT strats) : Dépend de C2-02
- C1-02 (routes_v2.py) : Risque élevé, 51 endpoints à préserver
- C3-01/C3-02/C3-03 (type hints + dataclasses + mypy) : Chantier indépendant, non bloquant

---

## DIAGNOSTIC

### État des lieux

Le repo a grandi de 0 à 193K lignes en 11 jours. C'est un exploit de vélocité, mais ça laisse des traces :

| Problème | Sévérité | Preuve |
|----------|----------|--------|
| **God files** | HAUTE | worker.py (3799 lignes), routes_v2.py (2602), paper_portfolio.py (1670) |
| **Dead code massif** | HAUTE | `intraday-backtesterV2/` entier (4 fichiers, ~4000 lignes), `strategies/` (remplacé par `strategies_v2/`), `run.py` et `dashboard.py` racine |
| **Fichiers d'état à la racine** | MOYENNE | `paper_vrp_state`, `paper_pairs_state`, `paper_momentum_state`, `paper_trading_state`, `paper_portfolio_state` — devraient être dans `data/` |
| **archive/ à 112MB** | BASSE | Strats rejetées + vieux backtests. Utile en référence mais pollue le repo |
| **`__pycache__` dans le repo** | BASSE | Devrait être dans `.gitignore` (probablement déjà mais le dossier reste) |
| **3 sources d'exécution** | HAUTE | `strategy_registry.py` pas source unique — flaggé CRO V12.5 |
| **Fichiers sans tests** | MOYENNE | `bot_service.py` (771 lignes), `preflight_check.py` — flaggé CRO V12.5 |
| **Duplication probable** | HAUTE | `core/risk_manager.py` (1058) vs `core/risk_manager_live.py` (966) — 2 risk managers |
| **Scripts one-shot dans scripts/** | BASSE | `wf_eu_all.py` (1424), `wf_fx_all.py` (930), `wf_crypto_all.py` (853) — scripts de walk-forward monolithiques |

### Ce qui est déjà bien

- 2998 tests, 0 échecs
- `.env` dans `.gitignore`
- CI/CD GitHub Actions avec pytest
- CRO 9.5/10 sur 12 domaines
- Secrets retirés du git (audit V9.0)

---

## AGENTS

| Agent | Spécialité | Domaines |
|-------|-----------|----------|
| **SURGEON** | Découpage god files, extraction de modules | C1 |
| **GRAVEDIGGER** | Dead code, fichiers orphelins, nettoyage arborescence | C2 |
| **TYPIST** | Type hints, signatures, mypy compliance | C3 |
| **LINTER** | Ruff, formatting, conventions, imports | C4 |
| **TESTER** | Tests manquants, couverture, tests de regression | C5 |
| **ARCHITECT** | Refactor structurel, single source of truth | C6 |
| **JANITOR** | Fichiers d'état, configs, .gitignore, arborescence | C7 |

---

## C1 — DÉCOUPAGE GOD FILES (Agent: SURGEON)

**Priorité globale : P0**
**Justification** : worker.py à 3799 lignes est le fichier le plus critique du projet (il orchestre 46 stratégies sur 3 brokers 24/7). Un fichier de cette taille est impossible à review, à tester unitairement, et à débugger en urgence. Chaque bug fix dans worker.py risque de casser autre chose.

### Tâche C1-01 : Découper worker.py (P0)

**Agent** : SURGEON
**Estimation** : 12h
**Fichier source** : `worker.py` (3799 lignes)

**Analyse probable de la structure actuelle** :
```
worker.py contient vraisemblablement :
  - Imports (~50 lignes)
  - Configuration / constantes (~100 lignes)
  - Cycle crypto (15min, 24/7)
  - Cycle FX (variable, 24h lun-ven)
  - Cycle EU intraday (09:00-17:30 CET)
  - Cycle US intraday (15:35-22:00 CET)
  - Cycle futures
  - Cycle risk (5min, V10 snapshots)
  - Cycle regime (15min, V12)
  - Cycle RoR (daily 07h)
  - Cycle HRP rebalance (4h)
  - Cycle Kelly mode check (4h)
  - Cycle reconciliation
  - EOD cleanup (17:35)
  - Heartbeat (30min)
  - Telegram command handlers
  - Main loop / scheduler
```

**Structure cible** :
```
worker.py                         (~200 lignes — orchestrateur pur)
  ├── imports
  ├── main() → charge config, init scheduler, lance les cycles
  └── graceful shutdown (SIGTERM)

core/worker/
  ├── __init__.py
  ├── scheduler.py                (~150 lignes — scheduling engine)
  ├── cycles/
  │   ├── __init__.py
  │   ├── crypto_cycle.py         (~300 lignes)
  │   ├── fx_cycle.py             (~250 lignes)
  │   ├── eu_cycle.py             (~250 lignes)
  │   ├── us_cycle.py             (~250 lignes)
  │   ├── futures_cycle.py        (~200 lignes)
  │   ├── risk_cycle.py           (~200 lignes — V10 snapshots, ERE, correlation)
  │   ├── regime_cycle.py         (~150 lignes — V12 regime detection)
  │   ├── ror_cycle.py            (~100 lignes — Monte Carlo daily)
  │   ├── rebalance_cycle.py      (~150 lignes — HRP + Kelly)
  │   ├── reconciliation_cycle.py (~150 lignes)
  │   └── eod_cycle.py            (~100 lignes — cleanup, orphan detection)
  └── health.py                   (~50 lignes — heartbeat, health endpoint)
```

**Contraintes** :
- AUCUN changement de comportement. Pure extraction.
- Chaque cycle doit être testable indépendamment (injection de dépendances broker/risk/state).
- Le scheduler dans worker.py reste le seul point d'entrée.
- Les cycles partagent l'état via un objet `WorkerState` passé en paramètre (pas de globals).
- Chaque cycle garde son timing actuel (30s tick, 15min crypto, etc.).

**Méthode** :
1. Lire worker.py intégralement, identifier les blocs fonctionnels
2. Extraire chaque bloc dans son fichier SANS modifier la logique
3. Créer les imports dans worker.py vers les nouveaux modules
4. Exécuter la suite de tests complète (2998 tests doivent tous passer)
5. Vérifier que le worker démarre et exécute un cycle complet en mode test

**Critère de validation** :
- `worker.py` < 300 lignes
- 2998 tests passent
- Aucun nouveau bug introduit
- Chaque cycle peut être importé et testé isolément

### Tâche C1-02 : Découper routes_v2.py (P1)

**Agent** : SURGEON
**Estimation** : 8h
**Fichier source** : `dashboard/api/routes_v2.py` (2602 lignes)

**Structure cible** :
```
dashboard/api/
  ├── routes_v2.py               (~100 lignes — router principal, imports)
  ├── routes/
  │   ├── __init__.py
  │   ├── portfolio.py           (endpoints portfolio/NAV/positions)
  │   ├── strategies.py          (endpoints stratégies/performance)
  │   ├── risk.py                (endpoints risk/DD/VaR/correlation)
  │   ├── regime.py              (endpoints regime/activation matrix)
  │   ├── trades.py              (endpoints trades/journal/history)
  │   ├── system.py              (endpoints health/status/config)
  │   ├── live.py                (endpoints live-specific — V10 /api/live/v2/*)
  │   └── crypto.py              (endpoints crypto-specific)
```

**Contraintes** :
- Chaque fichier de routes = 1 blueprint/router Flask ou FastAPI
- Les 51 endpoints existants doivent tous répondre identiquement
- Tests des endpoints existants doivent passer

### Tâche C1-03 : Découper paper_portfolio.py (P2)

**Agent** : SURGEON
**Estimation** : 4h
**Fichier source** : `scripts/paper_portfolio.py` (1670 lignes)

**Diagnostic** : Script monolithique de gestion du paper trading. Probablement un vestige d'avant le worker. À découper ou à marquer comme deprecated si le worker gère déjà le paper.

**Action** :
1. Vérifier si `paper_portfolio.py` est encore utilisé (appelé par le worker ? par un cron ? manuellement ?)
2. Si non utilisé → déplacer dans `archive/scripts/`
3. Si utilisé → extraire la logique réutilisable dans `core/`, supprimer le reste

### Tâche C1-04 : Audit main.py dashboard (P2)

**Agent** : SURGEON
**Estimation** : 3h
**Fichier source** : `dashboard/api/main.py` (1178 lignes)

**Diagnostic** : Probablement de la config, middleware, et du setup d'app mélangés. Découper en `main.py` (startup), `middleware.py`, `config.py`.

---

## C2 — DEAD CODE & FICHIERS ORPHELINS (Agent: GRAVEDIGGER)

**Priorité globale : P0**
**Justification** : Le dead code crée de la confusion pour les agents autonomes. Si un agent Claude Code cherche "comment fonctionne le backtest", il peut tomber sur `intraday-backtesterV2/` (mort) au lieu de `core/backtest/` (vivant). 112MB d'archive dans le repo ralentit les clones et les CI.

### Tâche C2-01 : Supprimer intraday-backtesterV2/ (P0)

**Agent** : GRAVEDIGGER
**Estimation** : 1h

**Diagnostic** : 4 fichiers (~4000 lignes), remplacé par `core/backtest/engine.py` (BacktesterV2 event-driven). Les fichiers :
- `run_eu_phase2_p1p2.py` (1141 lignes)
- `run_eu_phase2_p2p3.py` (1110 lignes)
- `run_eu_phase2.py` (882 lignes)
- `run_p1_strategies.py` (880 lignes)

**Action** :
1. Vérifier qu'aucun import ne pointe vers `intraday-backtesterV2/`
2. `git rm -r intraday-backtesterV2/`
3. Ajouter au `.gitignore` si nécessaire

### Tâche C2-02 : Audit strategies/ vs strategies_v2/ (P0)

**Agent** : GRAVEDIGGER
**Estimation** : 2h

**Diagnostic** : Deux répertoires de stratégies. `strategies_v2/` est le répertoire actif (29 fichiers documentés dans V12.5). `strategies/` est probablement l'ancien répertoire.

**Action** :
1. Lister tous les fichiers dans `strategies/`
2. Pour chaque fichier, vérifier s'il est importé quelque part dans le projet
3. Si importé → c'est un problème de migration incomplète, à résoudre
4. Si non importé → `git rm -r strategies/` (l'historique reste dans git)

### Tâche C2-03 : Nettoyer les fichiers racine orphelins (P0)

**Agent** : GRAVEDIGGER
**Estimation** : 1h

**Fichiers suspects à la racine** :
```
run.py              — probablement remplacé par worker.py
dashboard.py        — probablement remplacé par dashboard/api/main.py
Procfile            — Railway deploy (encore utilisé ? VPS Hetzner maintenant)
railway             — idem
conftest.py         — légitime (pytest config), mais vérifier le contenu
SYNTHESE_COMPLETE   — fichier doc, pas du code, à déplacer dans docs/
CLAUDE              — config Claude Code, légitime
```

**Action pour chaque fichier** :
1. Vérifier s'il est référencé/importé/utilisé
2. Si non → `archive/` ou `git rm`
3. `run.py` et `dashboard.py` : vérifier s'ils sont dans les services systemd (si oui, les remplacer par des wrappers qui appellent le vrai code)

### Tâche C2-04 : Gérer archive/ (P1)

**Agent** : GRAVEDIGGER
**Estimation** : 2h

**Diagnostic** : 112MB. Contient les strats rejetées (CLEAN-001, 9 stratégies), anciens backtests, probablement des data dumps.

**Action** :
1. Lister le contenu de `archive/`
2. Si uniquement du code Python archivé → acceptable, mais ajouter un README.md expliquant le contenu
3. Si contient des data (CSV, Parquet, pickle) → sortir du repo git, stocker sur Hetzner StorageBox
4. Ajouter `archive/data/` au `.gitignore` si des data y sont versionnées
5. Vérifier que `archive/rejected/` correspond aux 9 strats documentées dans le V12.5

### Tâche C2-05 : Purger __pycache__ et fichiers générés (P0)

**Agent** : GRAVEDIGGER
**Estimation** : 30min

**Action** :
```bash
# Supprimer tous les __pycache__
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null

# Supprimer les .pyc
find . -name "*.pyc" -delete

# Vérifier .gitignore
# Doit contenir :
__pycache__/
*.pyc
*.pyo
.pytest_cache/
*.egg-info/
dist/
build/
```

### Tâche C2-06 : Dead code intra-fichier (P1)

**Agent** : GRAVEDIGGER
**Estimation** : 6h

**Méthode** :
```bash
# Installer vulture (détecteur de dead code Python)
pip install vulture

# Scan du repo
vulture core/ strategies_v2/ worker.py dashboard/ --min-confidence 80

# Vulture va identifier :
# - Fonctions jamais appelées
# - Variables jamais utilisées
# - Imports inutilisés
# - Classes jamais instanciées
```

**Action** :
1. Run vulture sur tout le repo
2. Trier les résultats par module
3. Pour chaque dead code identifié :
   - Si c'est un faux positif (appelé dynamiquement, via getattr, etc.) → whitelist
   - Si c'est du vrai dead code → supprimer
4. Re-run les 2998 tests après chaque batch de suppressions

**Attention** : Le trading platform utilise probablement beaucoup de dispatch dynamique (strategy registry, cycle scheduling). Vulture aura des faux positifs. Vérifier manuellement chaque suppression.

---

## C3 — TYPE HINTS & SIGNATURES (Agent: TYPIST)

**Priorité globale : P1**
**Justification** : 193K lignes de Python probablement sans (ou avec peu de) type hints. Les type hints sont le premier rempart contre les bugs de type runtime — surtout critique quand des prix, des quantités et des pourcentages circulent entre 130 modules.

### Tâche C3-01 : Type hints sur les modules critiques (P0)

**Agent** : TYPIST
**Estimation** : 12h

**Fichiers prioritaires** (ceux qui manipulent de l'argent réel) :
```
core/risk_manager.py              (1058 lignes)
core/risk_manager_live.py         (966 lignes)
core/trading_engine.py            (982 lignes)
core/broker/ibkr_bracket.py       (1387 lignes)
core/crypto/risk_manager_crypto.py (1178 lignes)
core/crypto/backtest_engine.py    (1097 lignes)
core/var_live.py                  (752 lignes)
core/walk_forward_framework.py    (761 lignes)
core/trade_journal.py             (875 lignes)
```

**Standard de typage** :
```python
# AVANT (probable état actuel)
def calculate_position_size(capital, risk_pct, entry, stop):
    size = capital * risk_pct / abs(entry - stop)
    return size

# APRÈS
from decimal import Decimal
from typing import Optional

def calculate_position_size(
    capital: float,
    risk_pct: float,
    entry: float,
    stop: float,
    max_size: Optional[float] = None,
) -> float:
    """Calculate position size based on risk percentage and stop distance.
    
    Args:
        capital: Available capital in base currency.
        risk_pct: Risk per trade as decimal (0.01 = 1%).
        entry: Entry price.
        stop: Stop-loss price.
        max_size: Optional maximum position size cap.
    
    Returns:
        Position size in units of the instrument.
    
    Raises:
        ValueError: If entry equals stop (division by zero risk).
    """
    if entry == stop:
        raise ValueError(f"Entry ({entry}) cannot equal stop ({stop})")
    size = capital * risk_pct / abs(entry - stop)
    if max_size is not None:
        size = min(size, max_size)
    return size
```

**Règles** :
- Toutes les fonctions publiques doivent avoir des type hints sur les paramètres ET le retour
- Les fonctions privées (_underscore) : au minimum le retour
- Utiliser `float` pour les prix/quantités (pas `Decimal` — trop de refactoring, le codebase est déjà en float)
- `Optional[X]` pour tout paramètre qui peut être None
- Les dictionnaires de config/state : `TypedDict` ou `dataclass` (pas `dict[str, Any]`)

### Tâche C3-02 : Dataclasses pour les structures de données (P1)

**Agent** : TYPIST
**Estimation** : 8h

**Diagnostic probable** : Les positions, trades, ordres, et signaux sont probablement des `dict`. Ça veut dire zéro validation de structure, des KeyError runtime possibles, et de l'autocomplétion cassée.

**Structures à créer** :
```python
# core/models/position.py
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

class Side(Enum):
    LONG = "LONG"
    SHORT = "SHORT"

class PositionStatus(Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    PENDING = "PENDING"

@dataclass
class Position:
    ticker: str
    side: Side
    quantity: float
    entry_price: float
    entry_time: datetime
    strategy: str
    broker: str
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None
    current_price: Optional[float] = None
    unrealized_pnl: float = 0.0
    status: PositionStatus = PositionStatus.OPEN

@dataclass
class Trade:
    ticker: str
    side: Side
    quantity: float
    entry_price: float
    exit_price: float
    entry_time: datetime
    exit_time: datetime
    pnl: float
    commission: float
    slippage: float
    strategy: str
    broker: str

@dataclass
class Signal:
    strategy: str
    ticker: str
    side: Side
    strength: float  # 0.0 to 1.0
    timestamp: datetime
    regime: str = "UNKNOWN"
    confluence: int = 1  # nombre de signaux convergents

@dataclass
class Order:
    ticker: str
    side: Side
    quantity: float
    order_type: str  # "MARKET", "LIMIT", "PEGGED_MID"
    price: Optional[float] = None
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None
    status: str = "PENDING"
    broker_order_id: Optional[str] = None
```

**Migration** :
- Créer les dataclasses dans `core/models/`
- Ne PAS migrer tout le codebase d'un coup
- Commencer par les interfaces broker (là où les erreurs de structure sont les plus dangereuses)
- Ajouter des `@classmethod from_dict(cls, d: dict)` pour la compatibilité avec le code existant
- Migrer progressivement, module par module

### Tâche C3-03 : Configuration mypy (P1)

**Agent** : TYPIST
**Estimation** : 2h

**Action** :
```toml
# pyproject.toml (ou mypy.ini)
[tool.mypy]
python_version = "3.14"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = false  # Progressif — pas tout d'un coup
check_untyped_defs = true

# Strict sur les modules critiques
[[tool.mypy.overrides]]
module = [
    "core.risk_manager",
    "core.risk_manager_live",
    "core.trading_engine",
    "core.broker.ibkr_bracket",
    "core.crypto.risk_manager_crypto",
]
disallow_untyped_defs = true
```

**Intégration CI** :
- Ajouter `mypy core/risk_manager.py core/trading_engine.py` au pipeline GitHub Actions
- Progressivement ajouter des modules à la liste strict

---

## C4 — LINTING & CONVENTIONS (Agent: LINTER)

**Priorité globale : P1**
**Justification** : 193K lignes sans linter unifié = styles incohérents, imports désordonnés, violations PEP8 partout. Ruff est 100x plus rapide que flake8 et inclut isort + pyupgrade.

### Tâche C4-01 : Setup ruff + premier passage (P1)

**Agent** : LINTER
**Estimation** : 4h

**Configuration** :
```toml
# pyproject.toml
[tool.ruff]
target-version = "py312"
line-length = 100
src = ["core", "strategies_v2", "dashboard", "scripts", "tests"]

[tool.ruff.lint]
select = [
    "E",    # pycodestyle errors
    "W",    # pycodestyle warnings
    "F",    # pyflakes
    "I",    # isort
    "UP",   # pyupgrade
    "B",    # flake8-bugbear
    "SIM",  # flake8-simplify
    "RUF",  # ruff-specific rules
]
ignore = [
    "E501",   # line too long — on le gère avec line-length
    "B008",   # function call in default argument — faux positifs fréquents
]

[tool.ruff.lint.isort]
known-first-party = ["core", "strategies_v2", "dashboard"]

[tool.ruff.format]
quote-style = "double"
```

**Méthode** :
1. Installer ruff : `pip install ruff`
2. Premier scan : `ruff check . --statistics` → voir l'ampleur
3. Auto-fix les imports : `ruff check . --select I --fix`
4. Auto-fix les upgrades Python : `ruff check . --select UP --fix`
5. Auto-fix les simplifications : `ruff check . --select SIM --fix`
6. Vérifier que les 2998 tests passent après chaque batch
7. Les erreurs restantes (bugbear, pyflakes) → fix manuelles

**Intégration CI** :
```yaml
# .github/workflows/lint.yml
- name: Ruff lint
  run: ruff check .
- name: Ruff format check
  run: ruff format --check .
```

### Tâche C4-02 : Nettoyage imports (P1)

**Agent** : LINTER
**Estimation** : 3h

**Diagnostic probable** : Avec 130+ modules, les imports sont probablement un spaghetti de chemins relatifs et absolus, avec des `import *` et des imports circulaires potentiels.

**Action** :
1. `ruff check . --select F401` → imports inutilisés
2. `ruff check . --select F811` → re-définitions de noms
3. Chercher les `import *` : `grep -rn "from.*import \*" core/ strategies_v2/`
4. Chercher les imports circulaires : utiliser `pydeps` ou `import-linter`
5. Standardiser : imports absolus partout (`from core.risk_manager import ...` pas `from ..risk_manager import ...`)

### Tâche C4-03 : Docstrings sur les modules publics (P2)

**Agent** : LINTER
**Estimation** : 8h

**Standard** :
```python
"""Module description — une ligne.

Description plus détaillée si nécessaire.
Explique le rôle du module dans l'architecture globale.
"""
```

**Priorité de documentation** :
1. Modules core/risk/ (les plus critiques — gèrent l'argent réel)
2. Modules core/broker/ (interfaces avec les brokers)
3. Modules core/crypto/ (logique spécifique Binance France)
4. worker.py et ses cycles (après découpage C1-01)

---

## C5 — TESTS MANQUANTS (Agent: TESTER)

**Priorité globale : P1**
**Justification** : 2998 tests c'est excellent en volume, mais le CRO V12.5 a flaggé 2 fichiers sans tests (bot_service.py, preflight_check.py) et le strategy_registry n'est pas source unique. Les modules les plus critiques (worker.py, risk_manager_live.py) ont-ils une couverture suffisante ?

### Tâche C5-01 : Tests pour bot_service.py (P1)

**Agent** : TESTER
**Estimation** : 4h
**Fichier** : `core/telegram/bot_service.py` (771 lignes)

**Spécification** :
```
Tests à écrire :

1. Chaque commande Telegram (/status, /pnl, /risk, /kill, /emergency, /regime, etc.)
   - Input valide → réponse correcte
   - Input invalide → message d'erreur propre, pas de crash
   - Permissions : /emergency nécessite TOTP valide

2. Kill chain :
   - /kill CONFIRM → active 2 KS (IBKR + crypto) + EmergencyCloseAll
   - /emergency avec kill_switch_callback
   - Vérifier que les kills sont persistés

3. Edge cases :
   - Bot appelé quand worker est down
   - Commande pendant un cycle de trading
   - Commande avec caractères spéciaux / injection

Cible : 30+ tests
```

### Tâche C5-02 : Tests pour preflight_check.py (P1)

**Agent** : TESTER
**Estimation** : 3h

**Spécification** :
```
14 checks preflight documentés dans V12.5. Chaque check doit avoir :
  - Test PASS (conditions normales)
  - Test FAIL (condition violée → alerte correcte)
  - Test mode dégradé (retry échoue → continue, documenté comme design choice)

Cible : 28+ tests (14 checks × 2 cas minimum)
```

### Tâche C5-03 : Coverage report (P1)

**Agent** : TESTER
**Estimation** : 2h

**Action** :
```bash
# Installer coverage
pip install coverage pytest-cov

# Run avec coverage
pytest --cov=core --cov=strategies_v2 --cov-report=html --cov-report=term-missing

# Identifier les modules < 50% de couverture
# Prioriser : risk > execution > broker > strategies > scripts
```

**Cible** :
- Modules `core/risk/` : > 80% coverage
- Modules `core/broker/` : > 70% coverage
- Modules `core/crypto/` : > 70% coverage
- `worker.py` (post-découpage) : > 60% coverage
- Global : > 60% (réaliste vu la taille du codebase)

### Tâche C5-04 : Tests d'intégration worker cycles (P2)

**Agent** : TESTER
**Estimation** : 6h
**Dépendance** : C1-01 (après découpage worker.py)

**Spécification** :
```
Après découpage du worker en cycles isolés :

Pour chaque cycle (crypto, fx, eu, us, futures, risk, regime, ror, rebalance, reconciliation, eod) :
  1. Test avec mock broker → le cycle s'exécute sans crash
  2. Test avec données edge case (prix 0, spread infini, timeout broker)
  3. Test avec kill switch actif → le cycle respecte le kill switch
  4. Test avec regime PANIC → le cycle applique l'activation matrix

Cible : 50+ tests d'intégration
```

---

## C6 — REFACTOR STRUCTUREL (Agent: ARCHITECT)

**Priorité globale : P1**
**Justification** : Le CRO V12.5 a flaggé que `strategy_registry.py` n'est pas la source unique d'exécution (3 sources). C'est un risque de divergence : une strat peut être active dans le registry mais pas dans le worker, ou inversement.

### Tâche C6-01 : Single Source of Truth pour les stratégies (P0)

**Agent** : ARCHITECT
**Estimation** : 6h

**Diagnostic** : 3 sources d'exécution des stratégies (flaggé CRO V12.5). Probablement :
1. `strategy_registry.py` — le registre YAML/Python
2. Les imports directs dans `worker.py` — hardcodés
3. Les configs dans `config/` — YAML séparés

**Action** :
1. Identifier les 3 sources exactes
2. Désigner `strategy_registry.py` comme source unique
3. Le worker doit lire UNIQUEMENT le registry pour savoir quelles strats exécuter
4. Les configs YAML alimentent le registry, pas le worker directement
5. Ajouter un test qui vérifie que toutes les strats dans le worker sont dans le registry et vice-versa

### Tâche C6-02 : Unifier risk_manager.py et risk_manager_live.py (P1)

**Agent** : ARCHITECT
**Estimation** : 6h

**Diagnostic** : `core/risk_manager.py` (1058 lignes) et `core/risk_manager_live.py` (966 lignes) — deux risk managers. Probablement un pour le backtest et un pour le live. Mais avec 2024 lignes combinées et des responsabilités qui se chevauchent, c'est un risque de divergence.

**Action** :
1. Analyser les deux fichiers : quelles méthodes sont communes, lesquelles sont spécifiques
2. Extraire l'interface commune dans une classe abstraite `BaseRiskManager`
3. `BacktestRiskManager(BaseRiskManager)` — pour le backtester
4. `LiveRiskManager(BaseRiskManager)` — pour le worker live
5. Les 12 checks pre-trade doivent être dans la base (DRY)
6. Les circuit breakers live-only restent dans `LiveRiskManager`

### Tâche C6-03 : Audit des scripts/ one-shot (P2)

**Agent** : ARCHITECT
**Estimation** : 3h

**Fichiers** :
```
scripts/wf_eu_all.py        (1424 lignes)
scripts/paper_portfolio.py   (1670 lignes)
scripts/paper_portfolio_eu.py (1235 lignes)
scripts/wf_fx_all.py         (930 lignes)
scripts/wf_crypto_all.py     (853 lignes)
scripts/weekly_walk_forward.py (824 lignes)
```

**Diagnostic** : Scripts de walk-forward et paper portfolio probablement avec beaucoup de code dupliqué entre eux.

**Action** :
1. Comparer `wf_eu_all.py`, `wf_fx_all.py`, `wf_crypto_all.py` — extraire le pattern commun dans un `scripts/wf_runner.py` paramétrisable
2. `paper_portfolio.py` et `paper_portfolio_eu.py` — fusionner si possible, ou déplacer dans `archive/` si remplacés par le worker
3. Objectif : chaque script WF < 200 lignes (appel du framework commun avec config spécifique)

---

## C7 — ARBORESCENCE & HYGIÈNE (Agent: JANITOR)

**Priorité globale : P1**

### Tâche C7-01 : Déplacer les fichiers d'état de la racine (P1)

**Agent** : JANITOR
**Estimation** : 2h

**Fichiers** :
```
paper_vrp_state        → data/state/paper_vrp_state.json
paper_pairs_state      → data/state/paper_pairs_state.json
paper_momentum_state   → data/state/paper_momentum_state.json
paper_trading_state    → data/state/paper_trading_state.json
paper_portfolio_state  → data/state/paper_portfolio_state.json
```

**Action** :
1. Créer `data/state/`
2. Déplacer chaque fichier
3. `grep -rn "paper_vrp_state\|paper_pairs_state\|paper_momentum_state\|paper_trading_state\|paper_portfolio_state" core/ worker.py strategies_v2/` → mettre à jour tous les chemins
4. Ajouter `data/state/*.json` au `.gitignore` (les fichiers d'état ne doivent PAS être versionnés — ils changent à chaque trade)
5. Tester que le worker démarre correctement avec les nouveaux chemins

### Tâche C7-02 : Nettoyer .gitignore (P1)

**Agent** : JANITOR
**Estimation** : 30min

**Le .gitignore devrait contenir** :
```gitignore
# Python
__pycache__/
*.pyc
*.pyo
*.egg-info/
dist/
build/

# Environment
.env
venv/
.venv/

# IDE
.vscode/
.idea/
*.swp

# State files (generated at runtime)
data/state/
data/*.json
data/*.jsonl

# Logs
logs/
*.log

# Data cache
data_cache/

# Test
.pytest_cache/
htmlcov/
.coverage

# Archives data (too large for git)
archive/data/
archive/*.csv
archive/*.parquet

# OS
.DS_Store
Thumbs.db
```

### Tâche C7-03 : Structure de dossiers propre (P2)

**Agent** : JANITOR
**Estimation** : 2h

**Structure cible (post-nettoyage)** :
```
tplatform/
  ├── worker.py                    (< 300 lignes, orchestrateur)
  ├── conftest.py                  (pytest config)
  ├── pyproject.toml               (deps, ruff, mypy, pytest config)
  ├── .env                         (secrets, gitignored)
  ├── .gitignore
  ├── CLAUDE                       (Claude Code config)
  ├── .claude/                     (Claude Code skills)
  │
  ├── core/                        (logique métier)
  │   ├── worker/                  (cycles extraits de worker.py)
  │   ├── models/                  (dataclasses Position, Trade, Signal, Order)
  │   ├── risk/                    (risk managers, kill switches, VaR)
  │   ├── broker/                  (IBKR, Binance, Alpaca adapters)
  │   ├── crypto/                  (Binance France spécifique)
  │   ├── execution/               (smart router, fills, slippage)
  │   ├── alloc/                   (HRP, Kelly, dynamic allocator)
  │   ├── regime/                  (V12 regime engine)
  │   ├── backtest/                (BacktesterV2)
  │   ├── validation/              (shadow logger, fidelity, live tracker)
  │   ├── tax/                     (trade classifier, FR compliance)
  │   ├── telegram/                (bot service, commands)
  │   ├── portfolio/               (portfolio state, unified view)
  │   ├── data/                    (data quality, resync, DST, sessions)
  │   └── monitoring/              (alerting, health, snapshots)
  │
  ├── strategies_v2/               (29 stratégies actives)
  ├── config/                      (YAML configs, regime.yaml, etc.)
  ├── dashboard/                   (API + frontend)
  ├── tests/                       (116 fichiers test)
  ├── scripts/                     (scripts utilitaires, WF runners)
  ├── docs/                        (documentation, ADR, playbooks)
  ├── data/                        (runtime data, gitignored)
  │   ├── state/                   (fichiers d'état JSON)
  │   ├── risk/                    (rapports MC, stress tests)
  │   └── logs/                    (trade logs, JSONL)
  │
  └── archive/                     (code archivé, référence)
      ├── rejected/                (9 strats rejetées)
      └── scripts/                 (anciens scripts)

SUPPRIMÉS :
  ✗ strategies/                    (remplacé par strategies_v2/)
  ✗ intraday-backtesterV2/         (remplacé par core/backtest/)
  ✗ run.py                         (remplacé par worker.py)
  ✗ dashboard.py                   (remplacé par dashboard/api/)
  ✗ paper_*_state                  (déplacés dans data/state/)
  ✗ Procfile, railway              (plus utilisés — Hetzner VPS)
```

---

## SÉQUENÇAGE

### Phase 0 — Sécurité immédiate (1-2 jours)

```
C2-01  Supprimer intraday-backtesterV2/    GRAVEDIGGER  1h
C2-02  Audit strategies/ vs strategies_v2/ GRAVEDIGGER  2h
C2-03  Nettoyer fichiers racine orphelins  GRAVEDIGGER  1h
C2-05  Purger __pycache__                  GRAVEDIGGER  30min
C7-02  Nettoyer .gitignore                 JANITOR      30min
                                            TOTAL:       5h
```

### Phase 1 — God files & structure (1-2 semaines)

```
C1-01  Découper worker.py                  SURGEON      12h
C6-01  Single source of truth strats       ARCHITECT    6h
C7-01  Déplacer fichiers d'état racine     JANITOR      2h
C4-01  Setup ruff + premier passage        LINTER       4h
C4-02  Nettoyage imports                   LINTER       3h
                                            TOTAL:       27h
```

### Phase 2 — Qualité & couverture (2-4 semaines)

```
C1-02  Découper routes_v2.py               SURGEON      8h
C3-01  Type hints modules critiques        TYPIST       12h
C3-02  Dataclasses structures données      TYPIST       8h
C3-03  Config mypy                         TYPIST       2h
C5-01  Tests bot_service.py                TESTER       4h
C5-02  Tests preflight_check.py            TESTER       3h
C5-03  Coverage report                     TESTER       2h
C6-02  Unifier risk managers               ARCHITECT    6h
C2-06  Dead code intra-fichier (vulture)   GRAVEDIGGER  6h
C2-04  Gérer archive/ 112MB               GRAVEDIGGER  2h
                                            TOTAL:       53h
```

### Phase 3 — Polish (ongoing)

```
C1-03  Découper paper_portfolio.py         SURGEON      4h
C1-04  Audit main.py dashboard             SURGEON      3h
C4-03  Docstrings modules publics          LINTER       8h
C5-04  Tests intégration worker cycles     TESTER       6h
C6-03  Audit scripts/ one-shot            ARCHITECT    3h
C7-03  Structure dossiers finale           JANITOR      2h
                                            TOTAL:       26h
```

---

## RÈGLES DE NETTOYAGE

### Règle #1 : Ne JAMAIS changer le comportement
Chaque tâche est un refactoring pur. Aucun changement de logique trading, de seuils risk, de timing de cycles. Si une extraction de code change un comportement, c'est un bug du refactoring.

### Règle #2 : Tests d'abord, refactoring ensuite
Avant de toucher un fichier, vérifier qu'il a des tests. Si non, écrire les tests AVANT de refactorer. Le refactoring sans tests est un suicide.

### Règle #3 : Commits atomiques
Un commit = une tâche = un changement logique. Pas de méga-commits qui mélangent "fix import + rename file + add type hints + delete dead code".

### Règle #4 : Le worker ne doit JAMAIS être down
Pendant le découpage de worker.py (C1-01), le worker live continue de tourner. Le nouveau code est testé en staging (paper gateway port 4003) avant de remplacer le worker live.

### Règle #5 : 2998 tests = plancher
À aucun moment les tests ne doivent diminuer. Chaque suppression de dead code qui supprime un test doit être compensée par la suppression du code correspondant (sinon c'est du code vivant, pas du dead code).

---

**Total estimé : ~111 heures de développement**
**7 domaines, 7 agents, 24 tâches**
**Objectif : codebase propre, typée, testée, prête pour les agents autonomes**

**Document généré le 3 Avril 2026**
**Prochaine revue : après Phase 0 + Phase 1**
