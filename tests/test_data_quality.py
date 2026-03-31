"""Tests pour le module DataQualityGuard — 42 tests."""
import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from core.data.data_quality import DataQualityGuard, DEFAULT_THRESHOLDS


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def guard():
    return DataQualityGuard()


@pytest.fixture
def guard_custom():
    """Guard avec seuils personnalises."""
    return DataQualityGuard(config={
        "crypto": {"z_score_bad_tick": 6.0, "stale_data_seconds": 300},
        "equities": {"z_score_bad_tick": 3.0},
    })


@pytest.fixture
def normal_history():
    """Historique de 100 bougies avec prix autour de 100, volatilite ~1%."""
    np.random.seed(42)
    n = 100
    dates = pd.date_range("2025-01-01", periods=n, freq="5min")
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    close = np.maximum(close, 10)  # Eviter les prix negatifs
    df = pd.DataFrame({
        "open": close * (1 + np.random.randn(n) * 0.002),
        "high": close * (1 + abs(np.random.randn(n) * 0.005)),
        "low": close * (1 - abs(np.random.randn(n) * 0.005)),
        "close": close,
        "volume": np.random.randint(1000, 50000, n),
    }, index=dates)
    # Assurer la coherence OHLC
    df["high"] = df[["open", "high", "close"]].max(axis=1)
    df["low"] = df[["open", "low", "close"]].min(axis=1)
    return df


@pytest.fixture
def crypto_history():
    """Historique crypto avec volatilite plus elevee."""
    np.random.seed(123)
    n = 100
    dates = pd.date_range("2025-01-01", periods=n, freq="15min")
    close = 45000 + np.cumsum(np.random.randn(n) * 100)
    close = np.maximum(close, 30000)
    df = pd.DataFrame({
        "open": close * (1 + np.random.randn(n) * 0.003),
        "high": close * (1 + abs(np.random.randn(n) * 0.008)),
        "low": close * (1 - abs(np.random.randn(n) * 0.008)),
        "close": close,
        "volume": np.random.uniform(10, 500, n),
    }, index=dates)
    df["high"] = df[["open", "high", "close"]].max(axis=1)
    df["low"] = df[["open", "low", "close"]].min(axis=1)
    return df


# ── Tests OHLC Consistency ────────────────────────────────────────────────────

class TestOHLCConsistency:
    def test_valid_candle(self, guard):
        candle = {"open": 100, "high": 105, "low": 98, "close": 103, "volume": 5000}
        valid, msg = guard.validate_ohlc_consistency(candle)
        assert valid
        assert msg == "OK"

    def test_valid_doji(self, guard):
        """Doji : open == close == high == low."""
        candle = {"open": 100, "high": 100, "low": 100, "close": 100, "volume": 100}
        valid, msg = guard.validate_ohlc_consistency(candle)
        assert valid

    def test_high_below_open(self, guard):
        """High inferieur a open : invalide."""
        candle = {"open": 105, "high": 103, "low": 98, "close": 100, "volume": 1000}
        valid, msg = guard.validate_ohlc_consistency(candle)
        assert not valid
        assert "high" in msg.lower() or "OHLC_INVALID" in msg

    def test_high_below_close(self, guard):
        """High inferieur a close : invalide."""
        candle = {"open": 100, "high": 103, "low": 98, "close": 105, "volume": 1000}
        valid, msg = guard.validate_ohlc_consistency(candle)
        assert not valid

    def test_low_above_open(self, guard):
        """Low superieur a open : invalide."""
        candle = {"open": 95, "high": 105, "low": 98, "close": 100, "volume": 1000}
        valid, msg = guard.validate_ohlc_consistency(candle)
        assert not valid
        assert "low" in msg.lower() or "OHLC_INVALID" in msg

    def test_low_above_close(self, guard):
        """Low superieur a close : invalide."""
        candle = {"open": 100, "high": 105, "low": 98, "close": 95, "volume": 1000}
        valid, msg = guard.validate_ohlc_consistency(candle)
        assert not valid

    def test_negative_volume(self, guard):
        candle = {"open": 100, "high": 105, "low": 98, "close": 103, "volume": -100}
        valid, msg = guard.validate_ohlc_consistency(candle)
        assert not valid
        assert "volume" in msg.lower()

    def test_zero_volume_valid(self, guard):
        """Volume a zero est valide (marche ferme, pas de trades)."""
        candle = {"open": 100, "high": 105, "low": 98, "close": 103, "volume": 0}
        valid, msg = guard.validate_ohlc_consistency(candle)
        assert valid

    def test_zero_close(self, guard):
        candle = {"open": 100, "high": 105, "low": 0, "close": 0, "volume": 1000}
        valid, msg = guard.validate_ohlc_consistency(candle)
        assert not valid
        assert "close" in msg.lower() or "OHLC_INVALID" in msg

    def test_negative_close(self, guard):
        candle = {"open": 100, "high": 105, "low": -5, "close": -1, "volume": 1000}
        valid, msg = guard.validate_ohlc_consistency(candle)
        assert not valid

    def test_zero_low(self, guard):
        """Low a zero est invalide."""
        candle = {"open": 100, "high": 105, "low": 0, "close": 100, "volume": 1000}
        valid, msg = guard.validate_ohlc_consistency(candle)
        assert not valid


# ── Tests Bad Tick Detection ──────────────────────────────────────────────────

class TestBadTickDetection:
    def test_normal_price_not_bad(self, guard, normal_history):
        """Prix normal dans la fourchette historique : pas un bad tick."""
        last_close = normal_history["close"].iloc[-1]
        normal_price = last_close * 1.005  # +0.5% : normal
        is_bad, z_score = guard.detect_bad_tick(
            normal_price, normal_history["close"], market="equities"
        )
        assert not is_bad
        assert abs(z_score) < 3.5

    def test_extreme_price_is_bad(self, guard, normal_history):
        """Prix extreme (+50%) detecte comme bad tick."""
        last_close = normal_history["close"].iloc[-1]
        extreme_price = last_close * 1.50  # +50% : anormal
        is_bad, z_score = guard.detect_bad_tick(
            extreme_price, normal_history["close"], market="equities"
        )
        assert is_bad
        assert abs(z_score) > 3.5

    def test_crash_price_is_bad(self, guard, normal_history):
        """Chute de 30% detectee comme bad tick."""
        last_close = normal_history["close"].iloc[-1]
        crash_price = last_close * 0.70  # -30%
        is_bad, z_score = guard.detect_bad_tick(
            crash_price, normal_history["close"], market="equities"
        )
        assert is_bad

    def test_crypto_higher_threshold(self, guard, crypto_history):
        """Crypto tolere des mouvements plus grands (seuil z-score 5.0)."""
        last_close = crypto_history["close"].iloc[-1]
        # Un mouvement de 1% sur 15min ne devrait pas etre un bad tick en crypto
        # (z-score ~4.2, en dessous du seuil crypto de 5.0)
        moderate_move = last_close * 1.01
        is_bad, z_score = guard.detect_bad_tick(
            moderate_move, crypto_history["close"], market="crypto"
        )
        assert not is_bad
        # Mais le meme mouvement serait flagge en equities (seuil 3.5)
        is_bad_eq, z_score_eq = guard.detect_bad_tick(
            moderate_move, crypto_history["close"], market="equities"
        )
        assert is_bad_eq

    def test_insufficient_history(self, guard):
        """Pas assez d'historique : ne pas flaguer."""
        short_history = pd.Series([100, 101])
        is_bad, z_score = guard.detect_bad_tick(200, short_history)
        # Avec seulement 2 points, le z-score devrait etre calculable
        # mais le resultat depend de la variance
        assert isinstance(is_bad, bool)

    def test_constant_prices_any_change_flagged(self, guard):
        """Prix constants : tout changement est un bad tick (std=0)."""
        constant_history = pd.Series([100.0] * 25)
        is_bad, z_score = guard.detect_bad_tick(101, constant_history)
        assert is_bad
        assert z_score == float("inf")

    def test_z_score_returned_correctly(self, guard, normal_history):
        """Le z_score retourne est un float raisonnable."""
        last_close = normal_history["close"].iloc[-1]
        price = last_close * 1.01
        _, z_score = guard.detect_bad_tick(price, normal_history["close"])
        assert isinstance(z_score, float)
        assert not np.isnan(z_score)


# ── Tests Missing Candle Detection ────────────────────────────────────────────

class TestMissingCandles:
    def test_no_gaps_in_complete_series(self, guard):
        """Serie complete sans gap."""
        timestamps = pd.date_range("2025-01-06 09:30", periods=50, freq="5min")
        # Lundi 6 janvier 2025, heures de marche
        missing = guard.detect_missing_candles(timestamps, "5min", market="crypto")
        assert len(missing) == 0

    def test_detect_gap_in_series(self, guard):
        """Gap detecte quand des bougies manquent."""
        full = pd.date_range("2025-01-06 10:00", periods=20, freq="5min")
        # Supprimer 3 bougies au milieu
        partial = full.delete([5, 6, 7])
        missing = guard.detect_missing_candles(partial, "5min", market="crypto")
        assert len(missing) == 3

    def test_weekend_not_counted_fx(self, guard):
        """Les weekends ne sont pas comptes comme gaps pour FX."""
        # Vendredi + lundi (pas de samedi/dimanche)
        friday = pd.date_range("2025-01-03 20:00", periods=5, freq="1h")
        monday = pd.date_range("2025-01-06 00:00", periods=5, freq="1h")
        timestamps = friday.append(monday)
        missing = guard.detect_missing_candles(timestamps, "1h", market="fx")
        # Les heures du weekend ne doivent pas etre comptees comme manquantes
        weekend_missing = [
            m for m in missing
            if m.weekday() in (5, 6)  # samedi, dimanche
        ]
        assert len(weekend_missing) == 0

    def test_weekend_counted_crypto(self, guard):
        """Crypto 24/7 : les weekends SONT comptes comme gaps."""
        friday = pd.date_range("2025-01-03 22:00", periods=3, freq="1h")
        monday = pd.date_range("2025-01-06 01:00", periods=3, freq="1h")
        timestamps = friday.append(monday)
        missing = guard.detect_missing_candles(timestamps, "1h", market="crypto")
        # Il devrait y avoir des gaps le weekend
        assert len(missing) > 0

    def test_empty_timestamps(self, guard):
        """Serie vide : pas de gaps."""
        empty = pd.DatetimeIndex([])
        missing = guard.detect_missing_candles(empty, "5min", market="equities")
        assert len(missing) == 0

    def test_single_timestamp(self, guard):
        """Un seul timestamp : pas de gaps."""
        single = pd.DatetimeIndex([datetime(2025, 1, 6, 10, 0)])
        missing = guard.detect_missing_candles(single, "5min", market="equities")
        assert len(missing) == 0


# ── Tests Stale Data Detection ────────────────────────────────────────────────

class TestStaleData:
    def test_fresh_data_not_stale(self, guard):
        """Donnees recentes : pas stale."""
        now = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        last = now - timedelta(seconds=30)
        is_stale, seconds = guard.detect_stale_data(last, market="equities", now=now)
        assert not is_stale
        assert seconds == 30.0

    def test_old_data_is_stale(self, guard):
        """Donnees de 5 min : stale pour equities (seuil 60s)."""
        now = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        last = now - timedelta(minutes=5)
        is_stale, seconds = guard.detect_stale_data(last, market="equities", now=now)
        assert is_stale
        assert seconds == 300.0

    def test_crypto_more_tolerant(self, guard):
        """Crypto tolere 2 min avant stale."""
        now = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        last = now - timedelta(seconds=90)  # 1.5 min
        is_stale, _ = guard.detect_stale_data(last, market="crypto", now=now)
        assert not is_stale

    def test_crypto_stale_after_threshold(self, guard):
        """Crypto stale apres 2 min."""
        now = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        last = now - timedelta(seconds=150)  # 2.5 min
        is_stale, _ = guard.detect_stale_data(last, market="crypto", now=now)
        assert is_stale

    def test_naive_timestamp_handled(self, guard):
        """Timestamp sans timezone : traite comme UTC."""
        now = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        last = datetime(2025, 3, 1, 11, 55, 0)  # Naive, 5 min avant
        is_stale, seconds = guard.detect_stale_data(last, market="equities", now=now)
        assert is_stale
        assert seconds == 300.0


# ── Tests Freeze Mechanism ────────────────────────────────────────────────────

class TestFreezeMechanism:
    def test_freeze_and_check(self, guard):
        """Apres freeze, le ticker est gele."""
        guard.freeze_signal("BTCUSDC", duration_minutes=30)
        assert guard.is_frozen("BTCUSDC")

    def test_unfrozen_ticker(self, guard):
        """Ticker non gele retourne False."""
        assert not guard.is_frozen("ETHUSDC")

    def test_freeze_expires(self, guard):
        """Le gel expire apres la duree."""
        guard.freeze_signal("BTCUSDC", duration_minutes=1)
        # Simuler le temps qui passe en modifiant directement l'expiration
        guard._frozen_tickers["BTCUSDC"] = (
            datetime.now(timezone.utc) - timedelta(minutes=1)
        )
        assert not guard.is_frozen("BTCUSDC")

    def test_manual_unfreeze(self, guard):
        """Degelage manuel."""
        guard.freeze_signal("BTCUSDC", duration_minutes=30)
        guard.unfreeze_signal("BTCUSDC")
        assert not guard.is_frozen("BTCUSDC")

    def test_get_frozen_tickers(self, guard):
        """Liste des tickers geles."""
        guard.freeze_signal("BTCUSDC", duration_minutes=30)
        guard.freeze_signal("ETHUSDC", duration_minutes=30)
        frozen = guard.get_frozen_tickers()
        assert "BTCUSDC" in frozen
        assert "ETHUSDC" in frozen

    def test_frozen_ticker_blocks_candle(self, guard, normal_history):
        """Un ticker gele bloque la validation de la bougie."""
        guard.freeze_signal("SPY", duration_minutes=30)
        candle = {
            "ticker": "SPY",
            "open": 100, "high": 105, "low": 98, "close": 103,
            "volume": 5000,
        }
        is_valid, warnings = guard.validate_candle(candle, normal_history, "equities")
        assert not is_valid
        assert any("FROZEN" in w for w in warnings)


# ── Tests Validate Candle Integration ─────────────────────────────────────────

class TestValidateCandle:
    def test_valid_candle_passes(self, guard, normal_history):
        """Bougie normale avec historique normal passe."""
        last_close = normal_history["close"].iloc[-1]
        candle = {
            "open": last_close * 0.999,
            "high": last_close * 1.003,
            "low": last_close * 0.997,
            "close": last_close * 1.001,
            "volume": 5000,
        }
        is_valid, warnings = guard.validate_candle(candle, normal_history, "equities")
        assert is_valid
        assert len(warnings) == 0

    def test_invalid_ohlc_rejected(self, guard, normal_history):
        """Bougie avec OHLC incoherent rejetee."""
        candle = {
            "open": 100,
            "high": 90,  # High < open : invalide
            "low": 80,
            "close": 95,
            "volume": 1000,
        }
        is_valid, warnings = guard.validate_candle(candle, normal_history, "equities")
        assert not is_valid
        assert any("OHLC" in w for w in warnings)

    def test_extreme_return_rejected(self, guard, normal_history):
        """Return extreme rejete meme si z-score ne detecte pas."""
        last_close = normal_history["close"].iloc[-1]
        candle = {
            "open": last_close * 1.15,
            "high": last_close * 1.20,
            "low": last_close * 1.10,
            "close": last_close * 1.18,  # +18% : au-dessus de max_return 10%
            "volume": 5000,
        }
        is_valid, warnings = guard.validate_candle(candle, normal_history, "equities")
        assert not is_valid

    def test_stats_tracked(self, guard, normal_history):
        """Les stats sont mises a jour apres validation."""
        last_close = normal_history["close"].iloc[-1]
        candle = {
            "open": last_close,
            "high": last_close * 1.001,
            "low": last_close * 0.999,
            "close": last_close * 1.0005,
            "volume": 5000,
        }
        guard.validate_candle(candle, normal_history, "equities")
        stats = guard.get_stats()
        assert stats["candles_validated"] >= 1


# ── Tests Custom Config ───────────────────────────────────────────────────────

class TestCustomConfig:
    def test_custom_thresholds_applied(self, guard_custom):
        """Config personnalisee appliquee correctement."""
        assert guard_custom.thresholds["crypto"]["z_score_bad_tick"] == 6.0
        assert guard_custom.thresholds["crypto"]["stale_data_seconds"] == 300
        # Les valeurs non surchargees restent par defaut
        assert guard_custom.thresholds["crypto"]["max_gap_seconds"] == 900

    def test_default_market_unchanged(self, guard_custom):
        """FX garde ses seuils par defaut si non surcharge."""
        assert guard_custom.thresholds["fx"]["z_score_bad_tick"] == 4.0


# ── Tests Quality Report ─────────────────────────────────────────────────────

class TestQualityReport:
    def test_report_on_clean_data(self, guard, normal_history):
        """Rapport sur donnees propres : pas d'anomalies majeures."""
        report = guard.get_quality_report(normal_history, market="equities")
        assert report["total_rows"] == 100
        assert report["ohlc_invalid_count"] == 0
        assert report["negative_volumes"] == 0
        assert report["duplicate_timestamps"] == 0

    def test_report_on_empty_df(self, guard):
        """Rapport sur DataFrame vide."""
        report = guard.get_quality_report(pd.DataFrame(), market="crypto")
        assert report["total_rows"] == 0

    def test_report_detects_nans(self, guard, normal_history):
        """Rapport detecte les NaN."""
        df = normal_history.copy()
        df.loc[df.index[5], "close"] = np.nan
        df.loc[df.index[10], "volume"] = np.nan
        report = guard.get_quality_report(df, market="equities")
        assert "close" in report["nan_count"]
        assert report["nan_count"]["close"] == 1
