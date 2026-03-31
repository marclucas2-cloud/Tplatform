"""
Module de qualite des donnees en temps reel.

Garde de qualite executee sur chaque bougie/tick avant traitement
par les strategies. Detecte les bad ticks, gaps, donnees stale,
et gele les signaux sur un ticker quand la qualite est insuffisante.

Multi-marche : crypto (24/7), FX (24h sessions), equities (heures US).

Usage:
    from core.data.data_quality import DataQualityGuard

    guard = DataQualityGuard()
    is_valid, warnings = guard.validate_candle(candle, history, market="crypto")
    if not is_valid:
        logger.warning(f"Candle rejetee: {warnings}")
        return
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Repertoire racine du projet
_ROOT = Path(__file__).resolve().parent.parent.parent
_LOG_DIR = _ROOT / "data"
_LOG_FILE = _LOG_DIR / "data_quality_log.jsonl"

# ── Seuils par defaut par type de marche ──────────────────────────────────────

DEFAULT_THRESHOLDS = {
    "crypto": {
        "z_score_bad_tick": 5.0,       # Crypto: volatilite naturelle elevee
        "max_gap_seconds": 900,         # 15 min pour candles 15min
        "stale_data_seconds": 120,      # 2 min sans nouvelles donnees
        "max_return_pct": 15.0,         # Return max % sur une bougie
        "min_lookback": 10,             # Min bougies pour z-score
    },
    "fx": {
        "z_score_bad_tick": 4.0,        # FX: volatilite plus basse
        "max_gap_seconds": 300,         # 5 min pour candles 5min
        "stale_data_seconds": 60,       # 1 min sans nouvelles donnees
        "max_return_pct": 5.0,          # Return max % sur une bougie
        "min_lookback": 10,
    },
    "equities": {
        "z_score_bad_tick": 3.5,        # Equities: volatilite moderee
        "max_gap_seconds": 300,         # 5 min pendant heures de marche
        "stale_data_seconds": 60,       # 1 min sans nouvelles donnees
        "max_return_pct": 10.0,         # Return max % sur une bougie
        "min_lookback": 10,
    },
}

# Jours de la semaine ou chaque marche est ouvert (lundi=0 ... dimanche=6)
MARKET_DAYS = {
    "crypto": {0, 1, 2, 3, 4, 5, 6},   # 24/7
    "fx": {0, 1, 2, 3, 4},              # Lundi a vendredi (sessions)
    "equities": {0, 1, 2, 3, 4},        # Lundi a vendredi (heures US)
}

# Plages horaires des sessions (UTC). Crypto = 24/7 donc pas de filtre.
MARKET_SESSIONS_UTC = {
    "fx": (0, 24),         # FX : sessions continues du dimanche soir au vendredi soir
    "equities": (13, 21),  # ~9h30-16h00 ET en UTC (approximation large)
}


class DataQualityGuard:
    """Garde de qualite des donnees en temps reel.

    Valide chaque bougie entrante avant traitement par les strategies.
    Gele les signaux par ticker quand un bad tick est detecte.
    """

    def __init__(self, config: dict = None):
        """
        Args:
            config: dict optionnel pour surcharger les seuils par defaut.
                    Structure: {"crypto": {"z_score_bad_tick": 6.0, ...}, ...}
        """
        # Fusionner config utilisateur avec les seuils par defaut
        self.thresholds = {}
        for market in DEFAULT_THRESHOLDS:
            base = DEFAULT_THRESHOLDS[market].copy()
            if config and market in config:
                base.update(config[market])
            self.thresholds[market] = base

        # Registre des tickers geles : {ticker: datetime_expiration}
        self._frozen_tickers: dict[str, datetime] = {}
        self._lock = threading.Lock()

        # Compteurs pour monitoring
        self._stats = {
            "candles_validated": 0,
            "candles_rejected": 0,
            "bad_ticks_detected": 0,
            "stale_data_detected": 0,
            "gaps_detected": 0,
        }

    # ── Validation principale ────────────────────────────────────────────────

    def validate_candle(
        self,
        candle: dict,
        history: pd.DataFrame,
        market: str = "equities",
    ) -> tuple[bool, list[str]]:
        """Valide une bougie entrante contre l'historique.

        Args:
            candle: dict avec au minimum {open, high, low, close, volume, timestamp}
            history: DataFrame historique avec colonnes OHLCV et DatetimeIndex
            market: "crypto", "fx", ou "equities"

        Returns:
            (is_valid, list_of_warnings) — is_valid=False signifie rejeter la bougie
        """
        warnings = []
        is_valid = True

        # 1. Coherence OHLC — toujours verifiee
        ohlc_valid, ohlc_msg = self.validate_ohlc_consistency(candle)
        if not ohlc_valid:
            warnings.append(ohlc_msg)
            is_valid = False

        # 2. Bad tick via z-score si assez d'historique
        close = candle.get("close", 0)
        if close > 0 and history is not None and "close" in history.columns:
            lookback = self.thresholds.get(market, {}).get("min_lookback", 10)
            if len(history) >= lookback:
                is_bad, z_score = self.detect_bad_tick(
                    close, history["close"], lookback=lookback, market=market
                )
                if is_bad:
                    warnings.append(
                        f"BAD_TICK: z_score={z_score:.2f} "
                        f"(seuil={self.thresholds[market]['z_score_bad_tick']})"
                    )
                    is_valid = False
                    self._stats["bad_ticks_detected"] += 1

                    # Log pour post-mortem
                    self._log_bad_tick(candle, z_score, market)

        # 3. Return extreme (filet de securite supplementaire)
        if close > 0 and history is not None and "close" in history.columns and len(history) > 0:
            last_close = history["close"].iloc[-1]
            if last_close > 0:
                return_pct = abs((close - last_close) / last_close) * 100
                max_return = self.thresholds.get(market, {}).get("max_return_pct", 10.0)
                if return_pct > max_return:
                    warnings.append(
                        f"EXTREME_RETURN: {return_pct:.2f}% (max={max_return}%)"
                    )
                    is_valid = False

        # 4. Ticker gele ?
        ticker = candle.get("ticker", candle.get("symbol", ""))
        if ticker and self.is_frozen(ticker):
            warnings.append(f"TICKER_FROZEN: {ticker} est gele suite a un bad tick")
            is_valid = False

        # Stats
        if is_valid:
            self._stats["candles_validated"] += 1
        else:
            self._stats["candles_rejected"] += 1

        return is_valid, warnings

    # ── Coherence OHLC ───────────────────────────────────────────────────────

    @staticmethod
    def validate_ohlc_consistency(candle: dict) -> tuple[bool, str]:
        """Verifie la coherence OHLC d'une bougie.

        Regles:
            - high >= max(open, close)
            - low <= min(open, close)
            - volume >= 0
            - close > 0
            - low > 0

        Args:
            candle: dict avec {open, high, low, close, volume}

        Returns:
            (is_valid, message)
        """
        o = candle.get("open", 0)
        h = candle.get("high", 0)
        low = candle.get("low", 0)
        c = candle.get("close", 0)
        v = candle.get("volume", 0)

        if c <= 0:
            return False, f"OHLC_INVALID: close={c} <= 0"

        if low <= 0:
            return False, f"OHLC_INVALID: low={low} <= 0"

        if h < max(o, c):
            return False, (
                f"OHLC_INVALID: high={h} < max(open={o}, close={c})={max(o, c)}"
            )

        if low > min(o, c):
            return False, (
                f"OHLC_INVALID: low={low} > min(open={o}, close={c})={min(o, c)}"
            )

        if v < 0:
            return False, f"OHLC_INVALID: volume={v} < 0"

        return True, "OK"

    # ── Detection bad tick par z-score ────────────────────────────────────────

    def detect_bad_tick(
        self,
        price: float,
        history: pd.Series,
        lookback: int = 20,
        market: str = "equities",
    ) -> tuple[bool, float]:
        """Detecte un bad tick par z-score des returns.

        Calcule le return du prix par rapport au dernier close historique,
        puis le compare a la distribution des returns recents.

        Args:
            price: prix courant a tester
            history: Series des prix close historiques
            lookback: nombre de bougies pour le calcul
            market: type de marche (pour le seuil z-score)

        Returns:
            (is_bad, z_score) — is_bad=True si z_score > seuil
        """
        threshold = self.thresholds.get(market, {}).get(
            "z_score_bad_tick", 4.0
        )

        # Prendre les N derniers prix
        recent = history.tail(lookback)
        if len(recent) < 2:
            return False, 0.0

        # Calculer les returns historiques
        returns = recent.pct_change().dropna()
        if len(returns) < 2:
            return False, 0.0

        # Return du tick courant par rapport au dernier close
        last_close = recent.iloc[-1]
        if last_close == 0:
            return False, 0.0
        current_return = (price - last_close) / last_close

        # Z-score
        mean_ret = returns.mean()
        std_ret = returns.std()
        if std_ret == 0 or np.isnan(std_ret):
            # Si ecart-type nul, tout return non nul est suspect
            if abs(current_return) > 0:
                return True, float("inf")
            return False, 0.0

        z_score = (current_return - mean_ret) / std_ret

        is_bad = abs(z_score) > threshold
        return is_bad, round(float(z_score), 4)

    # ── Detection gaps de bougies ─────────────────────────────────────────────

    def detect_missing_candles(
        self,
        timestamps: pd.DatetimeIndex,
        freq: str,
        market: str = "equities",
    ) -> list[datetime]:
        """Detecte les bougies manquantes dans une serie temporelle.

        Prend en compte les weekends et heures de marche selon le type.

        Args:
            timestamps: DatetimeIndex des bougies existantes
            freq: frequence attendue ("5min", "15min", "1h", etc.)
            market: "crypto", "fx", ou "equities"

        Returns:
            Liste des timestamps manquants (gaps inattendus)
        """
        if len(timestamps) < 2:
            return []

        # Generer la grille temporelle attendue
        full_range = pd.date_range(
            start=timestamps.min(),
            end=timestamps.max(),
            freq=freq,
        )

        # Filtrer selon les jours de marche
        trading_days = MARKET_DAYS.get(market, {0, 1, 2, 3, 4})
        full_range = full_range[full_range.dayofweek.isin(trading_days)]

        # Filtrer selon les heures de session (sauf crypto)
        if market in MARKET_SESSIONS_UTC:
            start_h, end_h = MARKET_SESSIONS_UTC[market]
            if end_h > start_h:
                full_range = full_range[
                    (full_range.hour >= start_h) & (full_range.hour < end_h)
                ]

        # Trouver les timestamps manquants
        existing = set(timestamps)
        missing = [ts.to_pydatetime() for ts in full_range if ts not in existing]

        if missing:
            self._stats["gaps_detected"] += len(missing)

        return missing

    # ── Detection donnees perimees ────────────────────────────────────────────

    def detect_stale_data(
        self,
        last_timestamp: datetime,
        market: str = "equities",
        now: datetime = None,
    ) -> tuple[bool, float]:
        """Verifie si les donnees sont perimees.

        Args:
            last_timestamp: timestamp de la derniere bougie recue
            market: type de marche
            now: heure courante (default: utcnow)

        Returns:
            (is_stale, seconds_since_last)
        """
        if now is None:
            now = datetime.now(timezone.utc)

        # S'assurer que les deux timestamps sont comparables
        if last_timestamp.tzinfo is None:
            last_timestamp = last_timestamp.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        delta = (now - last_timestamp).total_seconds()
        threshold = self.thresholds.get(market, {}).get(
            "stale_data_seconds", 60
        )

        is_stale = delta > threshold

        if is_stale:
            self._stats["stale_data_detected"] += 1

        return is_stale, round(delta, 2)

    # ── Gel de signaux par ticker ─────────────────────────────────────────────

    def freeze_signal(self, ticker: str, duration_minutes: int = 30) -> None:
        """Gele les signaux pour un ticker pendant N minutes.

        Appele automatiquement quand un bad tick est detecte.

        Args:
            ticker: symbole du ticker a geler
            duration_minutes: duree du gel en minutes
        """
        expiration = datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)
        with self._lock:
            self._frozen_tickers[ticker] = expiration

        logger.warning(
            f"DATA_QUALITY: ticker {ticker} gele pour {duration_minutes} min "
            f"(expire a {expiration.isoformat()})"
        )

    def is_frozen(self, ticker: str) -> bool:
        """Verifie si un ticker est actuellement gele.

        Nettoie automatiquement les gels expires.

        Args:
            ticker: symbole a verifier

        Returns:
            True si le ticker est gele
        """
        with self._lock:
            if ticker not in self._frozen_tickers:
                return False

            expiration = self._frozen_tickers[ticker]
            now = datetime.now(timezone.utc)
            if now >= expiration:
                del self._frozen_tickers[ticker]
                return False

            return True

    def unfreeze_signal(self, ticker: str) -> None:
        """Degele manuellement un ticker.

        Args:
            ticker: symbole a degeler
        """
        with self._lock:
            self._frozen_tickers.pop(ticker, None)

        logger.info(f"DATA_QUALITY: ticker {ticker} degele manuellement")

    def get_frozen_tickers(self) -> dict[str, datetime]:
        """Retourne la liste des tickers geles avec leur expiration."""
        with self._lock:
            now = datetime.now(timezone.utc)
            # Nettoyer les expires
            self._frozen_tickers = {
                t: exp for t, exp in self._frozen_tickers.items()
                if exp > now
            }
            return dict(self._frozen_tickers)

    # ── Rapport de qualite ────────────────────────────────────────────────────

    def get_quality_report(
        self,
        df: pd.DataFrame,
        market: str = "equities",
    ) -> dict:
        """Genere un rapport de qualite complet sur un DataFrame.

        Args:
            df: DataFrame avec colonnes OHLCV et DatetimeIndex
            market: type de marche

        Returns:
            dict avec les resultats de tous les checks
        """
        report = {
            "market": market,
            "total_rows": len(df),
            "date_range": None,
            "ohlc_invalid_count": 0,
            "ohlc_invalid_rows": [],
            "nan_count": {},
            "bad_ticks": [],
            "missing_candles": [],
            "duplicate_timestamps": 0,
            "negative_volumes": 0,
            "zero_close_count": 0,
        }

        if len(df) == 0:
            return report

        # Plage de dates
        if isinstance(df.index, pd.DatetimeIndex):
            report["date_range"] = {
                "start": df.index.min().isoformat(),
                "end": df.index.max().isoformat(),
            }

        # NaN par colonne
        ohlcv = ["open", "high", "low", "close", "volume"]
        for col in ohlcv:
            if col in df.columns:
                nan_count = int(df[col].isna().sum())
                if nan_count > 0:
                    report["nan_count"][col] = nan_count

        # Coherence OHLC ligne par ligne
        for idx, row in df.iterrows():
            candle = {
                "open": row.get("open", 0),
                "high": row.get("high", 0),
                "low": row.get("low", 0),
                "close": row.get("close", 0),
                "volume": row.get("volume", 0),
            }
            valid, msg = self.validate_ohlc_consistency(candle)
            if not valid:
                report["ohlc_invalid_count"] += 1
                if len(report["ohlc_invalid_rows"]) < 20:  # Limiter pour la taille
                    report["ohlc_invalid_rows"].append({
                        "index": str(idx),
                        "reason": msg,
                    })

        # Bad ticks via z-score
        if "close" in df.columns and len(df) >= 20:
            threshold = self.thresholds.get(market, {}).get("z_score_bad_tick", 4.0)
            returns = df["close"].pct_change(fill_method=None).dropna()
            if len(returns) > 2:
                mean_ret = returns.rolling(20, min_periods=10).mean()
                std_ret = returns.rolling(20, min_periods=10).std()
                z_scores = (returns - mean_ret) / std_ret
                bad_mask = z_scores.abs() > threshold
                bad_indices = z_scores[bad_mask]
                for idx, z in bad_indices.items():
                    if len(report["bad_ticks"]) < 50:  # Limiter
                        report["bad_ticks"].append({
                            "index": str(idx),
                            "z_score": round(float(z), 4),
                        })

        # Bougies manquantes (si DatetimeIndex)
        if isinstance(df.index, pd.DatetimeIndex) and len(df) >= 2:
            # Estimer la frequence
            diffs = df.index.to_series().diff().dropna()
            if len(diffs) > 0:
                median_diff = diffs.median()
                freq_map = {
                    1: "1min", 5: "5min", 15: "15min",
                    30: "30min", 60: "1h", 240: "4h",
                    1440: "1D",
                }
                minutes = int(median_diff.total_seconds() / 60)
                freq = freq_map.get(minutes)
                if freq:
                    missing = self.detect_missing_candles(
                        df.index, freq, market
                    )
                    report["missing_candles"] = [
                        ts.isoformat() for ts in missing[:100]  # Limiter
                    ]

        # Doublons de timestamps
        if isinstance(df.index, pd.DatetimeIndex):
            report["duplicate_timestamps"] = int(df.index.duplicated().sum())

        # Volumes negatifs
        if "volume" in df.columns:
            report["negative_volumes"] = int((df["volume"] < 0).sum())

        # Close a zero
        if "close" in df.columns:
            report["zero_close_count"] = int((df["close"] <= 0).sum())

        return report

    # ── Stats et monitoring ───────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Retourne les statistiques courantes de la garde."""
        return {
            **self._stats,
            "frozen_tickers": list(self.get_frozen_tickers().keys()),
        }

    def reset_stats(self) -> None:
        """Remet les compteurs a zero."""
        for key in self._stats:
            self._stats[key] = 0

    # ── Persistence / Logging ─────────────────────────────────────────────────

    def _log_bad_tick(self, candle: dict, z_score: float, market: str) -> None:
        """Log un bad tick dans le fichier JSONL pour analyse post-mortem.

        Fichier: data/data_quality_log.jsonl
        """
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "bad_tick",
            "market": market,
            "ticker": candle.get("ticker", candle.get("symbol", "")),
            "candle_timestamp": str(candle.get("timestamp", "")),
            "open": candle.get("open"),
            "high": candle.get("high"),
            "low": candle.get("low"),
            "close": candle.get("close"),
            "volume": candle.get("volume"),
            "z_score": round(z_score, 4),
        }

        try:
            _LOG_DIR.mkdir(parents=True, exist_ok=True)
            with open(_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as e:
            # Ne jamais bloquer le worker pour un log
            logger.debug(f"Erreur ecriture log data_quality: {e}")

    @staticmethod
    def read_quality_log(
        limit: int = 100,
        ticker: Optional[str] = None,
    ) -> list[dict]:
        """Lit les dernieres entrees du log de qualite.

        Args:
            limit: nombre max d'entrees a retourner
            ticker: filtrer par ticker (optionnel)

        Returns:
            Liste des entrees (les plus recentes en premier)
        """
        if not _LOG_FILE.exists():
            return []

        entries = []
        try:
            with open(_LOG_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if ticker and entry.get("ticker") != ticker:
                            continue
                        entries.append(entry)
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass

        # Retourner les plus recentes en premier
        return entries[-limit:][::-1]
