"""
Tests unitaires — FOMC Reaction + BCE Press Conference strategies.

Couvre :
  - FOMC signal on FOMC day (continuation LONG / SHORT)
  - FOMC no signal on non-FOMC day
  - FOMC skip si VIX > 35
  - FOMC continuation vs skip based on initial move size
  - FOMC skip zone grise (0.1%-0.3%)
  - BCE press conference reversal signal
  - BCE press conference continuation signal
  - BCE no signal on non-BCE day
  - BCE skip si decision unanime
  - Stop/TP calculations FOMC
  - Stop/TP calculations BCE
  - Edge cases (empty data, missing tickers, etc.)
"""

import sys
import pytest
import pandas as pd
import numpy as np
from datetime import date, time as dt_time, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

# Setup paths
ROOT = Path(__file__).parent.parent
BACKTESTER = ROOT / "intraday-backtesterV2"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKTESTER))

from strategies.fomc_reaction import FOMCReactionStrategy
from strategies.bce_press_conference import BCEPressConferenceStrategy
from backtest_engine import Signal


# =============================================================================
# HELPERS — fabrication de DataFrames intraday
# =============================================================================


def _make_intraday_df(
    date_str: str,
    base_price: float = 450.0,
    freq: str = "5min",
    start_time: str = "09:30",
    end_time: str = "16:00",
    move_at: str = None,
    move_pct: float = 0.0,
) -> pd.DataFrame:
    """Genere un DataFrame intraday OHLCV pour un jour donne.

    Args:
        date_str: "YYYY-MM-DD"
        base_price: prix de depart
        freq: frequence des barres
        start_time: heure de debut
        end_time: heure de fin
        move_at: heure du move (ex: "14:05") — le prix saute a cette heure
        move_pct: magnitude du move en decimal (ex: 0.005 = +0.5%)
    """
    idx = pd.date_range(
        f"{date_str} {start_time}",
        f"{date_str} {end_time}",
        freq=freq,
        tz="US/Eastern",
    )
    n = len(idx)
    prices = np.full(n, base_price)

    # Appliquer le move a partir de l'heure specifiee
    if move_at is not None:
        move_time = pd.Timestamp(f"{date_str} {move_at}", tz="US/Eastern")
        for i, ts in enumerate(idx):
            if ts >= move_time:
                prices[i] = base_price * (1 + move_pct)

    # Ajouter un peu de bruit
    noise = np.random.default_rng(42).normal(0, 0.0001, n) * base_price
    open_prices = prices + noise * 0.5
    high_prices = prices + abs(noise)
    low_prices = prices - abs(noise)
    close_prices = prices + noise * 0.3

    df = pd.DataFrame({
        "open": open_prices,
        "high": high_prices,
        "low": low_prices,
        "close": close_prices,
        "volume": np.random.default_rng(42).integers(100_000, 1_000_000, n),
    }, index=idx)

    return df


def _make_fomc_data(
    date_str: str,
    spy_move_pct: float = 0.005,
    qqq_move_pct: float = 0.004,
    vix_level: float = 20.0,
) -> dict[str, pd.DataFrame]:
    """Genere les donnees pour un jour FOMC avec un move a 14:05 ET."""
    data = {
        "SPY": _make_intraday_df(date_str, 450.0, move_at="14:05", move_pct=spy_move_pct),
        "QQQ": _make_intraday_df(date_str, 380.0, move_at="14:05", move_pct=qqq_move_pct),
    }
    # VIX : prix constant
    vix_df = _make_intraday_df(date_str, vix_level)
    data["VIX"] = vix_df
    return data


def _make_bce_data(
    date_str: str,
    decision_move_pct: float = 0.003,
    press_move_pct: float = -0.004,
    base_price: float = 25.0,
) -> dict[str, pd.DataFrame]:
    """Genere les donnees pour un jour BCE avec reaction + conference.

    decision_move_pct: move entre 09:30 et 09:45 (reaction decision)
    press_move_pct: move additionnel entre 09:45 et 10:00 (conference)
    """
    idx = pd.date_range(
        f"{date_str} 09:30",
        f"{date_str} 16:00",
        freq="5min",
        tz="US/Eastern",
    )
    n = len(idx)

    # Construire les prix avec 2 phases de move
    prices = np.full(n, base_price)
    decision_time = pd.Timestamp(f"{date_str} 09:45", tz="US/Eastern")
    press_time = pd.Timestamp(f"{date_str} 10:00", tz="US/Eastern")

    for i, ts in enumerate(idx):
        if ts >= press_time:
            # Apres conference : decision + press moves combines
            total_move = decision_move_pct + press_move_pct
            prices[i] = base_price * (1 + total_move)
        elif ts >= decision_time:
            # Reaction decision seulement
            prices[i] = base_price * (1 + decision_move_pct)

    rng = np.random.default_rng(42)
    noise = rng.normal(0, 0.0001, n) * base_price

    data = {}
    for ticker in ["EUFN", "DB", "ING", "BBVA", "SAN"]:
        df = pd.DataFrame({
            "open": prices + noise * 0.2,
            "high": prices + abs(noise),
            "low": prices - abs(noise),
            "close": prices + noise * 0.1,
            "volume": rng.integers(50_000, 500_000, n),
        }, index=idx)
        data[ticker] = df

    # SPY pour reference
    data["SPY"] = _make_intraday_df(date_str, 450.0)

    return data


# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def mock_calendar():
    """Calendrier mock avec dates FOMC et BCE controlees."""
    cal = MagicMock()
    cal.is_fomc_day = MagicMock(side_effect=lambda d: d == date(2026, 1, 28) or d == date(2026, 3, 18))
    cal.is_bce_day = MagicMock(side_effect=lambda d: d == date(2026, 1, 22) or d == date(2026, 3, 5))
    return cal


@pytest.fixture
def fomc_strategy(mock_calendar):
    return FOMCReactionStrategy(calendar=mock_calendar)


@pytest.fixture
def bce_strategy(mock_calendar):
    return BCEPressConferenceStrategy(calendar=mock_calendar)


# =============================================================================
# FOMC REACTION — Signal generation
# =============================================================================


class TestFOMCSignalOnFOMCDay:
    """Test: FOMC genere un signal le jour FOMC avec move > 0.3%."""

    def test_long_signal_on_bullish_fomc(self, fomc_strategy):
        """Move SPY +0.5% a 14:05 → LONG signal."""
        data = _make_fomc_data("2026-01-28", spy_move_pct=0.005)
        signals = fomc_strategy.generate_signals(data, date(2026, 1, 28))

        assert len(signals) >= 1
        spy_signal = [s for s in signals if s.ticker == "SPY"]
        assert len(spy_signal) == 1
        assert spy_signal[0].action == "LONG"
        assert spy_signal[0].metadata["event_type"] == "FOMC"
        assert spy_signal[0].metadata["strategy"] == "FOMC Reaction"

    def test_short_signal_on_bearish_fomc(self, fomc_strategy):
        """Move SPY -0.5% a 14:05 → SHORT signal."""
        data = _make_fomc_data("2026-01-28", spy_move_pct=-0.005)
        signals = fomc_strategy.generate_signals(data, date(2026, 1, 28))

        assert len(signals) >= 1
        spy_signal = [s for s in signals if s.ticker == "SPY"]
        assert len(spy_signal) == 1
        assert spy_signal[0].action == "SHORT"

    def test_qqq_also_generates_signal(self, fomc_strategy):
        """QQQ genere aussi un signal si move suffisant."""
        data = _make_fomc_data("2026-01-28", spy_move_pct=0.005, qqq_move_pct=0.006)
        signals = fomc_strategy.generate_signals(data, date(2026, 1, 28))

        tickers = [s.ticker for s in signals]
        assert "SPY" in tickers
        assert "QQQ" in tickers

    def test_max_2_signals(self, fomc_strategy):
        """Jamais plus de 2 signaux (SPY + QQQ)."""
        data = _make_fomc_data("2026-01-28", spy_move_pct=0.01, qqq_move_pct=0.01)
        signals = fomc_strategy.generate_signals(data, date(2026, 1, 28))
        assert len(signals) <= 2


class TestFOMCNoSignalOnNonFOMCDay:
    """Test: Pas de signal hors jour FOMC."""

    def test_no_signal_regular_day(self, fomc_strategy):
        """Un jour normal ne genere aucun signal meme avec un gros move."""
        data = _make_fomc_data("2026-01-27", spy_move_pct=0.01)  # Jour avant FOMC
        signals = fomc_strategy.generate_signals(data, date(2026, 1, 27))
        assert signals == []

    def test_no_signal_weekend(self, fomc_strategy):
        """Weekend → aucun signal."""
        data = _make_fomc_data("2026-01-25", spy_move_pct=0.01)  # Dimanche
        signals = fomc_strategy.generate_signals(data, date(2026, 1, 25))
        assert signals == []


class TestFOMCVIXFilter:
    """Test: VIX > 35 → skip."""

    def test_skip_when_vix_high(self, fomc_strategy):
        """VIX a 40 → pas de signal malgre un FOMC day avec gros move."""
        data = _make_fomc_data("2026-01-28", spy_move_pct=0.008, vix_level=40.0)
        signals = fomc_strategy.generate_signals(data, date(2026, 1, 28))
        assert signals == []

    def test_signal_when_vix_normal(self, fomc_strategy):
        """VIX a 20 → signal genere normalement."""
        data = _make_fomc_data("2026-01-28", spy_move_pct=0.005, vix_level=20.0)
        signals = fomc_strategy.generate_signals(data, date(2026, 1, 28))
        assert len(signals) >= 1

    def test_signal_when_vix_exactly_35(self, fomc_strategy):
        """VIX a exactement 35 → signal genere (seuil strict >35)."""
        data = _make_fomc_data("2026-01-28", spy_move_pct=0.005, vix_level=35.0)
        signals = fomc_strategy.generate_signals(data, date(2026, 1, 28))
        assert len(signals) >= 1

    def test_signal_when_vix_missing(self, fomc_strategy):
        """VIX absent des donnees → on ne filtre pas, signal genere."""
        data = _make_fomc_data("2026-01-28", spy_move_pct=0.005)
        del data["VIX"]
        signals = fomc_strategy.generate_signals(data, date(2026, 1, 28))
        assert len(signals) >= 1


class TestFOMCMoveSize:
    """Test: continuation vs skip en fonction du move initial."""

    def test_skip_tiny_move(self, fomc_strategy):
        """Move < 0.1% (non-event) → skip."""
        data = _make_fomc_data("2026-01-28", spy_move_pct=0.0005)  # 0.05%
        signals = fomc_strategy.generate_signals(data, date(2026, 1, 28))
        spy_signals = [s for s in signals if s.ticker == "SPY"]
        assert len(spy_signals) == 0

    def test_skip_grey_zone(self, fomc_strategy):
        """Move entre 0.1% et 0.3% (zone grise) → skip."""
        data = _make_fomc_data("2026-01-28", spy_move_pct=0.002)  # 0.2%
        signals = fomc_strategy.generate_signals(data, date(2026, 1, 28))
        spy_signals = [s for s in signals if s.ticker == "SPY"]
        assert len(spy_signals) == 0

    def test_enter_at_threshold(self, fomc_strategy):
        """Move juste au-dessus du seuil 0.3% → signal genere."""
        # 0.35% pour absorber le bruit residuel sur les close prices
        data = _make_fomc_data("2026-01-28", spy_move_pct=0.0035)
        signals = fomc_strategy.generate_signals(data, date(2026, 1, 28))
        spy_signals = [s for s in signals if s.ticker == "SPY"]
        assert len(spy_signals) == 1

    def test_large_move_high_confidence(self, fomc_strategy):
        """Move > 0.8% → confidence 'high'."""
        data = _make_fomc_data("2026-01-28", spy_move_pct=0.01)  # 1.0%
        signals = fomc_strategy.generate_signals(data, date(2026, 1, 28))
        spy_signals = [s for s in signals if s.ticker == "SPY"]
        assert len(spy_signals) == 1
        assert spy_signals[0].metadata["confidence"] == "high"


class TestFOMCStopTP:
    """Test: calcul correct des stop-loss et take-profit."""

    def test_long_stop_tp(self, fomc_strategy):
        """LONG: stop = entry - 1.5x move, TP = entry + 2.0x move."""
        data = _make_fomc_data("2026-01-28", spy_move_pct=0.005)
        signals = fomc_strategy.generate_signals(data, date(2026, 1, 28))
        spy_signals = [s for s in signals if s.ticker == "SPY"]
        assert len(spy_signals) == 1

        sig = spy_signals[0]
        # Le move est ~0.5% de 450 = ~2.25
        move_size = abs(sig.metadata["initial_move_abs"])
        expected_stop = sig.entry_price - move_size * 1.5
        expected_tp = sig.entry_price + move_size * 2.0

        assert abs(sig.stop_loss - expected_stop) < 0.01
        assert abs(sig.take_profit - expected_tp) < 0.01
        assert sig.stop_loss < sig.entry_price
        assert sig.take_profit > sig.entry_price

    def test_short_stop_tp(self, fomc_strategy):
        """SHORT: stop = entry + 1.5x move, TP = entry - 2.0x move."""
        data = _make_fomc_data("2026-01-28", spy_move_pct=-0.005)
        signals = fomc_strategy.generate_signals(data, date(2026, 1, 28))
        spy_signals = [s for s in signals if s.ticker == "SPY"]
        assert len(spy_signals) == 1

        sig = spy_signals[0]
        assert sig.stop_loss > sig.entry_price
        assert sig.take_profit < sig.entry_price


# =============================================================================
# BCE PRESS CONFERENCE — Signal generation
# =============================================================================


class TestBCEReversal:
    """Test: reversal signal quand la conference renverse la reaction."""

    def test_reversal_signal_generated(self, bce_strategy):
        """Decision haussiere + conference baissiere = reversal SHORT."""
        # Decision +0.3%, conference -0.4% (renverse)
        data = _make_bce_data("2026-01-22", decision_move_pct=0.003, press_move_pct=-0.004)
        signals = bce_strategy.generate_signals(data, date(2026, 1, 22))

        assert len(signals) >= 1
        assert signals[0].action == "SHORT"
        assert signals[0].metadata["signal_type"] == "reversal"
        assert signals[0].metadata["event_type"] == "BCE"

    def test_reversal_long_on_bearish_decision(self, bce_strategy):
        """Decision baissiere + conference haussiere = reversal LONG."""
        data = _make_bce_data("2026-01-22", decision_move_pct=-0.003, press_move_pct=0.004)
        signals = bce_strategy.generate_signals(data, date(2026, 1, 22))

        assert len(signals) >= 1
        assert signals[0].action == "LONG"
        assert signals[0].metadata["signal_type"] == "reversal"


class TestBCEContinuation:
    """Test: continuation signal quand la conference confirme la direction."""

    def test_continuation_long(self, bce_strategy):
        """Decision haussiere + conference confirme = LONG."""
        data = _make_bce_data("2026-01-22", decision_move_pct=0.003, press_move_pct=0.004)
        signals = bce_strategy.generate_signals(data, date(2026, 1, 22))

        assert len(signals) >= 1
        assert signals[0].action == "LONG"
        assert signals[0].metadata["signal_type"] == "continuation"

    def test_continuation_short(self, bce_strategy):
        """Decision baissiere + conference confirme = SHORT."""
        data = _make_bce_data("2026-01-22", decision_move_pct=-0.003, press_move_pct=-0.004)
        signals = bce_strategy.generate_signals(data, date(2026, 1, 22))

        assert len(signals) >= 1
        assert signals[0].action == "SHORT"
        assert signals[0].metadata["signal_type"] == "continuation"


class TestBCENoSignal:
    """Test: pas de signal hors jour BCE ou si decision unanime."""

    def test_no_signal_non_bce_day(self, bce_strategy):
        """Un jour normal ne genere aucun signal."""
        data = _make_bce_data("2026-01-23")  # Pas un jour BCE
        signals = bce_strategy.generate_signals(data, date(2026, 1, 23))
        assert signals == []

    def test_skip_unanimous_decision(self, bce_strategy):
        """Decision move < 0.1% (unanime) → skip."""
        data = _make_bce_data("2026-01-22", decision_move_pct=0.0005, press_move_pct=0.005)
        signals = bce_strategy.generate_signals(data, date(2026, 1, 22))
        assert signals == []

    def test_skip_weak_press_conference(self, bce_strategy):
        """Conference move trop faible (< 0.2% reversal et < 0.3% continuation)."""
        data = _make_bce_data("2026-01-22", decision_move_pct=0.003, press_move_pct=0.001)
        signals = bce_strategy.generate_signals(data, date(2026, 1, 22))
        assert signals == []


class TestBCEStopTP:
    """Test: calcul correct des stop-loss et take-profit BCE."""

    def test_long_stop_below_entry(self, bce_strategy):
        """LONG: stop < entry, TP > entry."""
        data = _make_bce_data("2026-01-22", decision_move_pct=-0.003, press_move_pct=0.004)
        signals = bce_strategy.generate_signals(data, date(2026, 1, 22))
        assert len(signals) >= 1

        sig = signals[0]
        assert sig.action == "LONG"
        assert sig.stop_loss < sig.entry_price
        # Stop = entry * (1 - 0.015)
        expected_stop = sig.entry_price * (1 - 0.015)
        assert abs(sig.stop_loss - expected_stop) < 0.01
        # TP = entry * (1 + 0.03)
        expected_tp = sig.entry_price * (1 + 0.03)
        assert abs(sig.take_profit - expected_tp) < 0.01

    def test_short_stop_above_entry(self, bce_strategy):
        """SHORT: stop > entry, TP < entry."""
        data = _make_bce_data("2026-01-22", decision_move_pct=0.003, press_move_pct=-0.004)
        signals = bce_strategy.generate_signals(data, date(2026, 1, 22))
        assert len(signals) >= 1

        sig = signals[0]
        assert sig.action == "SHORT"
        assert sig.stop_loss > sig.entry_price
        assert sig.take_profit < sig.entry_price


# =============================================================================
# EDGE CASES
# =============================================================================


class TestEdgeCases:
    """Tests de robustesse et edge cases."""

    def test_fomc_missing_spy(self, fomc_strategy):
        """FOMC day mais SPY absent → pas de signal."""
        data = {"QQQ": _make_intraday_df("2026-01-28", 380.0)}
        signals = fomc_strategy.generate_signals(data, date(2026, 1, 28))
        assert signals == []

    def test_fomc_empty_spy(self, fomc_strategy):
        """FOMC day mais SPY DataFrame vide → pas de signal."""
        data = {
            "SPY": pd.DataFrame(columns=["open", "high", "low", "close", "volume"]),
        }
        signals = fomc_strategy.generate_signals(data, date(2026, 1, 28))
        assert signals == []

    def test_bce_missing_benchmark(self, bce_strategy):
        """BCE day mais EUFN absent → pas de signal."""
        data = {"SPY": _make_intraday_df("2026-01-22", 450.0)}
        signals = bce_strategy.generate_signals(data, date(2026, 1, 22))
        assert signals == []

    def test_fomc_metadata_completeness(self, fomc_strategy):
        """Verifier que tous les champs metadata sont presents."""
        data = _make_fomc_data("2026-01-28", spy_move_pct=0.005)
        signals = fomc_strategy.generate_signals(data, date(2026, 1, 28))
        assert len(signals) >= 1

        meta = signals[0].metadata
        required_keys = [
            "strategy", "event_type", "initial_move_pct", "initial_move_abs",
            "confidence", "reaction_magnitude",
        ]
        for key in required_keys:
            assert key in meta, f"Missing metadata key: {key}"

    def test_bce_metadata_completeness(self, bce_strategy):
        """Verifier que tous les champs metadata BCE sont presents."""
        data = _make_bce_data("2026-01-22", decision_move_pct=0.003, press_move_pct=-0.004)
        signals = bce_strategy.generate_signals(data, date(2026, 1, 22))
        assert len(signals) >= 1

        meta = signals[0].metadata
        required_keys = [
            "strategy", "event_type", "signal_type", "decision_move_pct",
            "press_conference_move_pct", "confidence", "reaction_magnitude",
        ]
        for key in required_keys:
            assert key in meta, f"Missing metadata key: {key}"

    def test_fomc_signal_is_signal_instance(self, fomc_strategy):
        """Les signaux retournes sont des instances de Signal."""
        data = _make_fomc_data("2026-01-28", spy_move_pct=0.005)
        signals = fomc_strategy.generate_signals(data, date(2026, 1, 28))
        for sig in signals:
            assert isinstance(sig, Signal)

    def test_bce_signal_is_signal_instance(self, bce_strategy):
        """Les signaux retournes sont des instances de Signal."""
        data = _make_bce_data("2026-01-22", decision_move_pct=0.003, press_move_pct=-0.004)
        signals = bce_strategy.generate_signals(data, date(2026, 1, 22))
        for sig in signals:
            assert isinstance(sig, Signal)

    def test_fomc_required_tickers(self, fomc_strategy):
        """get_required_tickers retourne SPY, QQQ, VIX."""
        tickers = fomc_strategy.get_required_tickers()
        assert "SPY" in tickers
        assert "QQQ" in tickers

    def test_bce_required_tickers(self, bce_strategy):
        """get_required_tickers retourne les banques EU + SPY."""
        tickers = bce_strategy.get_required_tickers()
        assert "EUFN" in tickers
        assert "SPY" in tickers

    def test_fomc_timestamp_in_market_hours(self, fomc_strategy):
        """Le timestamp du signal est dans les heures de marche (14:05-15:55 ET)."""
        data = _make_fomc_data("2026-01-28", spy_move_pct=0.005)
        signals = fomc_strategy.generate_signals(data, date(2026, 1, 28))
        assert len(signals) >= 1

        for sig in signals:
            sig_time = sig.timestamp.time()
            assert sig_time >= dt_time(9, 35), f"Signal trop tot: {sig_time}"
            assert sig_time <= dt_time(15, 55), f"Signal trop tard: {sig_time}"

    def test_bce_max_trades(self, bce_strategy):
        """Jamais plus de MAX_TRADES_PER_DAY signaux BCE."""
        data = _make_bce_data("2026-01-22", decision_move_pct=0.005, press_move_pct=-0.006)
        signals = bce_strategy.generate_signals(data, date(2026, 1, 22))
        assert len(signals) <= bce_strategy.MAX_TRADES_PER_DAY
