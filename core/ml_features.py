"""
Feature Engineering Pipeline — collecte et stockage des features pour chaque trade.

Accumule progressivement les features dans un SQLite local pour le futur filtre ML
(core/ml_filter.py). Le modele ML ne sera entraine qu'apres 200+ trades par strategie.

Architecture :
  1. Avant chaque trade : collect() extrait les features du contexte
  2. Apres chaque trade : store() enregistre les features + le resultat
  3. Le SQLite est append-only, jamais de suppression
  4. Les features sont versionnees (schema_version) pour la compatibilite future

Integration :
  paper_portfolio.py (execute_signals) -> FeatureCollector.collect() + store()
"""

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class FeatureCollector:
    """Collecte les features pour chaque trade pour le futur filtre ML.

    Stocke dans un SQLite pour accumulation progressive.

    Usage :
        collector = FeatureCollector()
        features = collector.collect(trade_context)
        # ... execute trade ...
        collector.store(features, trade_result)

        # Analytics
        df = collector.get_features_df("gap_continuation")
        print(f"{len(df)} trades collectes pour gap_continuation")
    """

    FEATURES = [
        "hour",
        "day_of_week",
        "vix",
        "regime",
        "gap_pct",
        "volume_ratio",
        "atr_ratio",
        "spy_return_1h",
        "sector_perf",
        "distance_to_event",
        "spread_estimate",
    ]

    SCHEMA_VERSION = 1
    DEFAULT_DB_PATH = "data_cache/ml_features.db"

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            db_path = str(Path(__file__).parent.parent / self.DEFAULT_DB_PATH)
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialise la base SQLite avec le schema requis."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trade_features (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    symbol TEXT,
                    direction TEXT,
                    schema_version INTEGER DEFAULT 1,
                    -- Features
                    hour REAL,
                    day_of_week REAL,
                    vix REAL,
                    regime REAL,
                    gap_pct REAL,
                    volume_ratio REAL,
                    atr_ratio REAL,
                    spy_return_1h REAL,
                    sector_perf REAL,
                    distance_to_event REAL,
                    spread_estimate REAL,
                    -- Extra features (JSON blob for future expansion)
                    extra_features TEXT,
                    -- Trade result (filled after trade)
                    pnl REAL,
                    pnl_pct REAL,
                    profitable INTEGER,
                    hold_duration_min REAL,
                    exit_reason TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_strategy
                ON trade_features(strategy)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_timestamp
                ON trade_features(timestamp)
            """)
            conn.commit()
        finally:
            conn.close()

    def collect(self, trade_context: dict) -> dict:
        """Collecte les features pour un trade.

        Args:
            trade_context: {
                strategy: str,
                symbol: str,
                direction: str ('LONG' or 'SHORT'),
                timestamp: str or datetime (optional, defaults to now),
                hour: int (heure de la journee, 0-23),
                day_of_week: int (0=lundi, 4=vendredi),
                vix: float (niveau du VIX),
                regime: int (0=bear, 1=neutral, 2=bull),
                gap_pct: float (% de gap a l'ouverture),
                volume_ratio: float (volume / avg volume 20j),
                atr_ratio: float (ATR courant / ATR moyen),
                spy_return_1h: float (return SPY 1h glissant),
                sector_perf: float (performance secteur 1j),
                distance_to_event: float (jours jusqu'au prochain event),
                spread_estimate: float (spread bid-ask en %),
                extra: dict (features additionnelles optionnelles),
            }

        Returns:
            dict avec toutes les features extraites (pret pour store()).
        """
        features = {
            "strategy": trade_context.get("strategy", "unknown"),
            "symbol": trade_context.get("symbol", ""),
            "direction": trade_context.get("direction", ""),
            "timestamp": str(
                trade_context.get("timestamp", datetime.now(UTC).isoformat())
            ),
            "schema_version": self.SCHEMA_VERSION,
        }

        # Extraire chaque feature standard
        for feat in self.FEATURES:
            value = trade_context.get(feat)
            if value is not None:
                try:
                    features[feat] = float(value)
                except (ValueError, TypeError):
                    features[feat] = None
            else:
                features[feat] = None

        # Extra features (blob JSON)
        extra = trade_context.get("extra", {})
        if extra:
            features["extra_features"] = json.dumps(extra)
        else:
            features["extra_features"] = None

        logger.debug(
            "Features collectees pour %s/%s: %d features non-null",
            features["strategy"],
            features["symbol"],
            sum(1 for f in self.FEATURES if features.get(f) is not None),
        )

        return features

    def store(self, features: dict, trade_result: dict) -> int:
        """Stocke en SQLite les features + le resultat du trade.

        Args:
            features: dict retourne par collect().
            trade_result: {
                pnl: float (P&L en $),
                pnl_pct: float (P&L en %),
                profitable: bool,
                hold_duration_min: float (duree en minutes),
                exit_reason: str ('tp', 'sl', 'timeout', 'close_eod'),
            }

        Returns:
            int: ID de la row inseree.
        """
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                """
                INSERT INTO trade_features (
                    timestamp, strategy, symbol, direction, schema_version,
                    hour, day_of_week, vix, regime, gap_pct,
                    volume_ratio, atr_ratio, spy_return_1h, sector_perf,
                    distance_to_event, spread_estimate, extra_features,
                    pnl, pnl_pct, profitable, hold_duration_min, exit_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    features.get("timestamp"),
                    features.get("strategy"),
                    features.get("symbol"),
                    features.get("direction"),
                    features.get("schema_version", self.SCHEMA_VERSION),
                    features.get("hour"),
                    features.get("day_of_week"),
                    features.get("vix"),
                    features.get("regime"),
                    features.get("gap_pct"),
                    features.get("volume_ratio"),
                    features.get("atr_ratio"),
                    features.get("spy_return_1h"),
                    features.get("sector_perf"),
                    features.get("distance_to_event"),
                    features.get("spread_estimate"),
                    features.get("extra_features"),
                    trade_result.get("pnl"),
                    trade_result.get("pnl_pct"),
                    1 if trade_result.get("profitable") else 0,
                    trade_result.get("hold_duration_min"),
                    trade_result.get("exit_reason"),
                ),
            )
            conn.commit()
            row_id = cursor.lastrowid
            logger.debug(
                "Feature stored (id=%d): %s/%s pnl=%.2f",
                row_id,
                features.get("strategy"),
                features.get("symbol"),
                trade_result.get("pnl", 0),
            )
            return row_id
        finally:
            conn.close()

    def get_features_df(self, strategy: str | None = None) -> list:
        """Recupere les features stockees sous forme de liste de dicts.

        Args:
            strategy: filtre par strategie (None = toutes).

        Returns:
            Liste de dicts avec toutes les colonnes.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            if strategy:
                rows = conn.execute(
                    "SELECT * FROM trade_features WHERE strategy = ? ORDER BY timestamp",
                    (strategy,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM trade_features ORDER BY timestamp"
                ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_strategy_counts(self) -> dict:
        """Nombre de trades collectes par strategie.

        Returns:
            {strategy_name: count}
        """
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT strategy, COUNT(*) as cnt FROM trade_features GROUP BY strategy"
            ).fetchall()
            return {row[0]: row[1] for row in rows}
        finally:
            conn.close()

    def get_ml_readiness(self, min_trades: int = 200) -> dict:
        """Check si chaque strategie a assez de trades pour le ML filter.

        Args:
            min_trades: seuil minimum (defaut 200).

        Returns:
            {
                strategy: {count, ready, missing},
                total_trades: int,
                ready_strategies: [str],
            }
        """
        counts = self.get_strategy_counts()
        report = {}
        ready = []

        for strategy, count in counts.items():
            is_ready = count >= min_trades
            report[strategy] = {
                "count": count,
                "ready": is_ready,
                "missing": max(0, min_trades - count),
            }
            if is_ready:
                ready.append(strategy)

        return {
            "strategies": report,
            "total_trades": sum(counts.values()),
            "ready_strategies": ready,
        }

    def purge_strategy(self, strategy: str) -> int:
        """Supprime toutes les features d'une strategie (pour reset).

        Args:
            strategy: nom de la strategie.

        Returns:
            Nombre de rows supprimees.
        """
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                "DELETE FROM trade_features WHERE strategy = ?",
                (strategy,),
            )
            conn.commit()
            deleted = cursor.rowcount
            logger.info("Purged %d features for strategy %s", deleted, strategy)
            return deleted
        finally:
            conn.close()
