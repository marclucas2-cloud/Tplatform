"""
Tests unitaires — ImplementationShortfall (ROC-005).

Couvre :
  - test_on_signal_records : enregistrement correct du signal
  - test_on_fill_computes_is : calcul IS correct
  - test_slippage_decomposition : composante slippage isolee
  - test_latency_decomposition : composante latency isolee
  - test_commission_included : composante commission incluse
  - test_spread_included : composante spread incluse
  - test_alert_on_high_is : alerte quand IS depasse le seuil
  - test_report_by_strategy : ventilation par strategie
  - test_report_by_symbol : ventilation par symbole
  - test_report_worst_trades : top 5 pires trades
  - test_report_recommendations : recommandations auto
  - test_annual_cost_estimate : estimation cout annuel
"""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Setup paths
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.implementation_shortfall import TRADING_DAYS_PER_YEAR, ImplementationShortfall

# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def tracker():
    """ImplementationShortfall sans alerter."""
    return ImplementationShortfall(alert_threshold_bps=5.0)


@pytest.fixture
def mock_alerter():
    """Mock alerter callback."""
    return MagicMock()


@pytest.fixture
def tracker_with_alert(mock_alerter):
    """ImplementationShortfall avec alerter mock."""
    return ImplementationShortfall(
        alert_threshold_bps=5.0,
        alerter=mock_alerter,
    )


@pytest.fixture
def now_iso():
    """Timestamp ISO actuel."""
    return datetime.now(UTC).isoformat()


def _make_recent_timestamp(hours_ago=0):
    """Genere un timestamp recent pour que les records soient dans la fenetre du rapport."""
    dt = datetime.now(UTC) - timedelta(hours=hours_ago)
    return dt.isoformat()


# =============================================================================
# TESTS
# =============================================================================

class TestImplementationShortfall:
    """Tests pour ImplementationShortfall."""

    def test_on_signal_records(self, tracker):
        """on_signal enregistre correctement le signal."""
        tracker.on_signal(
            signal_id="SIG001",
            symbol="EURUSD",
            signal_price=1.1000,
            mid_price=1.0995,
            spread=0.0002,
            strategy="EUR_USD_Trend",
            timestamp="2026-03-28T10:00:00+00:00",
        )

        assert "SIG001" in tracker._signals
        sig = tracker._signals["SIG001"]
        assert sig["symbol"] == "EURUSD"
        assert sig["signal_price"] == 1.1000
        assert sig["mid_price"] == 1.0995
        assert sig["spread"] == 0.0002
        assert sig["strategy"] == "EUR_USD_Trend"

    def test_on_fill_computes_is(self, tracker):
        """on_fill calcule l'IS total correctement."""
        ts = _make_recent_timestamp()

        tracker.on_signal(
            signal_id="SIG002",
            symbol="AAPL",
            signal_price=150.00,
            mid_price=149.95,
            spread=0.04,
            strategy="Momentum",
            timestamp=ts,
        )

        result = tracker.on_fill(
            signal_id="SIG002",
            fill_price=150.05,
            fill_qty=10,
            commission=1.00,
            timestamp=ts,
            side="BUY",
        )

        assert result is not None
        assert "total_is_bps" in result
        assert "slippage_bps" in result
        assert "latency_bps" in result
        assert "commission_bps" in result
        assert "spread_bps" in result
        # IS total = somme des composantes
        expected_total = (
            result["slippage_bps"] + result["latency_bps"] +
            result["commission_bps"] + result["spread_bps"]
        )
        assert abs(result["total_is_bps"] - expected_total) < 0.01

    def test_slippage_decomposition(self, tracker):
        """Composante slippage : fill_price vs signal_price."""
        ts = _make_recent_timestamp()

        # Signal = mid = 100.00, spread = 0 -> pas de latency ni spread
        tracker.on_signal(
            signal_id="SIG_SLIP",
            symbol="TEST",
            signal_price=100.00,
            mid_price=100.00,
            spread=0.0,
            strategy="TestStrat",
            timestamp=ts,
        )

        # Fill a 100.10 -> slippage de 10 bps (BUY, adverse)
        result = tracker.on_fill(
            signal_id="SIG_SLIP",
            fill_price=100.10,
            fill_qty=10,
            commission=0.0,
            timestamp=ts,
            side="BUY",
        )

        # Slippage = (100.10 - 100.00) / 100.00 * 10000 = 10 bps
        assert abs(result["slippage_bps"] - 10.0) < 0.01
        # Latency = 0 (signal == mid)
        assert abs(result["latency_bps"]) < 0.01
        # Commission = 0
        assert abs(result["commission_bps"]) < 0.01
        # Spread = 0
        assert abs(result["spread_bps"]) < 0.01

    def test_latency_decomposition(self, tracker):
        """Composante latency : signal_price vs mid_price."""
        ts = _make_recent_timestamp()

        # Mid = 100.00, signal = 100.05 -> marche a bouge de 5 bps
        tracker.on_signal(
            signal_id="SIG_LAT",
            symbol="TEST",
            signal_price=100.05,
            mid_price=100.00,
            spread=0.0,
            strategy="TestStrat",
            timestamp=ts,
        )

        # Fill exactement au signal price -> slippage = 0
        result = tracker.on_fill(
            signal_id="SIG_LAT",
            fill_price=100.05,
            fill_qty=10,
            commission=0.0,
            timestamp=ts,
            side="BUY",
        )

        # Latency = (100.05 - 100.00) / 100.00 * 10000 = 5 bps (BUY direction)
        assert abs(result["latency_bps"] - 5.0) < 0.01
        # Slippage = 0 (fill == signal)
        assert abs(result["slippage_bps"]) < 0.01

    def test_commission_included(self, tracker):
        """Composante commission correctement incluse."""
        ts = _make_recent_timestamp()

        tracker.on_signal(
            signal_id="SIG_COMM",
            symbol="TEST",
            signal_price=100.00,
            mid_price=100.00,
            spread=0.0,
            strategy="TestStrat",
            timestamp=ts,
        )

        # Fill au prix signal, mais commission de $1 sur notional $2000
        result = tracker.on_fill(
            signal_id="SIG_COMM",
            fill_price=100.00,
            fill_qty=20,
            commission=1.0,
            timestamp=ts,
            side="BUY",
        )

        # Commission = 1.0 / (100.00 * 20) * 10000 = 5.0 bps
        assert abs(result["commission_bps"] - 5.0) < 0.01
        # Slippage et latency = 0
        assert abs(result["slippage_bps"]) < 0.01
        assert abs(result["latency_bps"]) < 0.01

    def test_spread_included(self, tracker):
        """Composante spread (demi-spread) correctement incluse."""
        ts = _make_recent_timestamp()

        # Spread de 0.10 sur un prix de 100.00
        tracker.on_signal(
            signal_id="SIG_SPREAD",
            symbol="TEST",
            signal_price=100.00,
            mid_price=100.00,
            spread=0.10,
            strategy="TestStrat",
            timestamp=ts,
        )

        result = tracker.on_fill(
            signal_id="SIG_SPREAD",
            fill_price=100.00,
            fill_qty=10,
            commission=0.0,
            timestamp=ts,
            side="BUY",
        )

        # Spread = (0.10 / 2) / 100.00 * 10000 = 5.0 bps
        assert abs(result["spread_bps"] - 5.0) < 0.01
        # Slippage et latency = 0
        assert abs(result["slippage_bps"]) < 0.01
        assert abs(result["latency_bps"]) < 0.01

    def test_alert_on_high_is(self, tracker_with_alert, mock_alerter):
        """Alerte quand IS depasse le seuil (5 bps)."""
        ts = _make_recent_timestamp()

        tracker_with_alert.on_signal(
            signal_id="SIG_ALERT",
            symbol="EURUSD",
            signal_price=1.1000,
            mid_price=1.1000,
            spread=0.0,
            strategy="FX_Trend",
            timestamp=ts,
        )

        # Fill tres eloigne -> IS > 5 bps
        tracker_with_alert.on_fill(
            signal_id="SIG_ALERT",
            fill_price=1.1010,
            fill_qty=10000,
            commission=0.0,
            timestamp=ts,
            side="BUY",
        )

        # Slippage = (1.1010 - 1.1000) / 1.1000 * 10000 ~= 9.09 bps -> > 5 bps
        mock_alerter.assert_called_once()
        call_args = mock_alerter.call_args
        assert "IS ELEVE" in call_args[0][0]
        assert call_args[1]["level"] == "warning"

    def test_report_by_strategy(self, tracker):
        """Rapport ventile correctement par strategie."""
        ts = _make_recent_timestamp()

        # 2 trades de strategies differentes
        for i, strat in enumerate(["StratA", "StratB", "StratA"]):
            sid = f"SIG_STRAT_{i}"
            tracker.on_signal(
                signal_id=sid,
                symbol="TEST",
                signal_price=100.00,
                mid_price=100.00,
                spread=0.0,
                strategy=strat,
                timestamp=ts,
            )
            # Slippage variable
            fill = 100.05 if strat == "StratA" else 100.02
            tracker.on_fill(
                signal_id=sid,
                fill_price=fill,
                fill_qty=10,
                commission=0.0,
                timestamp=ts,
                side="BUY",
            )

        report = tracker.get_report(period_days=30)

        assert "StratA" in report["by_strategy"]
        assert "StratB" in report["by_strategy"]
        assert report["by_strategy"]["StratA"]["count"] == 2
        assert report["by_strategy"]["StratB"]["count"] == 1
        # StratA a un IS plus eleve (5 bps) que StratB (2 bps)
        assert report["by_strategy"]["StratA"]["avg_is"] > report["by_strategy"]["StratB"]["avg_is"]

    def test_report_by_symbol(self, tracker):
        """Rapport ventile correctement par symbole."""
        ts = _make_recent_timestamp()

        for i, sym in enumerate(["AAPL", "MSFT", "AAPL"]):
            sid = f"SIG_SYM_{i}"
            tracker.on_signal(
                signal_id=sid,
                symbol=sym,
                signal_price=100.00,
                mid_price=100.00,
                spread=0.0,
                strategy="TestStrat",
                timestamp=ts,
            )
            tracker.on_fill(
                signal_id=sid,
                fill_price=100.03,
                fill_qty=10,
                commission=0.0,
                timestamp=ts,
                side="BUY",
            )

        report = tracker.get_report(period_days=30)

        assert "AAPL" in report["by_symbol"]
        assert "MSFT" in report["by_symbol"]
        assert report["by_symbol"]["AAPL"]["count"] == 2
        assert report["by_symbol"]["MSFT"]["count"] == 1

    def test_report_worst_trades(self, tracker):
        """Top 5 pires trades par IS."""
        ts = _make_recent_timestamp()

        # Creer 7 trades avec IS croissant
        for i in range(7):
            sid = f"SIG_WORST_{i}"
            tracker.on_signal(
                signal_id=sid,
                symbol="TEST",
                signal_price=100.00,
                mid_price=100.00,
                spread=0.0,
                strategy="TestStrat",
                timestamp=ts,
            )
            # IS croissant : 1 bps, 2 bps, ..., 7 bps
            fill = 100.00 + (i + 1) * 0.01
            tracker.on_fill(
                signal_id=sid,
                fill_price=fill,
                fill_qty=10,
                commission=0.0,
                timestamp=ts,
                side="BUY",
            )

        report = tracker.get_report(period_days=30)

        assert len(report["worst_trades"]) == 5
        # Le pire trade doit etre SIG_WORST_6 (7 bps)
        assert report["worst_trades"][0]["signal_id"] == "SIG_WORST_6"
        # Verifie l'ordre decroissant
        for j in range(len(report["worst_trades"]) - 1):
            assert report["worst_trades"][j]["total_is_bps"] >= report["worst_trades"][j + 1]["total_is_bps"]

    def test_report_recommendations(self, tracker):
        """Recommandations automatiques generees quand IS eleve."""
        ts = _make_recent_timestamp()

        # Creer 5 trades avec IS eleve pour une strategie
        for i in range(5):
            sid = f"SIG_RECO_{i}"
            tracker.on_signal(
                signal_id=sid,
                symbol="EURUSD",
                signal_price=100.00,
                mid_price=100.00,
                spread=0.0,
                strategy="HighCostStrat",
                timestamp=ts,
            )
            # IS ~5 bps chacun -> avg > 3 bps -> recommendation
            tracker.on_fill(
                signal_id=sid,
                fill_price=100.05,
                fill_qty=10,
                commission=0.0,
                timestamp=ts,
                side="BUY",
            )

        report = tracker.get_report(period_days=30)

        assert len(report["recommendations"]) > 0
        # Au moins une recommandation pour la strategie a IS eleve
        reco_text = " ".join(report["recommendations"])
        assert "HighCostStrat" in reco_text or "limit orders" in reco_text.lower()

    def test_annual_cost_estimate(self, tracker):
        """Estimation du cout annuel basee sur la periode observee."""
        ts = _make_recent_timestamp()

        # 1 trade avec IS connu
        tracker.on_signal(
            signal_id="SIG_ANNUAL",
            symbol="TEST",
            signal_price=100.00,
            mid_price=100.00,
            spread=0.0,
            strategy="TestStrat",
            timestamp=ts,
        )
        tracker.on_fill(
            signal_id="SIG_ANNUAL",
            fill_price=100.10,
            fill_qty=100,
            commission=0.0,
            timestamp=ts,
            side="BUY",
        )

        report = tracker.get_report(period_days=30)

        assert "estimated_annual_cost_usd" in report
        # Le cout doit etre > 0 car on a du slippage
        assert report["estimated_annual_cost_usd"] > 0

        # Verification calcul : total_cost / period_days * 252
        record = tracker.get_record("SIG_ANNUAL")
        expected_daily = record["total_cost"] / 30
        expected_annual = expected_daily * TRADING_DAYS_PER_YEAR
        assert abs(report["estimated_annual_cost_usd"] - expected_annual) < 0.1

    def test_get_record_returns_none_for_unknown(self, tracker):
        """get_record retourne None pour un signal inconnu."""
        result = tracker.get_record("UNKNOWN_SIG")
        assert result is None

    def test_on_fill_unknown_signal(self, tracker):
        """on_fill retourne None si le signal n'a pas ete enregistre."""
        result = tracker.on_fill(
            signal_id="UNKNOWN",
            fill_price=100.0,
            fill_qty=10,
            commission=0.0,
            side="BUY",
        )
        assert result is None
