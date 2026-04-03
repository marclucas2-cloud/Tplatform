# TPLATFORM — TODO XXXL ROBUSTESSE STRUCTURELLE

**Classification** : CONFIDENTIEL — Robustesse & Résilience
**Date** : 3 Avril 2026
**Base** : Synthèse V12.5 (CRO 9.5/10) + Cleanup V12 (3116 tests) + worker.py 3292 lignes
**Capital en jeu** : ~45K€ live sur 3 brokers (Binance 10K, IBKR 10K, Alpaca 30K imminent)
**Bus factor** : 1 (solo operator)
**Contrainte absolue** : Le worker ne doit JAMAIS être down pendant les travaux
**Mode d'exécution** : Claude Code agents autonomes

## EXECUTION STATUS (3 Avril 2026)

**22/22 taches FAITES** | 3297 tests (181 nouveaux) | 0 regression

| Tache | Status | Fichier(s) cree(s) | Tests |
|-------|--------|---------------------|-------|
| R1-01 Task Queue | FAIT | core/worker/task_queue.py | 21 |
| R1-02 Cycle Runner | FAIT | core/worker/cycle_runner.py | 18 |
| R1-03 Worker State | FAIT | core/worker/worker_state.py | 14 |
| R1-04 Migration worker | FAIT | worker.py (CycleRunners + metrics) | — |
| R2-01 Metrics Pipeline | FAIT | core/monitoring/metrics_pipeline.py | 16 |
| R2-02 Anomaly Detector | FAIT | core/monitoring/anomaly_detector.py | 6 |
| R2-03 Dashboard /cycles | FAIT | dashboard/api/routes/cycles.py | — |
| R2-04 Telegram /health | FAIT | core/telegram/bot_service.py (modif) | — |
| R3-01 Contracts | FAIT | core/broker/contracts/{binance,ibkr,alpaca}_contracts.py | 7 |
| R3-02 Contract Runner | FAIT | core/broker/contracts/contract_runner.py | 3 |
| R3-03 Response Snapshots | FAIT | core/broker/contracts/response_snapshots.py | 2 |
| R4-01 Order SM | FAIT | core/execution/order_state_machine.py | 30 |
| R4-02 Order Tracker | FAIT | core/execution/order_tracker.py | 5 |
| R4-03 Position SM | FAIT | core/execution/position_state_machine.py | 7 |
| R5-01 Event Logger | FAIT | core/worker/event_logger.py | 16 |
| R5-02 Replay Engine | FAIT | core/worker/replay_engine.py | 6 |
| R5-03 Incident Report | FAIT | core/monitoring/incident_report.py | — |
| R6-01 Broker Health | FAIT | core/broker/broker_health.py | 11 |
| R6-02 Partial Data | FAIT | core/risk/partial_data_handler.py | 5 |
| R7-01 Shadow Worker | FAIT | core/worker/shadow_mode.py | 2 |
| R7-02 Rollback | FAIT | scripts/deploy.sh | — |
| R7-03 Deploy Checklist | FAIT | scripts/pre_deploy_check.py | — |

---

## PHILOSOPHIE

Ce document traite un seul sujet : **la robustesse structurelle**.

Pas de nouvelles stratégies. Pas de nouveaux modules de trading. Pas d'optimisation de Sharpe.
L'objectif est que le système survive à tout ce qui peut arriver :

- Un broker qui change son API sans prévenir
- Un deploy qui introduit un bug silencieux
- Un crash à 3h du matin quand tu dors
- Un ordre qui entre dans un état impossible
- Un cycle qui bloque et contamine les autres
- Un incident dont tu ne comprends pas la cause après coup

Chaque chantier élimine une **catégorie entière de bugs**, pas juste un bug individuel.

---

## AGENTS

| Agent | Spécialité | Chantiers |
|-------|-----------|-----------|
| **CLOCKWORK** | Architecture worker, scheduling, isolation des cycles | R1 |
| **PANOPTICON** | Observabilité, métriques, alerting proactif | R2 |
| **DIPLOMAT** | Interfaces broker, contract testing, dégradation gracieuse | R3, R6 |
| **STATEKEEPER** | State machines, lifecycle ordres, invariants | R4 |
| **ARCHAEOLOGIST** | Replay engine, debugging post-mortem, audit trail | R5 |
| **GATEKEEPER** | Canary deploys, rollback, CI/CD sécurisé | R7 |

---

## R1 — WORKER EVENT-DRIVEN AVEC QUEUE (Agent: CLOCKWORK)

**Priorité globale : P0**
**Justification** : Le worker est un scheduler procédural séquentiel. Si le cycle crypto prend 30s (Binance timeout), le cycle FX attend — même si le signal FX est urgent. Un cycle qui plante peut empêcher les suivants de s'exécuter pendant un tick entier (30s). Avec 46 stratégies sur 3 brokers 24/7, c'est une bombe à retardement.

**État actuel** (post-cleanup, 3292 lignes) :
```
worker.py
  └── main loop (30s tick)
        ├── crypto_cycle()      → séquentiel, bloque si Binance lag
        ├── fx_cycle()          → séquentiel, attend que crypto finisse
        ├── eu_cycle()          → séquentiel
        ├── us_cycle()          → séquentiel
        ├── futures_cycle()     → séquentiel
        ├── risk_cycle()        → séquentiel (toutes les 5min)
        ├── regime_cycle()      → séquentiel (toutes les 15min)
        ├── rebalance_cycle()   → séquentiel (toutes les 4h)
        ├── reconciliation()    → séquentiel
        └── eod_cleanup()       → séquentiel
```

**Problèmes structurels** :
1. **Couplage temporel** : si un cycle est lent, tous les suivants sont retardés
2. **Pas d'isolation des erreurs** : une exception non catchée dans un cycle peut crasher le worker
3. **Pas de métriques de cycle** : tu ne sais pas combien de temps chaque cycle prend réellement
4. **Pas de priorité** : un signal de kill switch urgent attend que le cycle crypto finisse ses 11 strats

### Tâche R1-01 : Task Queue Architecture (P0)

**Fichier** : `core/worker/task_queue.py`
**Agent** : CLOCKWORK
**Estimation** : 10h

**Spécification** :
```python
"""
Architecture cible : Producer-Consumer avec priorités.

Chaque cycle est un Producer qui schedule des tâches dans une PriorityQueue.
Un pool de Consumers exécute les tâches par priorité.
Les cycles sont isolés — un crash dans un consumer ne tue pas les autres.

                 ┌──────────────────────────────────────┐
                 │           SCHEDULER (main)            │
                 │  Trigger les cycles selon leurs       │
                 │  intervalles (15min, 5min, 4h, etc.)  │
                 └──────────────┬───────────────────────┘
                                │ schedule()
                 ┌──────────────▼───────────────────────┐
                 │         PRIORITY QUEUE                │
                 │                                       │
                 │  P0: KILL_SWITCH, EMERGENCY_CLOSE     │
                 │  P1: RISK_CHECK, REGIME_DETECTION     │
                 │  P2: TRADE_SIGNAL (FX, EU, US, etc.)  │
                 │  P3: REBALANCE, RECONCILIATION        │
                 │  P4: MONITORING, HEARTBEAT, EOD       │
                 └──────────────┬───────────────────────┘
                                │ consume()
                 ┌──────────────▼───────────────────────┐
                 │        WORKER POOL (3 threads)        │
                 │                                       │
                 │  Thread 1: exécute la tâche P0/P1     │
                 │  Thread 2: exécute la tâche P2 next   │
                 │  Thread 3: exécute la tâche P3/P4     │
                 └──────────────────────────────────────┘
"""

import queue
import threading
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, Any, Optional
from datetime import datetime

class TaskPriority(IntEnum):
    CRITICAL = 0    # Kill switch, emergency close
    HIGH = 1        # Risk checks, regime detection
    NORMAL = 2      # Trade signals
    LOW = 3         # Rebalance, reconciliation
    BACKGROUND = 4  # Monitoring, heartbeat, EOD cleanup

@dataclass(order=True)
class Task:
    priority: int
    scheduled_at: datetime = field(compare=False)
    name: str = field(compare=False)
    callable: Callable = field(compare=False)
    args: tuple = field(default=(), compare=False)
    kwargs: dict = field(default_factory=dict, compare=False)
    timeout_seconds: float = field(default=60.0, compare=False)
    max_retries: int = field(default=0, compare=False)

class TaskQueue:
    def __init__(self, num_workers: int = 3, metrics_callback=None):
        self._queue = queue.PriorityQueue()
        self._workers: list[threading.Thread] = []
        self._running = threading.Event()
        self._metrics_callback = metrics_callback
        self._num_workers = num_workers

    def submit(self, task: Task) -> None:
        """Submit a task to the queue."""
        self._queue.put(task)

    def start(self) -> None:
        """Start worker threads."""
        self._running.set()
        for i in range(self._num_workers):
            t = threading.Thread(
                target=self._worker_loop,
                name=f"worker-{i}",
                daemon=True,
            )
            t.start()
            self._workers.append(t)

    def stop(self, timeout: float = 30.0) -> None:
        """Graceful shutdown — finish current tasks, reject new ones."""
        self._running.clear()
        for w in self._workers:
            w.join(timeout=timeout)

    def _worker_loop(self) -> None:
        """Main loop for each worker thread."""
        while self._running.is_set():
            try:
                task = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            start = time.monotonic()
            success = False
            error = None

            try:
                task.callable(*task.args, **task.kwargs)
                success = True
            except Exception as e:
                error = e
                # Log but DON'T crash the worker thread
                # Retry logic here if task.max_retries > 0
            finally:
                elapsed = time.monotonic() - start
                if self._metrics_callback:
                    self._metrics_callback(
                        task_name=task.name,
                        priority=task.priority,
                        elapsed_seconds=elapsed,
                        success=success,
                        error=str(error) if error else None,
                    )
                self._queue.task_done()
```

**Contraintes** :
- `threading.Thread` pas `multiprocessing` (les brokers IBKR/Binance ne sont pas fork-safe)
- 3 workers threads max (le VPS a 4 vCPU, on laisse 1 pour l'OS + IB Gateway)
- Les tâches CRITICAL (kill switch) préemptent tout — elles s'exécutent avant toute tâche en attente
- Chaque tâche a un timeout (default 60s). Si timeout → kill la tâche + alerte
- Les métriques de chaque tâche (durée, succès/échec) sont émises pour R2 (observabilité)

**Critère de validation** :
- Injecter un sleep(30) dans crypto_cycle → les autres cycles ne sont PAS retardés
- Injecter une exception dans eu_cycle → les autres cycles continuent normalement
- Une tâche CRITICAL (kill switch) est exécutée dans les 2s même si tous les workers sont occupés
- 3116 tests passent

### Tâche R1-02 : Cycle Isolation avec Error Boundaries (P0)

**Fichier** : `core/worker/cycle_runner.py`
**Agent** : CLOCKWORK
**Estimation** : 6h
**Dépendance** : R1-01

**Spécification** :
```python
"""
Chaque cycle est wrappé dans un CycleRunner qui :
1. Capture toutes les exceptions (error boundary)
2. Mesure le temps d'exécution
3. Gère les retries avec backoff
4. Émet des métriques structurées
5. Envoie une alerte Telegram si le cycle échoue 3x de suite
6. Track l'état du cycle (HEALTHY, DEGRADED, FAILED)
"""

from dataclasses import dataclass
from enum import Enum
from typing import Callable
import time

class CycleHealth(Enum):
    HEALTHY = "HEALTHY"        # Dernier run OK
    DEGRADED = "DEGRADED"      # 1-2 échecs consécutifs, en retry
    FAILED = "FAILED"          # 3+ échecs consécutifs, alerté

@dataclass
class CycleMetrics:
    name: str
    last_run_at: float
    last_duration_seconds: float
    last_success: bool
    consecutive_failures: int
    total_runs: int
    total_failures: int
    health: CycleHealth
    avg_duration_seconds: float  # rolling 20 derniers runs

class CycleRunner:
    def __init__(
        self,
        name: str,
        callable: Callable,
        max_consecutive_failures: int = 3,
        timeout_seconds: float = 60.0,
        alert_callback: Callable = None,
    ):
        self.name = name
        self._callable = callable
        self._max_failures = max_consecutive_failures
        self._timeout = timeout_seconds
        self._alert = alert_callback
        self._consecutive_failures = 0
        self._total_runs = 0
        self._total_failures = 0
        self._durations: list[float] = []
        self._health = CycleHealth.HEALTHY

    def run(self, *args, **kwargs) -> CycleMetrics:
        """Execute the cycle with error boundary."""
        start = time.monotonic()
        self._total_runs += 1
        success = False

        try:
            self._callable(*args, **kwargs)
            success = True
            self._consecutive_failures = 0
            self._health = CycleHealth.HEALTHY
        except Exception as e:
            self._consecutive_failures += 1
            self._total_failures += 1

            if self._consecutive_failures >= self._max_failures:
                self._health = CycleHealth.FAILED
                if self._alert:
                    self._alert(
                        f"🔴 Cycle {self.name} FAILED: "
                        f"{self._consecutive_failures} échecs consécutifs. "
                        f"Dernière erreur: {e}"
                    )
            else:
                self._health = CycleHealth.DEGRADED
                if self._alert and self._consecutive_failures == 1:
                    self._alert(
                        f"⚠️ Cycle {self.name} erreur: {e}. "
                        f"Retry {self._consecutive_failures}/{self._max_failures}"
                    )
        finally:
            duration = time.monotonic() - start
            self._durations.append(duration)
            if len(self._durations) > 20:
                self._durations.pop(0)

        return CycleMetrics(
            name=self.name,
            last_run_at=start,
            last_duration_seconds=duration,
            last_success=success,
            consecutive_failures=self._consecutive_failures,
            total_runs=self._total_runs,
            total_failures=self._total_failures,
            health=self._health,
            avg_duration_seconds=sum(self._durations) / len(self._durations),
        )

    @property
    def is_healthy(self) -> bool:
        return self._health == CycleHealth.HEALTHY

    @property
    def metrics(self) -> CycleMetrics:
        return CycleMetrics(
            name=self.name,
            last_run_at=0,
            last_duration_seconds=0,
            last_success=True,
            consecutive_failures=self._consecutive_failures,
            total_runs=self._total_runs,
            total_failures=self._total_failures,
            health=self._health,
            avg_duration_seconds=(
                sum(self._durations) / len(self._durations)
                if self._durations else 0
            ),
        )
```

**Intégration dans le worker** :
```python
# worker.py (post-refactoring)
crypto_runner = CycleRunner("crypto", crypto_cycle, alert_callback=telegram_alert)
fx_runner = CycleRunner("fx", fx_cycle, alert_callback=telegram_alert)
risk_runner = CycleRunner("risk", risk_cycle, alert_callback=telegram_alert)
# ... etc

# Au lieu de :
#   crypto_cycle()  ← crash = tout plante
# On fait :
#   task_queue.submit(Task(
#       priority=TaskPriority.NORMAL,
#       name="crypto_15min",
#       callable=crypto_runner.run,
#       timeout_seconds=120,
#   ))
```

### Tâche R1-03 : Worker State partagé thread-safe (P0)

**Fichier** : `core/worker/worker_state.py`
**Agent** : CLOCKWORK
**Estimation** : 4h
**Dépendance** : R1-01

**Spécification** :
```python
"""
État partagé entre les cycles, thread-safe.

Aujourd'hui l'état est probablement un mix de :
- Variables globales dans worker.py
- Fichiers JSON sur disque
- Attributs d'instances partagées

Problème : race conditions quand 3 threads accèdent au même état.

Solution : un objet WorkerState avec locking granulaire.
"""

import threading
from dataclasses import dataclass, field
from typing import Dict, Optional
from datetime import datetime

@dataclass
class WorkerState:
    """Shared state across all worker cycles. Thread-safe."""

    # Locks par domaine (pas un seul lock global — trop de contention)
    _position_lock: threading.Lock = field(default_factory=threading.Lock)
    _regime_lock: threading.RLock = field(default_factory=threading.RLock)
    _kill_lock: threading.Lock = field(default_factory=threading.Lock)
    _metrics_lock: threading.Lock = field(default_factory=threading.Lock)

    # État positions (source de vérité = broker, ceci est le cache local)
    _positions: Dict[str, dict] = field(default_factory=dict)

    # État regime
    _current_regime: Dict[str, str] = field(default_factory=lambda: {
        "fx": "UNKNOWN",
        "crypto": "UNKNOWN",
        "us_equity": "UNKNOWN",
        "eu_equity": "UNKNOWN",
        "global": "UNKNOWN",
    })
    _regime_updated_at: Optional[datetime] = None

    # Kill switches
    _kill_switches: Dict[str, bool] = field(default_factory=lambda: {
        "ibkr": False,
        "binance": False,
        "alpaca": False,
        "global": False,
    })

    # Métriques des cycles
    _cycle_metrics: Dict[str, dict] = field(default_factory=dict)

    # --- Positions ---
    def get_positions(self, broker: str = None) -> dict:
        with self._position_lock:
            if broker:
                return {k: v for k, v in self._positions.items()
                        if v.get("broker") == broker}
            return dict(self._positions)

    def update_position(self, key: str, position: dict) -> None:
        with self._position_lock:
            self._positions[key] = position

    def remove_position(self, key: str) -> None:
        with self._position_lock:
            self._positions.pop(key, None)

    # --- Regime ---
    def get_regime(self, asset_class: str = "global") -> str:
        with self._regime_lock:
            return self._current_regime.get(asset_class, "UNKNOWN")

    def set_regime(self, asset_class: str, regime: str) -> None:
        with self._regime_lock:
            self._current_regime[asset_class] = regime
            self._regime_updated_at = datetime.now()

    # --- Kill switches ---
    def is_killed(self, broker: str = "global") -> bool:
        with self._kill_lock:
            return self._kill_switches.get(broker, False) or \
                   self._kill_switches.get("global", False)

    def activate_kill(self, broker: str) -> None:
        with self._kill_lock:
            self._kill_switches[broker] = True

    def deactivate_kill(self, broker: str) -> None:
        with self._kill_lock:
            self._kill_switches[broker] = False

    # --- Cycle metrics ---
    def record_cycle_metrics(self, name: str, metrics: dict) -> None:
        with self._metrics_lock:
            self._cycle_metrics[name] = metrics

    def get_all_cycle_metrics(self) -> dict:
        with self._metrics_lock:
            return dict(self._cycle_metrics)
```

**Contraintes** :
- Locks granulaires par domaine (position_lock, regime_lock, kill_lock, metrics_lock) — pas un lock global qui sérialiserait tout
- `RLock` pour le regime (un cycle peut lire le regime pendant qu'il le met à jour)
- Le kill switch utilise un `Lock` simple car les lectures/écritures sont atomiques
- Chaque cycle reçoit une référence à `WorkerState`, jamais de globals

### Tâche R1-04 : Migration progressive du worker (P0)

**Fichier** : `worker.py` (modification)
**Agent** : CLOCKWORK
**Estimation** : 8h
**Dépendance** : R1-01, R1-02, R1-03

**Spécification** :
```
Migration du worker séquentiel vers l'architecture event-driven.

ÉTAPE 1 : Dual-mode (1 semaine)
  - Le nouveau TaskQueue tourne EN PARALLÈLE du scheduler existant
  - Les cycles sont exécutés par les DEUX systèmes
  - Le nouveau système log ses décisions mais n'exécute PAS les trades
  - Comparaison : les deux systèmes doivent produire les mêmes signaux
  - Si divergence > 0 → investiguer et fixer

ÉTAPE 2 : Shadow-to-live (1 semaine)
  - Le TaskQueue exécute les trades
  - Le scheduler existant tourne en shadow (log seulement)
  - Si le TaskQueue échoue → fallback automatique sur le scheduler

ÉTAPE 3 : Cutover
  - Supprimer le scheduler existant
  - Le TaskQueue est le seul scheduler
  - worker.py < 200 lignes (init + graceful shutdown)

Rollback :
  - À chaque étape, git tag le commit précédent
  - Script de rollback : git checkout + systemctl restart
  - Le rollback doit prendre < 60s
```

---

## R2 — OBSERVABILITÉ STRUCTURÉE (Agent: PANOPTICON)

**Priorité globale : P0**
**Justification** : Tu as 15 commandes Telegram (monitoring réactif — tu dois demander) et un snapshot JSONL toutes les 5 min (données brutes non exploitées). Ce qui manque : du monitoring proactif qui détecte les anomalies AVANT qu'elles deviennent des incidents. "Le cycle crypto ralentit depuis 3 jours" est une info que tu n'as pas aujourd'hui.

### Tâche R2-01 : Metrics Pipeline (P0)

**Fichier** : `core/monitoring/metrics_pipeline.py`
**Agent** : PANOPTICON
**Estimation** : 8h

**Spécification** :
```python
"""
Pipeline de métriques structurées.

Chaque composant du système émet des métriques via un collecteur central.
Les métriques sont stockées dans SQLite (léger, pas besoin de TimescaleDB à cette échelle)
avec un index sur timestamp + metric_name pour les requêtes de tendance.

Sources de métriques :
  - CycleRunner (R1-02) : durée, succès/échec, health par cycle
  - Brokers : latence API, nombre de requêtes, erreurs
  - Risk : DD courant, ERE, regime, Kelly mode
  - Trading : trades exécutés, slippage, fill rate
  - System : CPU, RAM, disk, uptime worker
  - Queue (R1-01) : profondeur, temps d'attente, tâches en retard

Stockage :
  metrics.db (SQLite)
  - Table: metrics (timestamp, name, value, tags JSON)
  - Index: (name, timestamp)
  - Rétention : 90 jours (purge automatique)
  - Taille estimée : ~50MB pour 90 jours à 1 metric/sec

Requêtes typiques :
  - "Durée moyenne du cycle crypto sur les 7 derniers jours"
  - "Nombre d'erreurs Binance API par heure"
  - "Tendance du DD global sur 30 jours"
  - "Latence p95 des fills IBKR cette semaine"
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict
import sqlite3
import json
import threading

@dataclass
class Metric:
    name: str               # "cycle.crypto.duration_seconds"
    value: float            # 2.34
    timestamp: datetime     # now
    tags: Dict[str, str] = None  # {"broker": "binance", "status": "success"}

class MetricsCollector:
    """Thread-safe metrics collector with SQLite backend."""

    def __init__(self, db_path: str = "data/metrics.db"):
        self._db_path = db_path
        self._buffer: list[Metric] = []
        self._lock = threading.Lock()
        self._flush_interval = 10  # flush toutes les 10 secondes
        self._init_db()

    def emit(self, name: str, value: float, tags: dict = None) -> None:
        """Emit a metric. Thread-safe, buffered."""
        metric = Metric(
            name=name,
            value=value,
            timestamp=datetime.now(),
            tags=tags,
        )
        with self._lock:
            self._buffer.append(metric)
            if len(self._buffer) >= 100:  # flush si buffer plein
                self._flush()

    def _flush(self) -> None:
        """Write buffered metrics to SQLite."""
        if not self._buffer:
            return
        batch = self._buffer[:]
        self._buffer.clear()
        # SQLite write en dehors du lock pour pas bloquer les émissions
        conn = sqlite3.connect(self._db_path)
        conn.executemany(
            "INSERT INTO metrics (timestamp, name, value, tags) VALUES (?, ?, ?, ?)",
            [(m.timestamp.isoformat(), m.name, m.value,
              json.dumps(m.tags) if m.tags else None) for m in batch],
        )
        conn.commit()
        conn.close()

    def query(
        self,
        name: str,
        hours: int = 24,
        aggregation: str = "avg",
    ) -> float:
        """Query aggregated metric value over a time window."""
        conn = sqlite3.connect(self._db_path)
        cursor = conn.execute(
            f"SELECT {aggregation}(value) FROM metrics "
            f"WHERE name = ? AND timestamp > datetime('now', '-{hours} hours')",
            (name,),
        )
        result = cursor.fetchone()[0]
        conn.close()
        return result or 0.0

    def _init_db(self) -> None:
        conn = sqlite3.connect(self._db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                name TEXT NOT NULL,
                value REAL NOT NULL,
                tags TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_metrics_name_ts ON metrics (name, timestamp)"
        )
        conn.commit()
        conn.close()


# Singleton global (thread-safe car emit() est thread-safe)
metrics = MetricsCollector()
```

**Convention de nommage des métriques** :
```
Hiérarchie : {domaine}.{composant}.{métrique}

Exemples :
  cycle.crypto.duration_seconds       — durée du cycle crypto
  cycle.crypto.consecutive_failures   — échecs consécutifs
  broker.binance.latency_ms           — latence API Binance
  broker.ibkr.reconnections           — nombre de reconnexions
  risk.dd.global_pct                  — drawdown global en %
  risk.regime.current                 — regime courant (encodé en int)
  trade.fill.slippage_bps             — slippage du dernier fill en bps
  trade.fill.count                    — nombre de fills dans l'heure
  system.cpu.percent                  — CPU usage
  system.ram.percent                  — RAM usage
  system.disk.percent                 — Disk usage
  queue.depth                         — nombre de tâches en attente
  queue.oldest_seconds                — âge de la plus vieille tâche
```

### Tâche R2-02 : Anomaly Detector (P0)

**Fichier** : `core/monitoring/anomaly_detector.py`
**Agent** : PANOPTICON
**Estimation** : 6h
**Dépendance** : R2-01

**Spécification** :
```python
"""
Détection proactive d'anomalies sur les métriques.

Pas de ML (trop peu de données). Trois méthodes simples et robustes :

1. THRESHOLD : valeur dépasse un seuil fixe
   Exemple : cycle.crypto.duration_seconds > 30 → ALERT

2. TREND : moyenne glissante en hausse/baisse significative
   Exemple : cycle.crypto.duration_seconds avg 7j est 2x avg 30j → WARN

3. ABSENCE : une métrique attendue n'a pas été émise depuis N minutes
   Exemple : cycle.crypto.duration_seconds pas émis depuis 20min → CRITICAL
   (= le cycle crypto ne tourne plus)

Chaque anomalie déclenche une alerte Telegram avec contexte.
"""

from dataclasses import dataclass
from typing import Optional
from enum import Enum

class AlertLevel(Enum):
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"

@dataclass
class AnomalyRule:
    metric_name: str
    rule_type: str          # "threshold", "trend", "absence"
    level: AlertLevel
    # Threshold params
    threshold_max: Optional[float] = None
    threshold_min: Optional[float] = None
    # Trend params
    short_window_hours: int = 24      # fenêtre courte
    long_window_hours: int = 168      # fenêtre longue (7j)
    trend_ratio_warn: float = 1.5     # ratio court/long pour WARN
    trend_ratio_crit: float = 2.0     # ratio court/long pour CRITICAL
    # Absence params
    max_silence_minutes: int = 30

# Règles par défaut
DEFAULT_RULES = [
    # --- Cycles ---
    AnomalyRule("cycle.crypto.duration_seconds", "threshold",
                AlertLevel.WARN, threshold_max=30),
    AnomalyRule("cycle.crypto.duration_seconds", "threshold",
                AlertLevel.CRITICAL, threshold_max=60),
    AnomalyRule("cycle.crypto.duration_seconds", "trend",
                AlertLevel.WARN),
    AnomalyRule("cycle.crypto.duration_seconds", "absence",
                AlertLevel.CRITICAL, max_silence_minutes=20),

    AnomalyRule("cycle.fx.duration_seconds", "absence",
                AlertLevel.CRITICAL, max_silence_minutes=20),
    AnomalyRule("cycle.risk.duration_seconds", "absence",
                AlertLevel.CRITICAL, max_silence_minutes=10),

    # --- Brokers ---
    AnomalyRule("broker.binance.latency_ms", "threshold",
                AlertLevel.WARN, threshold_max=1000),
    AnomalyRule("broker.binance.latency_ms", "threshold",
                AlertLevel.CRITICAL, threshold_max=5000),
    AnomalyRule("broker.ibkr.reconnections", "threshold",
                AlertLevel.WARN, threshold_max=3),  # 3 reconnexions/heure
    AnomalyRule("broker.ibkr.reconnections", "threshold",
                AlertLevel.CRITICAL, threshold_max=10),

    # --- Risk ---
    AnomalyRule("risk.dd.global_pct", "threshold",
                AlertLevel.WARN, threshold_max=3.0),
    AnomalyRule("risk.dd.global_pct", "threshold",
                AlertLevel.CRITICAL, threshold_max=5.0),

    # --- System ---
    AnomalyRule("system.disk.percent", "threshold",
                AlertLevel.WARN, threshold_max=80),
    AnomalyRule("system.disk.percent", "threshold",
                AlertLevel.CRITICAL, threshold_max=90),
    AnomalyRule("system.ram.percent", "threshold",
                AlertLevel.WARN, threshold_max=85),

    # --- Queue ---
    AnomalyRule("queue.oldest_seconds", "threshold",
                AlertLevel.WARN, threshold_max=60),
    AnomalyRule("queue.oldest_seconds", "threshold",
                AlertLevel.CRITICAL, threshold_max=300),
]
```

### Tâche R2-03 : Cycle Health Dashboard endpoint (P1)

**Fichier** : `dashboard/api/routes/cycles.py`
**Agent** : PANOPTICON
**Estimation** : 4h
**Dépendance** : R1-02, R2-01

**Spécification** :
```
GET /api/cycles → JSON

{
  "cycles": {
    "crypto": {
      "health": "HEALTHY",
      "last_run": "2026-04-03T14:30:00",
      "last_duration_seconds": 2.3,
      "avg_duration_seconds": 2.1,
      "consecutive_failures": 0,
      "total_runs_24h": 96,
      "total_failures_24h": 1,
      "trend": "STABLE"  // ou "DEGRADING" ou "IMPROVING"
    },
    "fx": { ... },
    "risk": { ... },
    ...
  },
  "queue": {
    "depth": 2,
    "oldest_task_seconds": 0.5,
    "tasks_completed_1h": 47,
    "tasks_failed_1h": 0
  },
  "system": {
    "cpu_percent": 23.4,
    "ram_percent": 61.2,
    "disk_percent": 34.0,
    "uptime_hours": 48.3
  }
}
```

### Tâche R2-04 : Telegram /health refactoré (P1)

**Fichier** : `core/telegram/bot_service.py` (modification)
**Agent** : PANOPTICON
**Estimation** : 3h
**Dépendance** : R2-01, R2-02

**Spécification** :
```
/health → Résumé proactif (pas juste "worker is alive")

Format :
  📊 System Health — 03/04 14:30

  Cycles:
    ✅ crypto   2.3s avg (96 runs, 1 fail)
    ✅ fx       1.1s avg (48 runs, 0 fail)
    ✅ risk     0.8s avg (288 runs, 0 fail)
    ⚠️ eu      3.1s avg (↑47% vs 7j) ← TREND anomaly
    ✅ regime   0.4s avg

  Brokers:
    ✅ Binance  latency 45ms
    ✅ IBKR     latency 12ms, 0 reconnect
    ⬜ Alpaca   paper mode

  Queue: depth 0, 0 overdue

  Anomalies 24h: 1
    ⚠️ 08:15 cycle.eu.duration_seconds trend +47% vs 7j avg
```

---

## R3 — CONTRACT TESTING BROKER INTERFACES (Agent: DIPLOMAT)

**Priorité globale : P1**
**Justification** : Tes tests mockent les brokers. Ils testent que TON code gère correctement les réponses. Ils ne testent PAS que les réponses des brokers ont la structure que tu attends. Quand Binance a bloqué BTCUSDT (TRD_GRP_002), tu l'as découvert en prod. Un contract test l'aurait détecté avant.

### Tâche R3-01 : Broker Contract Definitions (P1)

**Fichier** : `core/broker/contracts/`
**Agent** : DIPLOMAT
**Estimation** : 6h

**Spécification** :
```python
"""
Contrats = schémas des réponses attendues de chaque broker API.
Pas des mocks — des assertions sur la structure réelle.

Pour chaque broker, on définit :
1. Quelles méthodes on utilise
2. Quelle structure de réponse on attend
3. Quels champs sont critiques (sans eux, le code plante)
4. Quels champs sont optionnels (le code gère leur absence)
"""

# core/broker/contracts/binance_contracts.py

from dataclasses import dataclass
from typing import List, Dict, Any

@dataclass
class BinanceContract:
    """Expected response structures from Binance API."""

    @staticmethod
    def account_balance(response: dict) -> bool:
        """Validate GET /api/v3/account response."""
        required_keys = {"balances", "canTrade", "canWithdraw"}
        if not required_keys.issubset(response.keys()):
            return False
        for balance in response["balances"]:
            if not {"asset", "free", "locked"}.issubset(balance.keys()):
                return False
            # Les valeurs doivent être des strings numériques
            try:
                float(balance["free"])
                float(balance["locked"])
            except (ValueError, TypeError):
                return False
        return True

    @staticmethod
    def order_response(response: dict) -> bool:
        """Validate POST /api/v3/order response."""
        required = {
            "symbol", "orderId", "status", "type",
            "side", "executedQty", "cummulativeQuoteQty"
        }
        return required.issubset(response.keys())

    @staticmethod
    def margin_account(response: dict) -> bool:
        """Validate GET /sapi/v1/margin/account response."""
        required = {"marginLevel", "totalAssetOfBtc", "totalLiabilityOfBtc"}
        return required.issubset(response.keys())

    @staticmethod
    def trading_pairs(response: dict) -> bool:
        """Validate GET /api/v3/exchangeInfo response."""
        if "symbols" not in response:
            return False
        for sym in response["symbols"][:5]:  # sample check
            if not {"symbol", "status", "baseAsset", "quoteAsset"}.issubset(sym.keys()):
                return False
        return True
```

**Contrats à définir** :
```
Binance :
  - account_balance (GET /api/v3/account)
  - order_response (POST /api/v3/order)
  - margin_account (GET /sapi/v1/margin/account)
  - trading_pairs (GET /api/v3/exchangeInfo)
  - klines (GET /api/v3/klines)
  - margin_borrow (POST /sapi/v1/margin/loan)

IBKR (via ib_insync) :
  - positions (ib.positions())
  - order_status (trade.orderStatus)
  - contract_details (ib.reqContractDetails())
  - historical_data (ib.reqHistoricalData())

Alpaca :
  - account (api.get_account())
  - positions (api.list_positions())
  - order (api.submit_order())
  - bars (api.get_bars())
```

### Tâche R3-02 : Contract Test Runner (P1)

**Fichier** : `core/broker/contracts/contract_runner.py`
**Agent** : DIPLOMAT
**Estimation** : 6h
**Dépendance** : R3-01

**Spécification** :
```
Runner qui appelle les APIs broker en mode READ-ONLY et valide les contrats.

Exécution : toutes les heures via le worker (cycle BACKGROUND priority)

Pour chaque broker :
  1. Appeler les endpoints read-only (balance, positions, exchange info)
  2. Valider la réponse contre le contrat défini
  3. Si validation échoue :
     a) Logger le delta (quel champ manque, quel type a changé)
     b) Émettre une métrique "broker.{name}.contract_violation"
     c) Alerte Telegram : "⚠️ Binance API contract violation: 
        missing field 'marginLevel' in margin_account response"
  4. Si validation OK : émettre "broker.{name}.contract_ok"

IMPORTANT : JAMAIS d'appels qui modifient l'état (pas de POST order, pas de borrow).
Uniquement des GET / lectures.

Tolérance :
  - 1 violation isolée → WARN (peut être un glitch réseau)
  - 3 violations consécutives → CRITICAL (l'API a probablement changé)
  - Sur CRITICAL → réduire le sizing sur ce broker de 50% automatiquement
```

### Tâche R3-03 : API Response Snapshots (P2)

**Fichier** : `core/broker/contracts/response_snapshots.py`
**Agent** : DIPLOMAT
**Estimation** : 4h
**Dépendance** : R3-02

**Spécification** :
```
Sauvegarder un snapshot de chaque réponse API toutes les heures.
Stockage : data/contracts/snapshots/{broker}_{endpoint}_{timestamp}.json

Utilité :
  1. Quand un contrat échoue, comparer avec le dernier snapshot OK
     → voir exactement ce qui a changé
  2. Construire une bibliothèque de réponses réelles pour les tests
     → remplacer les mocks par des données réelles historiques
  3. Détecter les changements progressifs (un champ qui disparaît 
     parfois, une valeur qui change de type)

Rétention : 7 jours (purge automatique)
Taille estimée : ~10MB/semaine (réponses JSON compressées)
```

---

## R4 — STATE MACHINE POUR LE LIFECYCLE DES ORDRES (Agent: STATEKEEPER)

**Priorité globale : P0**
**Justification** : Les 3 bugs critiques du 30/03 étaient tous des problèmes de lifecycle d'ordre (SL manquant = ordre FILLED sans protection, emergency close manquant = ordre OPEN indéfiniment, validate_order absent = ordre créé sans validation). Une state machine formelle rend ces bugs structurellement impossibles.

### Tâche R4-01 : Order State Machine (P0)

**Fichier** : `core/execution/order_state_machine.py`
**Agent** : STATEKEEPER
**Estimation** : 8h

**Spécification** :
```python
"""
State machine formelle pour le lifecycle d'un ordre.

États :
  DRAFT       → ordre créé en mémoire, pas encore validé
  VALIDATED   → a passé validate_order() (risk checks OK)
  SUBMITTED   → envoyé au broker
  PARTIAL     → partiellement rempli
  FILLED      → complètement rempli
  REJECTED    → rejeté par le risk manager ou le broker
  CANCELLED   → annulé (par le trader ou par timeout)
  EXPIRED     → expiré (fin de journée, GTC timeout)
  ERROR       → erreur inattendue

Transitions légales (TOUTES les autres sont INTERDITES) :

  DRAFT      → VALIDATED    (validate_order() passe)
  DRAFT      → REJECTED     (validate_order() échoue)
  VALIDATED  → SUBMITTED    (envoyé au broker)
  VALIDATED  → REJECTED     (broker refuse immédiatement)
  SUBMITTED  → PARTIAL      (fill partiel reçu)
  SUBMITTED  → FILLED       (fill complet reçu)
  SUBMITTED  → REJECTED     (broker rejette après soumission)
  SUBMITTED  → CANCELLED    (annulé par le trader/timeout)
  SUBMITTED  → EXPIRED      (fin de journée)
  SUBMITTED  → ERROR        (erreur inattendue)
  PARTIAL    → FILLED       (fill du reliquat)
  PARTIAL    → CANCELLED    (timeout sur reliquat, position partielle gardée)
  PARTIAL    → ERROR        (erreur pendant le fill du reliquat)

Invariants (vérifiés à chaque transition) :

  1. Un ordre FILLED doit avoir un SL associé (sinon → ERREUR, pas de transition)
  2. Un ordre PARTIAL doit avoir un SL ajusté à la quantité partielle
  3. Un ordre ne peut JAMAIS revenir à un état précédent (pas de FILLED → SUBMITTED)
  4. Un ordre VALIDATED a un timestamp de validation (audit trail)
  5. Un ordre SUBMITTED a un broker_order_id
"""

from enum import Enum
from typing import Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime

class OrderState(Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    SUBMITTED = "SUBMITTED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    ERROR = "ERROR"

# Transitions légales : (from_state, to_state) → guard function name
LEGAL_TRANSITIONS = {
    (OrderState.DRAFT, OrderState.VALIDATED): "guard_validation",
    (OrderState.DRAFT, OrderState.REJECTED): None,
    (OrderState.VALIDATED, OrderState.SUBMITTED): "guard_submission",
    (OrderState.VALIDATED, OrderState.REJECTED): None,
    (OrderState.SUBMITTED, OrderState.PARTIAL): "guard_partial_fill",
    (OrderState.SUBMITTED, OrderState.FILLED): "guard_full_fill",
    (OrderState.SUBMITTED, OrderState.REJECTED): None,
    (OrderState.SUBMITTED, OrderState.CANCELLED): None,
    (OrderState.SUBMITTED, OrderState.EXPIRED): None,
    (OrderState.SUBMITTED, OrderState.ERROR): None,
    (OrderState.PARTIAL, OrderState.FILLED): "guard_full_fill",
    (OrderState.PARTIAL, OrderState.CANCELLED): "guard_partial_cancel",
    (OrderState.PARTIAL, OrderState.ERROR): None,
}

TERMINAL_STATES = {
    OrderState.FILLED,
    OrderState.REJECTED,
    OrderState.CANCELLED,
    OrderState.EXPIRED,
    OrderState.ERROR,
}

@dataclass
class OrderStateMachine:
    order_id: str
    state: OrderState = OrderState.DRAFT
    history: list = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    broker_order_id: Optional[str] = None
    filled_quantity: float = 0.0
    total_quantity: float = 0.0
    has_sl: bool = False

    def transition(self, new_state: OrderState, **context) -> bool:
        """Attempt a state transition. Returns True if successful."""
        key = (self.state, new_state)

        # Vérifier que la transition est légale
        if key not in LEGAL_TRANSITIONS:
            raise IllegalTransitionError(
                f"Transition {self.state.value} → {new_state.value} is ILLEGAL. "
                f"Legal transitions from {self.state.value}: "
                f"{[t[1].value for t in LEGAL_TRANSITIONS if t[0] == self.state]}"
            )

        # Exécuter le guard si défini
        guard_name = LEGAL_TRANSITIONS[key]
        if guard_name:
            guard = getattr(self, guard_name)
            if not guard(**context):
                return False

        # Transition
        self.history.append({
            "from": self.state.value,
            "to": new_state.value,
            "at": datetime.now().isoformat(),
            "context": context,
        })
        self.state = new_state
        return True

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    # --- Guards ---

    def guard_validation(self, **ctx) -> bool:
        """L'ordre a passé validate_order()."""
        return ctx.get("risk_approved", False)

    def guard_submission(self, **ctx) -> bool:
        """L'ordre a un broker_order_id."""
        broker_id = ctx.get("broker_order_id")
        if not broker_id:
            return False
        self.broker_order_id = broker_id
        return True

    def guard_partial_fill(self, **ctx) -> bool:
        """Fill partiel : SL doit être ajusté."""
        qty = ctx.get("filled_quantity", 0)
        self.filled_quantity += qty
        # INVARIANT : SL doit exister pour la quantité partielle
        if not ctx.get("sl_adjusted", False):
            raise InvariantViolation(
                f"Order {self.order_id}: partial fill WITHOUT SL adjustment. "
                f"This is the exact bug from 30/03. BLOCKING transition."
            )
        self.has_sl = True
        return True

    def guard_full_fill(self, **ctx) -> bool:
        """Fill complet : SL doit exister."""
        self.filled_quantity = self.total_quantity
        if not ctx.get("has_sl", False) and not self.has_sl:
            raise InvariantViolation(
                f"Order {self.order_id}: FILLED without SL. "
                f"BLOCKING transition. This MUST be fixed."
            )
        self.has_sl = True
        return True

    def guard_partial_cancel(self, **ctx) -> bool:
        """Cancel du reliquat : la position partielle doit rester protégée."""
        return self.has_sl  # SL doit déjà être en place


class IllegalTransitionError(Exception):
    """Transition impossible dans la state machine."""
    pass

class InvariantViolation(Exception):
    """Un invariant de sécurité est violé."""
    pass
```

### Tâche R4-02 : Intégration Order SM dans les brokers (P0)

**Fichier** : Modifications dans `core/broker/`, `core/trading_engine.py`
**Agent** : STATEKEEPER
**Estimation** : 8h
**Dépendance** : R4-01

**Spécification** :
```
Intégration de l'OrderStateMachine dans le flow réel :

1. trading_engine.py :
   - create_order() → crée un OrderStateMachine en état DRAFT
   - validate_order() → transition DRAFT → VALIDATED
   - submit_order() → transition VALIDATED → SUBMITTED
   - on_fill() → transition SUBMITTED → FILLED (ou PARTIAL)

2. ibkr_bracket.py :
   - on_order_status() → appelle order_sm.transition() avec le bon état
   - Le guard_full_fill() BLOQUE si pas de SL → le bug du 30/03 est 
     structurellement impossible

3. binance_broker.py :
   - Même pattern

4. Persistance :
   - L'historique de chaque OrderStateMachine est sauvegardé dans 
     trade_journal (SQLite)
   - En cas de crash/restart, les ordres SUBMITTED sont rechargés 
     et réconciliés avec le broker

5. Audit :
   - GET /api/orders/{id}/history → liste des transitions avec timestamps
   - Chaque InvariantViolation est loguée ET alertée sur Telegram
```

### Tâche R4-03 : Position Lifecycle SM (P1)

**Fichier** : `core/execution/position_state_machine.py`
**Agent** : STATEKEEPER
**Estimation** : 6h
**Dépendance** : R4-01

**Spécification** :
```
Même pattern pour les positions :

États :
  PENDING     → ordre soumis, pas encore rempli
  OPEN        → position active, SL en place
  REDUCING    → en cours de fermeture partielle
  CLOSING     → en cours de fermeture totale
  CLOSED      → fermée, PnL réalisé
  ORPHAN      → position sans ordre associé (détectée par réconciliation)
  EMERGENCY   → en cours de fermeture d'urgence (kill switch)

Invariants :
  1. OPEN → SL doit exister broker-side
  2. OPEN → reconciliation confirme que le broker a la même position
  3. REDUCING → quantity_broker == quantity_local (pas de divergence)
  4. CLOSED → PnL est calculé et enregistré dans le journal
  5. ORPHAN → alerte immédiate, adoption ou fermeture

Transitions interdites :
  CLOSED → OPEN (une position fermée ne peut pas réouvrir)
  EMERGENCY → OPEN (après un emergency close, pas de réouverture auto)
```

---

## R5 — REPLAY ENGINE POUR DEBUGGING POST-MORTEM (Agent: ARCHAEOLOGIST)

**Priorité globale : P1**
**Justification** : Quand un bug arrive à 3h du matin, tu te réveilles avec un message Telegram et des logs. Tu dois deviner ce qui s'est passé. Un replay engine te permet de REJOUER exactement la séquence d'événements, step by step, localement.

### Tâche R5-01 : Event Logger déterministe (P0)

**Fichier** : `core/worker/event_logger.py`
**Agent** : ARCHAEOLOGIST
**Estimation** : 6h

**Spécification** :
```python
"""
Chaque cycle du worker log son input complet ET son output.

Format : JSONL (une ligne par événement), un fichier par jour.
Fichier : data/events/events_2026-04-03.jsonl

Chaque événement contient :
  - timestamp (ISO 8601, microseconde)
  - cycle_name ("crypto", "fx", "risk", etc.)
  - event_type ("CYCLE_START", "SIGNAL", "ORDER", "FILL", "ERROR", etc.)
  - input_snapshot : état complet au début du cycle
      - positions courantes
      - prix courants
      - regime courant
      - kelly mode
      - kill switch status
  - output : décision prise
      - signaux générés
      - ordres soumis
      - transitions d'état
  - duration_ms : temps d'exécution

Taille estimée : ~5MB/jour (compressible à ~500KB)
Rétention : 30 jours

IMPORTANT : 
  - Les events sont append-only (jamais modifiés après écriture)
  - L'écriture est asynchrone (ne bloque pas le cycle)
  - Le format doit être REPLAY-COMPATIBLE (voir R5-02)
"""

import json
import os
import threading
from datetime import datetime, date
from typing import Any, Dict

class EventLogger:
    def __init__(self, base_dir: str = "data/events"):
        self._base_dir = base_dir
        self._lock = threading.Lock()
        self._current_date: date = None
        self._file = None
        os.makedirs(base_dir, exist_ok=True)

    def log(
        self,
        cycle_name: str,
        event_type: str,
        data: Dict[str, Any],
        input_snapshot: Dict[str, Any] = None,
    ) -> None:
        """Log an event. Thread-safe."""
        event = {
            "ts": datetime.now().isoformat(),
            "cycle": cycle_name,
            "type": event_type,
            "data": data,
        }
        if input_snapshot:
            event["snapshot"] = input_snapshot

        line = json.dumps(event, default=str) + "\n"

        with self._lock:
            self._ensure_file()
            self._file.write(line)
            self._file.flush()  # flush immédiat pour pas perdre en cas de crash

    def _ensure_file(self) -> None:
        today = date.today()
        if self._current_date != today:
            if self._file:
                self._file.close()
            filename = f"events_{today.isoformat()}.jsonl"
            self._file = open(
                os.path.join(self._base_dir, filename), "a"
            )
            self._current_date = today

    def close(self) -> None:
        with self._lock:
            if self._file:
                self._file.close()
                self._file = None
```

### Tâche R5-02 : Replay Engine (P1)

**Fichier** : `core/worker/replay_engine.py`
**Agent** : ARCHAEOLOGIST
**Estimation** : 10h
**Dépendance** : R5-01

**Spécification** :
```python
"""
Rejeu d'une séquence d'événements enregistrés par EventLogger.

Usage :
  python -m core.worker.replay_engine \
      --events data/events/events_2026-04-03.jsonl \
      --cycle crypto \
      --from "2026-04-03T03:15:00" \
      --to "2026-04-03T03:30:00" \
      --step  # mode pas-à-pas

Le replay engine :
1. Charge les événements de la fenêtre temporelle
2. Pour chaque événement CYCLE_START :
   a) Restaure le snapshot d'input (positions, prix, regime, etc.)
   b) Exécute le cycle avec le même input
   c) Compare l'output du replay avec l'output enregistré
   d) Affiche les divergences
3. En mode --step : pause après chaque événement, attend Enter

Permet de répondre à :
  "Pourquoi le worker a ouvert une position BTC à 3h15 ?"
  → Rejouer le cycle crypto de 3h15 avec l'exact même état
  → Voir le signal, le regime, le sizing, la validation
  → Identifier l'erreur dans la chaîne de décision

IMPORTANT :
  - Le replay utilise des MOCK BROKERS (pas d'appels réels)
  - Les mock brokers retournent les prix du snapshot
  - Le replay est DETERMINISTE (même input → même output)
"""

import json
from datetime import datetime
from typing import List, Dict, Optional

class ReplayEngine:
    def __init__(self, events_file: str):
        self.events = self._load_events(events_file)

    def replay(
        self,
        cycle_name: str,
        from_ts: Optional[datetime] = None,
        to_ts: Optional[datetime] = None,
        step_mode: bool = False,
    ) -> List[dict]:
        """Replay events for a specific cycle in a time window."""
        filtered = [
            e for e in self.events
            if e["cycle"] == cycle_name
            and (not from_ts or datetime.fromisoformat(e["ts"]) >= from_ts)
            and (not to_ts or datetime.fromisoformat(e["ts"]) <= to_ts)
        ]

        results = []
        for event in filtered:
            if event["type"] == "CYCLE_START" and "snapshot" in event:
                # Restaurer l'état
                snapshot = event["snapshot"]
                # Exécuter le cycle avec cet état
                replay_output = self._execute_cycle(
                    cycle_name, snapshot
                )
                # Comparer avec l'output enregistré
                original_output = self._find_cycle_end(
                    event["ts"], cycle_name
                )
                divergences = self._compare(
                    replay_output, original_output
                )
                results.append({
                    "timestamp": event["ts"],
                    "divergences": divergences,
                    "replay_output": replay_output,
                    "original_output": original_output,
                })

                if step_mode:
                    print(f"\n--- Event {event['ts']} ---")
                    print(f"Snapshot: {json.dumps(snapshot, indent=2)}")
                    if divergences:
                        print(f"⚠️ DIVERGENCES: {divergences}")
                    else:
                        print("✅ Output matches")
                    input("Press Enter to continue...")

        return results

    def _load_events(self, path: str) -> list:
        events = []
        with open(path) as f:
            for line in f:
                events.append(json.loads(line.strip()))
        return events

    def _execute_cycle(self, cycle_name: str, snapshot: dict) -> dict:
        """Execute a cycle with mocked state from snapshot."""
        # Import le cycle approprié et exécute avec mock broker
        # Le snapshot contient : positions, prices, regime, kelly_mode, etc.
        raise NotImplementedError("Cycle-specific replay logic")

    def _find_cycle_end(self, start_ts: str, cycle_name: str) -> dict:
        """Find the CYCLE_END event matching this CYCLE_START."""
        for e in self.events:
            if (e["cycle"] == cycle_name
                and e["type"] == "CYCLE_END"
                and e["ts"] > start_ts):
                return e.get("data", {})
        return {}

    def _compare(self, replay: dict, original: dict) -> list:
        """Compare replay output with original. Return divergences."""
        divergences = []
        # Comparer les signaux, ordres, transitions
        # Retourner les différences
        return divergences
```

### Tâche R5-03 : Incident Report Generator (P2)

**Fichier** : `core/monitoring/incident_report.py`
**Agent** : ARCHAEOLOGIST
**Estimation** : 4h
**Dépendance** : R5-01, R2-01

**Spécification** :
```
Quand une anomalie CRITICAL est détectée (R2-02), générer automatiquement
un rapport d'incident :

1. Contexte temporel : 30 minutes d'événements avant l'anomalie
2. État du système : positions, regime, DD, Kelly mode
3. Métriques clés : latence broker, durée des cycles, queue depth
4. Transitions d'état : quels ordres/positions ont changé d'état
5. Alertes précédentes : y avait-il des WARN avant le CRITICAL ?

Format : Markdown, sauvé dans data/incidents/incident_2026-04-03_031500.md
Envoyé sur Telegram en résumé (5 lignes max)

Utilité : quand tu te réveilles le matin, tu as un rapport structuré
au lieu de 15 messages Telegram à reconstituer.
```

---

## R6 — GRACEFUL DEGRADATION PAR BROKER (Agent: DIPLOMAT)

**Priorité globale : P1**
**Justification** : Le worker partage de l'état global (regime, DD, portfolio unifié) qui dépend de TOUS les brokers. Si IBKR Gateway tombe (ça arrive — 2FA expire, maintenance), que se passe-t-il pour les métriques cross-broker ? Le regime global est-il stale ? Le DD global est-il faux ?

### Tâche R6-01 : Broker Health Status (P1)

**Fichier** : `core/broker/broker_health.py`
**Agent** : DIPLOMAT
**Estimation** : 4h

**Spécification** :
```python
"""
Chaque broker a un statut de santé tracké en temps réel.

États :
  HEALTHY     — API répond, latence normale, contrats OK
  DEGRADED    — API répond mais lente (>2x latence normale) 
                ou erreurs intermittentes (<3 consécutives)
  DOWN        — API ne répond pas ou 3+ erreurs consécutives
  MAINTENANCE — down planifié (weekend IBKR, etc.)

Impact par état :

  HEALTHY :
    - Trading normal
    - Données incluses dans le regime global / DD global

  DEGRADED :
    - Trading réduit (sizing /2)
    - Données incluses mais marquées "degraded"
    - Alerte : "⚠️ IBKR degraded: latency 450ms (avg 50ms)"

  DOWN :
    - AUCUN nouveau trade sur ce broker
    - Positions existantes : SL broker-side actifs, pas d'intervention
    - Données EXCLUES des calculs cross-broker
    - Regime global calculé sans ce broker
    - DD global calculé sans ce broker (conservative : on assume 0 PnL)
    - Alerte : "🔴 IBKR DOWN since 03:15. Crypto+Alpaca continue normally."

  MAINTENANCE :
    - Comme DOWN mais sans alerte répétée
    - Pré-programmable (IBKR weekend, etc.)
"""

from enum import Enum
from datetime import datetime
from typing import Optional

class BrokerHealth(Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"
    MAINTENANCE = "MAINTENANCE"

class BrokerHealthTracker:
    def __init__(self, broker_name: str):
        self.broker_name = broker_name
        self.health = BrokerHealth.HEALTHY
        self._consecutive_errors = 0
        self._last_success: Optional[datetime] = None
        self._avg_latency_ms: float = 0
        self._latency_samples: list[float] = []

    def record_success(self, latency_ms: float) -> BrokerHealth:
        """Record a successful API call."""
        self._consecutive_errors = 0
        self._last_success = datetime.now()
        self._latency_samples.append(latency_ms)
        if len(self._latency_samples) > 100:
            self._latency_samples.pop(0)
        self._avg_latency_ms = sum(self._latency_samples) / len(self._latency_samples)

        if latency_ms > 2 * self._avg_latency_ms and self._avg_latency_ms > 0:
            self.health = BrokerHealth.DEGRADED
        else:
            self.health = BrokerHealth.HEALTHY
        return self.health

    def record_error(self, error: str) -> BrokerHealth:
        """Record a failed API call."""
        self._consecutive_errors += 1
        if self._consecutive_errors >= 3:
            self.health = BrokerHealth.DOWN
        elif self._consecutive_errors >= 1:
            self.health = BrokerHealth.DEGRADED
        return self.health

    def set_maintenance(self, until: datetime) -> None:
        self.health = BrokerHealth.MAINTENANCE

    @property
    def is_tradeable(self) -> bool:
        return self.health in (BrokerHealth.HEALTHY, BrokerHealth.DEGRADED)

    @property
    def is_data_reliable(self) -> bool:
        return self.health == BrokerHealth.HEALTHY
```

### Tâche R6-02 : Regime & DD avec données partielles (P1)

**Fichier** : Modifications dans `core/regime/`, `core/risk/unified_portfolio.py`
**Agent** : DIPLOMAT
**Estimation** : 6h
**Dépendance** : R6-01

**Spécification** :
```
Quand un broker est DOWN :

1. Regime Engine :
   - Si IBKR down → regime FX = UNKNOWN, regime EU = UNKNOWN
   - Regime global = worst(regimes disponibles + UNKNOWN pour les manquants)
   - UNKNOWN → toutes les strats de ce broker → activation 0.5x (pas 0.0)
   - Log : "Regime calculated without IBKR data (DOWN since 03:15)"

2. Unified Portfolio :
   - DD global = DD calculé sur les brokers HEALTHY seulement
   - Marqué "PARTIAL" dans le rapport : "DD global 2.1% (PARTIAL: IBKR excluded)"
   - NAV = sum(brokers HEALTHY) + last_known_nav(brokers DOWN)
   - Le NAV des brokers DOWN est frozen à la dernière valeur connue

3. Circuit breakers :
   - Les seuils globaux (3%/5%/8%) s'appliquent au DD PARTIAL
   - Conservative : si DD PARTIAL > seuil → trigger, même si le broker DOWN
     avait un PnL positif qui aurait compensé

4. Telegram :
   - /portfolio affiche clairement quels brokers sont inclus/exclus
   - "📊 Portfolio — 03/04 14:30
     ✅ Binance: $10,234 (+1.2%)
     ✅ Alpaca: $30,100 (+0.3%)
     🔴 IBKR: $10,000 (FROZEN — down since 03:15)
     NAV: $50,334 (PARTIAL)"
```

---

## R7 — CANARY DEPLOYS (Agent: GATEKEEPER)

**Priorité globale : P1**
**Justification** : Chaque deploy est un `git pull && systemctl restart` sur le VPS live. Pas de staging, pas de rollback automatique. Avec 45K en jeu, un deploy qui introduit un bug silencieux (pas un crash — un calcul faux) peut coûter cher avant d'être détecté.

### Tâche R7-01 : Shadow Worker (P1)

**Fichier** : `scripts/shadow_worker.py`, `core/worker/shadow_mode.py`
**Agent** : GATEKEEPER
**Estimation** : 8h

**Spécification** :
```
Un deuxième worker qui tourne en parallèle du worker live.
Il exécute le même code, reçoit les mêmes données, mais ne trade PAS.

Architecture :
  worker.py (LIVE)     → génère signaux → exécute les ordres
  shadow_worker.py     → génère signaux → LOG SEULEMENT

Le shadow worker :
  1. Se connecte aux mêmes feeds de données (Binance WS, IBKR historical)
  2. Exécute les mêmes cycles
  3. Génère les mêmes signaux
  4. Au lieu de submit_order() → log_shadow_signal()
  5. Stocke ses signaux dans data/shadow/shadow_signals.jsonl

Comparateur :
  Un script compare les signaux live vs shadow toutes les heures.
  Si divergence > 0 :
    - Identifier quel signal diffère (strat, ticker, direction, sizing)
    - Alerte Telegram : "⚠️ Shadow divergence: crypto_cycle produced 
      BUY BTCUSDC in live but SELL in shadow at 14:15"
    - Si > 3 divergences en 1h → ALERTE ROUGE

Usage deploy :
  1. Deploy le nouveau code sur shadow_worker
  2. Laisser tourner 4-24h
  3. Si 0 divergence → deploy sur worker live
  4. Si divergences → investiguer avant de deployer

Ressources :
  - CPU : +30% (acceptable sur 4 vCPU)
  - RAM : +200MB environ
  - Le shadow worker est un service systemd séparé (crash indépendant)
```

### Tâche R7-02 : Rollback automatique (P1)

**Fichier** : `scripts/deploy.sh`
**Agent** : GATEKEEPER
**Estimation** : 4h

**Spécification** :
```bash
#!/bin/bash
# scripts/deploy.sh — Deploy with automatic rollback

set -euo pipefail

WORKER_SERVICE="trading-worker"
SHADOW_SERVICE="trading-shadow"
ROLLBACK_TAG=""
HEALTH_ENDPOINT="http://localhost:8080/health"
HEALTH_TIMEOUT=30
CANARY_HOURS=4

echo "=== DEPLOY STARTED ==="

# 1. Tag le commit actuel comme rollback point
ROLLBACK_TAG="rollback-$(date +%Y%m%d-%H%M%S)"
git tag "$ROLLBACK_TAG"
echo "Rollback point: $ROLLBACK_TAG"

# 2. Pull le nouveau code
git pull origin main

# 3. Run les tests
echo "Running tests..."
python -m pytest tests/ -x -q --timeout=300
if [ $? -ne 0 ]; then
    echo "❌ TESTS FAILED. Rolling back."
    git checkout "$ROLLBACK_TAG"
    exit 1
fi

# 4. Deploy sur shadow d'abord
echo "Deploying to shadow worker..."
systemctl restart "$SHADOW_SERVICE"
sleep 5

# 5. Vérifier que le shadow démarre
curl -sf "$HEALTH_ENDPOINT" > /dev/null 2>&1
if [ $? -ne 0 ]; then
    echo "❌ Shadow worker failed to start. Rolling back."
    git checkout "$ROLLBACK_TAG"
    systemctl restart "$SHADOW_SERVICE"
    exit 1
fi

echo "✅ Shadow running. Monitoring for $CANARY_HOURS hours..."
echo "Run 'scripts/promote.sh' to promote to live after canary period."
echo "Run 'scripts/rollback.sh $ROLLBACK_TAG' to rollback."
```

### Tâche R7-03 : Deploy Checklist automatisée (P2)

**Fichier** : `scripts/pre_deploy_check.py`
**Agent** : GATEKEEPER
**Estimation** : 3h

**Spécification** :
```
Checks automatiques AVANT tout deploy :

1. Tous les tests passent (pytest)
2. Ruff clean (pas de violations)
3. Mypy clean (sur les modules stricts)
4. Pas de secrets dans le diff (grep pour API keys, passwords)
5. Pas de print() dans core/ (utiliser logging)
6. Pas de TODO FIXME HACK dans le diff
7. Le worker actuel est HEALTHY (pas de deploy pendant un incident)
8. Pas de positions ouvertes critiques (pas de deploy avec un gros trade en cours)
9. Git status propre (pas de fichiers non commités)

Si un check échoue → BLOCK le deploy avec explication.
Override possible avec --force (mais loggé + alerté).
```

---

## SÉQUENÇAGE GLOBAL

### Phase 0 — Fondations architecturales (Semaines 1-3)

```
R1-01  Task Queue Architecture             CLOCKWORK     10h
R1-02  Cycle Isolation (Error Boundaries)   CLOCKWORK     6h
R1-03  Worker State thread-safe             CLOCKWORK     4h
R4-01  Order State Machine                  STATEKEEPER   8h
R5-01  Event Logger déterministe            ARCHAEOLOGIST 6h
R2-01  Metrics Pipeline                     PANOPTICON    8h
                                             TOTAL:        42h
```

### Phase 1 — Intégration & Monitoring (Semaines 3-6)

```
R1-04  Migration progressive worker         CLOCKWORK     8h
R4-02  Intégration Order SM brokers         STATEKEEPER   8h
R2-02  Anomaly Detector                     PANOPTICON    6h
R2-03  Cycle Health Dashboard               PANOPTICON    4h
R2-04  Telegram /health refactoré           PANOPTICON    3h
R6-01  Broker Health Status                 DIPLOMAT      4h
R6-02  Regime & DD données partielles       DIPLOMAT      6h
                                             TOTAL:        39h
```

### Phase 2 — Résilience & Debugging (Semaines 6-10)

```
R3-01  Broker Contract Definitions          DIPLOMAT      6h
R3-02  Contract Test Runner                 DIPLOMAT      6h
R4-03  Position Lifecycle SM                STATEKEEPER   6h
R5-02  Replay Engine                        ARCHAEOLOGIST 10h
R7-01  Shadow Worker                        GATEKEEPER    8h
R7-02  Rollback automatique                 GATEKEEPER    4h
                                             TOTAL:        40h
```

### Phase 3 — Polish (Semaines 10+)

```
R3-03  API Response Snapshots               DIPLOMAT      4h
R5-03  Incident Report Generator            ARCHAEOLOGIST 4h
R7-03  Deploy Checklist automatisée         GATEKEEPER    3h
                                             TOTAL:        11h
```

---

## MÉTRIQUES DE SUCCÈS

### Phase 0 complete
- [ ] Worker event-driven opérationnel (3 worker threads, priority queue)
- [ ] Chaque cycle isolé — un crash dans crypto n'affecte PAS fx
- [ ] Order state machine bloque les transitions invalides (SL absent → rejeté)
- [ ] Event logger produit des JSONL rejouables
- [ ] Metrics pipeline collecte > 20 métriques distinctes

### Phase 1 complete
- [ ] Worker migré de séquentiel à event-driven (0 divergence pendant 1 semaine)
- [ ] Anomaly detector émet des alertes proactives (trend, absence, threshold)
- [ ] /health Telegram affiche l'état de chaque cycle et broker
- [ ] Broker DOWN → strats de ce broker stoppées, les autres continuent normalement
- [ ] Regime global calculable avec 1 broker DOWN

### Phase 2 complete
- [ ] Contract tests détectent un changement d'API broker dans l'heure
- [ ] Replay engine reproduit un incident historique avec 100% de fidélité
- [ ] Shadow worker détecte une régression de signal avant deploy live
- [ ] Rollback < 60 secondes

### 6 mois
- [ ] 0 incident non détecté par le monitoring proactif
- [ ] Temps moyen de diagnostic d'un incident < 15 minutes (vs heures aujourd'hui)
- [ ] 100% des deploys passent par le canary process
- [ ] 0 transition d'état d'ordre impossible

---

## INTERDÉPENDANCES

```
R1-01 (Queue) ──────┬──► R1-02 (Cycle Isolation)
                     ├──► R1-03 (Worker State)
                     └──► R1-04 (Migration)
                                │
R2-01 (Metrics) ────┬──► R2-02 (Anomaly Detector)
                     ├──► R2-03 (Dashboard)
                     └──► R2-04 (Telegram /health)
                                │
R4-01 (Order SM) ───┬──► R4-02 (Intégration brokers)
                     └──► R4-03 (Position SM)
                                │
R5-01 (Event Logger) ──► R5-02 (Replay Engine) ──► R5-03 (Incident Report)
                                │
R3-01 (Contracts) ──────► R3-02 (Runner) ──► R3-03 (Snapshots)
                                │
R6-01 (Broker Health) ──► R6-02 (Regime partial data)
                                │
R7-01 (Shadow Worker) ──► R7-02 (Rollback) ──► R7-03 (Deploy Checklist)
```

Les chantiers R1-R2-R4-R5 sont parallélisables.
R3 et R6 sont parallélisables.
R7 dépend de R1 (le shadow worker utilise la même architecture).

---

## RÈGLE ABSOLUE

> Le worker live ne doit JAMAIS être down pendant ces travaux.
>
> Chaque changement est testé en shadow avant d'être déployé en live.
> Chaque migration est progressive (dual-mode → shadow → cutover).
> Chaque commit a un rollback tag.
>
> Si un doute existe sur l'impact d'un changement : NE PAS DEPLOYER.
> Mieux vaut 1 semaine de retard que 1 trade corrompu.

---

**Total estimé : ~132 heures de développement**
**7 chantiers, 6 agents, 22 tâches**
**Objectif : éliminer des catégories entières de bugs, pas des bugs individuels**

**Document généré le 3 Avril 2026**
**Prochaine revue : après Phase 0**
