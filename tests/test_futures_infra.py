"""
Tests infrastructure futures — 20+ tests couvrant :
  - Contract resolution (front month, next month)
  - Roll detection et execution
  - Margin calculation et validation
  - Points to dollars conversion
  - SmartRouter futures routing
  - Data download validation
  - Edge cases (near expiry, holiday, pre-market)

Aucun appel reseau reel — tout est mocke.
"""

import sys
import pytest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def contract_mgr():
    """FuturesContractManager avec config par defaut."""
    from core.broker.ibkr_futures import FuturesContractManager
    return FuturesContractManager()


@pytest.fixture
def margin_tracker():
    """FuturesMarginTracker avec $25K de capital."""
    from core.futures_margin import FuturesMarginTracker
    return FuturesMarginTracker(total_capital=25_000)


@pytest.fixture
def roll_manager():
    """FuturesRollManager en mode dry-run (sans client)."""
    from core.futures_roll import FuturesRollManager
    return FuturesRollManager(futures_client=None)


@pytest.fixture
def sample_positions():
    """Positions futures exemples."""
    return [
        {"symbol": "MES", "qty": 1},
        {"symbol": "MNQ", "qty": 1},
    ]


# =============================================================================
# TEST 1-4 : Contract Resolution
# =============================================================================

class TestContractResolution:
    """Tests de resolution de contrats futures."""

    def test_front_month_quarterly(self, contract_mgr):
        """Front month MES en janvier → contrat Mars (H)."""
        ref = date(2026, 1, 15)
        front = contract_mgr.get_front_month("MES", ref)

        assert front["symbol"] == "MES"
        assert front["month_code"] == "H"  # Mars
        assert front["expiry"] == "2026-03-20"
        assert front["exchange"] == "CME"
        assert front["multiplier"] == 5

    def test_front_month_after_expiry(self, contract_mgr):
        """Front month MES apres expiry Mars → contrat Juin (M)."""
        ref = date(2026, 3, 21)  # Lendemain de l'expiry Mars
        front = contract_mgr.get_front_month("MES", ref)

        assert front["month_code"] == "M"  # Juin
        assert "2026-06" in front["expiry"]

    def test_next_month_quarterly(self, contract_mgr):
        """Next month MES en janvier → contrat Juin (M)."""
        ref = date(2026, 1, 15)
        next_m = contract_mgr.get_next_month("MES", ref)

        assert next_m["month_code"] == "M"  # Juin
        assert "2026-06" in next_m["expiry"]

    def test_front_month_monthly_mcl(self, contract_mgr):
        """MCL a des expirations mensuelles (tous les mois)."""
        ref = date(2026, 4, 1)
        front = contract_mgr.get_front_month("MCL", ref)

        # Devrait etre le prochain mois d'expiry
        assert front["symbol"] == "MCL"
        assert front["exchange"] == "NYMEX"
        expiry_date = date.fromisoformat(front["expiry"])
        assert expiry_date >= ref

    def test_local_symbol_format(self, contract_mgr):
        """Le local_symbol a le format {SYMBOL}{MONTH}{YY}."""
        ref = date(2026, 1, 15)
        front = contract_mgr.get_front_month("MES", ref)

        assert front["local_symbol"] == "MESH26"

    def test_unknown_symbol_raises(self, contract_mgr):
        """Symbole inconnu leve ValueError."""
        with pytest.raises(ValueError, match="Contrat futures inconnu"):
            contract_mgr.get_front_month("INVALID")


# =============================================================================
# TEST 5-8 : Roll Detection
# =============================================================================

class TestRollDetection:
    """Tests de detection et scheduling des rolls."""

    def test_should_roll_far_from_expiry(self, contract_mgr):
        """Pas de roll quand on est loin de l'expiry."""
        ref = date(2026, 1, 15)  # 64 jours avant expiry Mars
        assert contract_mgr.should_roll("MES", days_before_expiry=5, ref_date=ref) is False

    def test_should_roll_near_expiry(self, contract_mgr):
        """Roll necessaire quand on est a 3 jours de l'expiry."""
        ref = date(2026, 3, 17)  # 3 jours avant expiry 2026-03-20
        assert contract_mgr.should_roll("MES", days_before_expiry=5, ref_date=ref) is True

    def test_should_roll_on_expiry_day(self, contract_mgr):
        """Roll necessaire le jour de l'expiry."""
        ref = date(2026, 3, 20)  # Jour d'expiry
        assert contract_mgr.should_roll("MES", days_before_expiry=5, ref_date=ref) is True

    def test_roll_schedule(self, roll_manager):
        """Le calendrier de roll retourne les infos correctes."""
        ref = date(2026, 1, 15)
        schedule = roll_manager.get_roll_schedule(
            symbols=["MES", "MNQ"], ref_date=ref
        )

        assert len(schedule) == 2
        for entry in schedule:
            assert "symbol" in entry
            assert "expiry" in entry
            assert "days_to_expiry" in entry
            assert "needs_roll" in entry
            assert entry["days_to_expiry"] > 0

        # Trie par days_to_expiry
        assert schedule[0]["days_to_expiry"] <= schedule[1]["days_to_expiry"]


# =============================================================================
# TEST 9-10 : Roll Execution (dry-run)
# =============================================================================

class TestRollExecution:
    """Tests d'execution des rolls (dry-run)."""

    def test_roll_dry_run_near_expiry(self, roll_manager):
        """Roll dry-run execute quand on est pres de l'expiry."""
        ref = date(2026, 3, 17)
        result = roll_manager.check_and_execute_roll(
            symbol="MES",
            current_qty=2,
            current_direction="BUY",
            ref_date=ref,
        )

        assert result is not None
        assert result["symbol"] == "MES"
        assert result["status"] == "dry_run"
        assert result["qty"] == 2
        assert result["direction"] == "BUY"
        assert "old_contract" in result
        assert "new_contract" in result

    def test_roll_not_needed(self, roll_manager):
        """Pas de roll quand on est loin de l'expiry."""
        ref = date(2026, 1, 15)
        result = roll_manager.check_and_execute_roll(
            symbol="MES",
            current_qty=1,
            ref_date=ref,
        )

        assert result is None

    def test_roll_no_position(self, roll_manager):
        """Pas de roll si aucune position ouverte."""
        ref = date(2026, 3, 17)
        result = roll_manager.check_and_execute_roll(
            symbol="MES",
            current_qty=0,
            ref_date=ref,
        )

        assert result is None


# =============================================================================
# TEST 11-15 : Margin Calculation
# =============================================================================

class TestMarginCalculation:
    """Tests du suivi de marge."""

    def test_margin_used_single_position(self, margin_tracker):
        """Marge utilisee pour 1 contrat MES = $1400."""
        positions = [{"symbol": "MES", "qty": 1}]
        used = margin_tracker.calculate_margin_used(positions)
        assert used == 1400.0

    def test_margin_used_multiple_positions(self, margin_tracker, sample_positions):
        """Marge utilisee pour MES + MNQ = $1400 + $1800."""
        used = margin_tracker.calculate_margin_used(sample_positions)
        assert used == 3200.0

    def test_margin_available(self, margin_tracker, sample_positions):
        """Marge disponible = max(30% capital) - used."""
        available = margin_tracker.calculate_margin_available(sample_positions)
        max_margin = 25_000 * 0.30  # $7500
        expected = max_margin - 3200  # $4300
        assert available == expected

    def test_margin_health_green(self, margin_tracker):
        """Sante marge = green quand utilisation < 70%."""
        positions = [{"symbol": "MES", "qty": 1}]
        health = margin_tracker.check_margin_health(positions)

        assert health["alert_level"] == "green"
        assert health["used"] == 1400.0
        assert health["ratio"] < 0.70
        assert len(health["violations"]) == 0

    def test_margin_health_violations(self):
        """Detection des violations de marge."""
        from core.futures_margin import FuturesMarginTracker

        # Capital tres petit = violations
        tracker = FuturesMarginTracker(total_capital=5_000)
        positions = [{"symbol": "MES", "qty": 2}]  # 2 * $1400 = $2800

        health = tracker.check_margin_health(positions)

        # $2800 / ($5000 * 0.30) = 186% → rouge
        assert health["alert_level"] == "red"
        assert len(health["violations"]) > 0


# =============================================================================
# TEST 16-17 : Max Contracts
# =============================================================================

class TestMaxContracts:
    """Tests du calcul max de contrats."""

    def test_max_contracts_mes_25k(self, margin_tracker):
        """Max MES pour $25K: floor($2500 / $1400) = 1."""
        max_c = margin_tracker.max_contracts("MES")
        assert max_c == 1

    def test_max_contracts_mcl_25k(self, margin_tracker):
        """Max MCL pour $25K: floor($2500 / $600) = 4 (mais limited by available margin)."""
        max_c = margin_tracker.max_contracts("MCL")
        # $2500 / $600 = 4.16 → 4 par position
        # $7500 / $600 = 12.5 → 12 par total dispo
        # min(4, 12) = 4
        assert max_c == 4

    def test_max_contracts_with_existing_positions(self, margin_tracker):
        """Max contracts diminue avec des positions existantes."""
        existing = [{"symbol": "MES", "qty": 1}, {"symbol": "MNQ", "qty": 1}]
        # Used: $3200, Available: $4300
        max_c = margin_tracker.max_contracts("MCL", current_positions=existing)
        # min(4 by position, floor(4300/600)=7 by available) = 4
        assert max_c == 4


# =============================================================================
# TEST 18-19 : Points to Dollars Conversion
# =============================================================================

class TestConversion:
    """Tests de conversion points ↔ dollars."""

    def test_points_to_dollars_mes(self, contract_mgr):
        """10 points MES = 10 * $5 = $50."""
        dollars = contract_mgr.points_to_dollars("MES", 10.0)
        assert dollars == 50.0

    def test_dollars_to_points_mnq(self, contract_mgr):
        """$100 MNQ = 100 / $2 = 50 points."""
        points = contract_mgr.dollars_to_points("MNQ", 100.0)
        assert points == 50.0

    def test_roundtrip_conversion(self, contract_mgr):
        """Conversion aller-retour = identite."""
        for symbol in ["MES", "MNQ", "MCL", "MGC"]:
            original_pts = 42.5
            dollars = contract_mgr.points_to_dollars(symbol, original_pts)
            back = contract_mgr.dollars_to_points(symbol, dollars)
            assert abs(back - original_pts) < 1e-10


# =============================================================================
# TEST 20-22 : SmartRouter Futures Routing
# =============================================================================

class TestSmartRouterFutures:
    """Tests du routing futures dans le SmartRouter."""

    def test_detect_futures_symbol(self):
        """SmartRouter reconnait les symboles futures."""
        from core.broker.factory import SmartRouter
        assert SmartRouter.detect_asset_type("MES") == "future"
        assert SmartRouter.detect_asset_type("MNQ") == "future"
        assert SmartRouter.detect_asset_type("MCL") == "future"
        assert SmartRouter.detect_asset_type("MGC") == "future"
        assert SmartRouter.detect_asset_type("ES") == "future"
        assert SmartRouter.detect_asset_type("NQ") == "future"

    def test_detect_equity_symbol(self):
        """SmartRouter reconnait les symboles equity."""
        from core.broker.factory import SmartRouter
        assert SmartRouter.detect_asset_type("AAPL") == "equity"
        assert SmartRouter.detect_asset_type("SPY") == "equity"

    @patch.dict("os.environ", {"IBKR_HOST": "127.0.0.1", "ALPACA_API_KEY": "test"})
    def test_futures_route_to_ibkr(self):
        """Symbole futures route vers IBKR (pas Alpaca)."""
        from core.broker.factory import SmartRouter

        router = SmartRouter()
        # Mocker le get_broker pour eviter la connexion reelle
        mock_ibkr = MagicMock()
        mock_ibkr.name = "ibkr"
        router._brokers["ibkr"] = mock_ibkr

        broker = router.route(symbol="MES")
        assert broker.name == "ibkr"

    @patch.dict("os.environ", {"ALPACA_API_KEY": "test"}, clear=True)
    def test_futures_without_ibkr_raises(self):
        """Futures sans IBKR leve BrokerError."""
        from core.broker.factory import SmartRouter
        from core.broker.base import BrokerError

        router = SmartRouter()
        with pytest.raises(BrokerError, match="requiert IBKR"):
            router.route(symbol="MES")

    @patch.dict("os.environ", {"IBKR_HOST": "127.0.0.1"})
    def test_is_futures_symbol(self):
        """is_futures_symbol helper."""
        from core.broker.factory import SmartRouter
        router = SmartRouter()
        assert router.is_futures_symbol("MES") is True
        assert router.is_futures_symbol("AAPL") is False


# =============================================================================
# TEST 23-24 : Capital Validation
# =============================================================================

class TestCapitalValidation:
    """Tests de validation capital vs contrats."""

    def test_micro_allowed_25k(self, contract_mgr):
        """Micro-contrats autorises avec $25K."""
        ok, msg = contract_mgr.validate_capital("MES", 25_000)
        assert ok is True

    def test_fullsize_blocked_25k(self, contract_mgr):
        """Full-size refuses avec $25K."""
        ok, msg = contract_mgr.validate_capital("ES", 25_000)
        assert ok is False
        assert "full-size" in msg.lower() or "REFUSE" in msg

    def test_fullsize_allowed_100k(self, contract_mgr):
        """Full-size autorises avec $100K+."""
        ok, msg = contract_mgr.validate_capital("ES", 150_000)
        assert ok is True

    def test_margin_validation_new_position(self, margin_tracker):
        """Validation d'une nouvelle position passe les checks."""
        ok, msg = margin_tracker.validate_new_position("MES", qty=1)
        assert ok is True

    def test_margin_validation_too_many_contracts(self, margin_tracker):
        """Trop de contrats refuse par la validation."""
        ok, msg = margin_tracker.validate_new_position("MES", qty=10)
        assert ok is False
        assert "REFUSE" in msg


# =============================================================================
# TEST 25-26 : Data Download Validation
# =============================================================================

class TestDataValidation:
    """Tests de validation des donnees telechargees."""

    def test_etf_vs_futures_validation_pass(self):
        """Validation passe quand les rendements sont similaires."""
        from scripts.download_futures_data import validate_etf_vs_futures

        # Generer des donnees synthetiques similaires
        dates = pd.date_range("2025-01-01", periods=100, freq="B")
        np.random.seed(42)
        returns = np.random.normal(0.0005, 0.01, size=100)

        futures_prices = 5000 * np.cumprod(1 + returns)
        etf_prices = 500 * np.cumprod(1 + returns + np.random.normal(0, 0.001, 100))

        futures_df = pd.DataFrame({
            "datetime": dates,
            "open": futures_prices * 0.999,
            "high": futures_prices * 1.005,
            "low": futures_prices * 0.995,
            "close": futures_prices,
            "volume": np.random.randint(1000, 10000, 100),
        })

        etf_df = pd.DataFrame({
            "datetime": dates,
            "open": etf_prices * 0.999,
            "high": etf_prices * 1.005,
            "low": etf_prices * 0.995,
            "close": etf_prices,
            "volume": np.random.randint(1000, 10000, 100),
        })

        result = validate_etf_vs_futures(futures_df, etf_df)
        assert result["valid"] is True
        assert result["correlation"] > 0.90

    def test_etf_vs_futures_validation_fail(self):
        """Validation echoue quand les series sont decorrelees."""
        from scripts.download_futures_data import validate_etf_vs_futures

        dates = pd.date_range("2025-01-01", periods=100, freq="B")
        np.random.seed(42)

        # Series completement decorrelees
        futures_df = pd.DataFrame({
            "datetime": dates,
            "open": np.random.uniform(4900, 5100, 100),
            "high": np.random.uniform(5000, 5200, 100),
            "low": np.random.uniform(4800, 5000, 100),
            "close": np.random.uniform(4900, 5100, 100),
            "volume": np.random.randint(1000, 10000, 100),
        })

        etf_df = pd.DataFrame({
            "datetime": dates,
            "open": np.random.uniform(490, 510, 100),
            "high": np.random.uniform(500, 520, 100),
            "low": np.random.uniform(480, 500, 100),
            "close": np.random.uniform(490, 510, 100),
            "volume": np.random.randint(1000, 10000, 100),
        })

        result = validate_etf_vs_futures(futures_df, etf_df)
        assert result["valid"] is False


# =============================================================================
# TEST 27-28 : Edge Cases
# =============================================================================

class TestEdgeCases:
    """Tests edge cases : near expiry, year boundary, etc."""

    def test_year_boundary_front_month(self, contract_mgr):
        """Front month en decembre → contrat Mars de l'annee suivante."""
        ref = date(2026, 12, 19)  # Apres expiry Dec
        front = contract_mgr.get_front_month("MES", ref)

        # Devrait etre Mars 2027
        expiry = date.fromisoformat(front["expiry"])
        assert expiry.year == 2027
        assert front["month_code"] == "H"

    def test_multiple_rolls_same_session(self, roll_manager):
        """Plusieurs symboles peuvent roller en meme temps."""
        ref = date(2026, 3, 17)  # Pres de l'expiry Mars
        results = roll_manager.check_and_execute_rolls(
            symbols=["MES", "MNQ"],
            positions={
                "MES": {"qty": 1, "direction": "BUY"},
                "MNQ": {"qty": 1, "direction": "SELL"},
            },
            ref_date=ref,
        )

        assert len(results) == 2
        assert results[0]["symbol"] in ["MES", "MNQ"]
        assert results[1]["symbol"] in ["MES", "MNQ"]

    def test_zero_capital_raises(self):
        """Capital 0 ou negatif leve ValueError."""
        from core.futures_margin import FuturesMarginTracker

        with pytest.raises(ValueError, match="Capital invalide"):
            FuturesMarginTracker(total_capital=0)

        with pytest.raises(ValueError, match="Capital invalide"):
            FuturesMarginTracker(total_capital=-1000)

    def test_margin_tracker_update_capital(self, margin_tracker):
        """Mise a jour du capital recalcule les limites."""
        assert margin_tracker.max_total_margin == 25_000 * 0.30

        margin_tracker.total_capital = 50_000
        assert margin_tracker.max_total_margin == 50_000 * 0.30

    def test_is_micro_detection(self, contract_mgr):
        """Detection correcte micro vs full-size."""
        assert contract_mgr.is_micro("MES") is True
        assert contract_mgr.is_micro("MNQ") is True
        assert contract_mgr.is_micro("MCL") is True
        assert contract_mgr.is_micro("MGC") is True
        assert contract_mgr.is_micro("ES") is False
        assert contract_mgr.is_micro("NQ") is False

    def test_supported_symbols_complete(self, contract_mgr):
        """Tous les symboles attendus sont supportes."""
        supported = contract_mgr.supported_symbols
        for sym in ["MES", "MNQ", "MCL", "MGC", "ES", "NQ", "CL", "GC"]:
            assert sym in supported

    def test_empty_positions_margin(self, margin_tracker):
        """Marge utilisee = 0 sans positions."""
        assert margin_tracker.calculate_margin_used([]) == 0.0
        assert margin_tracker.calculate_margin_available([]) == margin_tracker.max_total_margin


# =============================================================================
# TEST 29 : Continuous Contract Construction
# =============================================================================

class TestContinuousContract:
    """Tests de construction de contrats continus."""

    def test_no_roll_dates_passthrough(self):
        """Sans dates de roll, les donnees passent telles quelles."""
        from scripts.download_futures_data import build_continuous_contract

        df = pd.DataFrame({
            "datetime": pd.date_range("2025-01-01", periods=10, freq="B"),
            "open": range(100, 110),
            "high": range(101, 111),
            "low": range(99, 109),
            "close": range(100, 110),
            "volume": [1000] * 10,
        })

        result = build_continuous_contract(df)
        assert len(result) == 10
        assert result["close"].iloc[0] == 100

    def test_ratio_adjustment(self):
        """L'ajustement ratio preserve les rendements."""
        from scripts.download_futures_data import build_continuous_contract

        dates = pd.date_range("2025-01-01", periods=10, freq="B")
        closes = [100.0, 102.0, 101.0, 103.0, 105.0,
                  106.0, 104.0, 107.0, 108.0, 110.0]

        df = pd.DataFrame({
            "datetime": dates,
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [1000] * 10,
        })

        roll_dates = [{"date": str(dates[5]), "ratio": 1.02}]
        result = build_continuous_contract(df, roll_dates)

        # Les prix avant le roll doivent etre ajustes par ratio 1.02
        assert abs(result["close"].iloc[0] - 100.0 * 1.02) < 0.01
        # Les prix apres le roll ne changent pas
        assert result["close"].iloc[5] == 106.0


# =============================================================================
# TEST 30 : _authorized_by Guard
# =============================================================================

class TestAuthorizedByGuard:
    """Tests du guard _authorized_by sur les ordres futures."""

    def test_create_position_without_auth_raises(self):
        """Ordre futures sans _authorized_by leve BrokerError."""
        from core.broker.ibkr_futures import IBKRFuturesClient
        from core.broker.base import BrokerError

        mock_broker = MagicMock()
        mock_broker.is_paper = True
        client = IBKRFuturesClient(mock_broker)

        with pytest.raises(BrokerError, match="_authorized_by"):
            client.create_futures_position(
                symbol="MES",
                direction="BUY",
                qty=1,
                _authorized_by=None,
            )

    def test_close_position_without_auth_raises(self):
        """Fermeture sans _authorized_by leve BrokerError."""
        from core.broker.ibkr_futures import IBKRFuturesClient
        from core.broker.base import BrokerError

        mock_broker = MagicMock()
        client = IBKRFuturesClient(mock_broker)

        with pytest.raises(BrokerError, match="_authorized_by"):
            client.close_futures_position("MES", _authorized_by=None)

    def test_live_trading_blocked(self):
        """Trading LIVE bloque pour les futures."""
        from core.broker.ibkr_futures import IBKRFuturesClient
        from core.broker.base import BrokerError

        mock_broker = MagicMock()
        mock_broker.is_paper = False
        client = IBKRFuturesClient(mock_broker)

        with pytest.raises(BrokerError, match="LIVE"):
            client.create_futures_position(
                symbol="MES",
                direction="BUY",
                qty=1,
                _authorized_by="test",
            )

    def test_fractional_qty_blocked(self):
        """Quantite fractionnaire bloquee pour les futures."""
        from core.broker.ibkr_futures import IBKRFuturesClient
        from core.broker.base import BrokerError

        mock_broker = MagicMock()
        mock_broker.is_paper = True
        client = IBKRFuturesClient(mock_broker)

        with pytest.raises(BrokerError, match="entier positif"):
            client.create_futures_position(
                symbol="MES",
                direction="BUY",
                qty=1.5,  # type: ignore
                _authorized_by="test",
            )
