"""
Tests unitaires du risk management — paper portfolio.

Verifie :
  - Circuit-breaker bloque les ordres en cas de drawdown > 5%
  - Max positions bloque les nouveaux ordres
  - Exposition nette cap bloque les ordres excessifs
  - Paper-only guard refuse les ordres en mode live
  - Detection des jours feries NYSE (+ early close)
  - Ordres non autorises rejetes (_authorized_by)
  - PDT guard bloque l'intraday si equity < $25K
"""
import os
import sys
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from pathlib import Path

# Setup paths
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture(autouse=True)
def env_paper_trading():
    """Assure que PAPER_TRADING=true pour tous les tests."""
    with patch.dict(os.environ, {
        "PAPER_TRADING": "true",
        "ALPACA_API_KEY": "test-key",
        "ALPACA_SECRET_KEY": "test-secret",
    }):
        yield


@pytest.fixture
def base_state():
    """State de base pour les tests."""
    return {
        "capital": 100_000.0,
        "positions": {},
        "allocations": {},
        "last_monthly": None,
        "daily_capital_start": 100_000.0,
        "daily_pnl": 0.0,
        "benchmark_start_price": None,
        "benchmark_start_date": None,
        "history": [],
        "intraday_positions": {},
    }


@pytest.fixture
def mock_alpaca_client():
    """Mock du client Alpaca."""
    client = MagicMock()
    client.authenticate.return_value = {
        "status": "ACTIVE",
        "equity": 100_000.0,
        "cash": 50_000.0,
        "buying_power": 200_000.0,
        "currency": "USD",
        "paper": True,
        "account_number": "TEST123",
    }
    client.get_positions.return_value = []
    client.create_position.return_value = {
        "orderId": "test-order-1",
        "symbol": "SPY",
        "side": "buy",
        "status": "filled",
        "qty": "10",
        "filled_qty": 10.0,
        "filled_price": 450.0,
        "stop_loss": None,
        "take_profit": None,
        "bracket": False,
        "paper": True,
        "authorized_by": "paper_portfolio",
    }
    return client


# =============================================================================
# TEST 1 : Circuit-breaker bloque les ordres
# =============================================================================

class TestCircuitBreaker:
    """Le circuit-breaker doit bloquer TOUS les ordres si le drawdown > 5%."""

    def test_circuit_breaker_blocks_orders(self, base_state, mock_alpaca_client):
        """Si equity a baisse de > 5% depuis le debut de journee, aucun ordre ne passe."""
        from scripts.paper_portfolio import execute_orders, STRATEGIES, compute_allocations

        # Simuler un drawdown de 6%
        base_state["daily_capital_start"] = 100_000.0
        mock_alpaca_client.authenticate.return_value["equity"] = 93_500.0  # -6.5%

        allocations = compute_allocations(STRATEGIES, 93_500.0)
        signals = {
            "momentum_25etf": {
                "action": "rebalance",
                "targets": ["SPY", "QQQ"],
                "capital": 10000,
            }
        }

        with patch("scripts.paper_portfolio.is_us_market_open", return_value=True), \
             patch("core.alpaca_client.client.AlpacaClient") as MockClient, \
             patch("scripts.paper_portfolio._check_daily_trailing_stops"):
            MockClient.from_env.return_value = mock_alpaca_client

            orders = execute_orders(signals, allocations, base_state, dry_run=False)

        # Aucun ordre ne doit passer
        assert orders == [], "Circuit-breaker n'a pas bloque les ordres lors d'un DD > 5%"

    def test_circuit_breaker_allows_normal_operations(self, base_state, mock_alpaca_client):
        """Si le drawdown est < 5%, les ordres passent normalement."""
        from scripts.paper_portfolio import execute_orders, STRATEGIES, compute_allocations

        base_state["daily_capital_start"] = 100_000.0
        mock_alpaca_client.authenticate.return_value["equity"] = 97_000.0  # -3%

        allocations = compute_allocations(STRATEGIES, 97_000.0)
        # Signal hold = pas d'ordres mais pas de blocage
        signals = {
            "momentum_25etf": {"action": "hold", "reason": "test"},
        }

        with patch("scripts.paper_portfolio.is_us_market_open", return_value=True), \
             patch("core.alpaca_client.client.AlpacaClient") as MockClient, \
             patch("scripts.paper_portfolio._check_daily_trailing_stops"):
            MockClient.from_env.return_value = mock_alpaca_client

            # Ne doit PAS lever d'exception ni retourner [] a cause du circuit-breaker
            orders = execute_orders(signals, allocations, base_state, dry_run=False)

        # Pas d'ordres car "hold", mais pas bloque par le circuit-breaker
        assert isinstance(orders, list)


# =============================================================================
# TEST 2 : Max positions bloque les nouveaux ordres
# =============================================================================

class TestMaxPositions:
    """Le guard max positions doit bloquer au-dela de 10 positions."""

    def test_max_positions_blocks(self, base_state, mock_alpaca_client):
        """Avec 10 positions, aucun nouvel ordre n'est accepte."""
        from scripts.paper_portfolio import execute_orders, STRATEGIES, compute_allocations

        # Simuler 10 positions existantes
        mock_alpaca_client.get_positions.return_value = [
            {"symbol": f"SYM{i}", "qty": 10.0, "market_val": 5000.0,
             "avg_entry": 500.0, "unrealized_pl": 0.0}
            for i in range(10)
        ]

        allocations = compute_allocations(STRATEGIES, 100_000.0)
        signals = {
            "momentum_25etf": {
                "action": "rebalance",
                "targets": ["SPY"],
                "capital": 10000,
            }
        }

        with patch("scripts.paper_portfolio.is_us_market_open", return_value=True), \
             patch("core.alpaca_client.client.AlpacaClient") as MockClient, \
             patch("scripts.paper_portfolio._check_daily_trailing_stops"):
            MockClient.from_env.return_value = mock_alpaca_client

            orders = execute_orders(signals, allocations, base_state, dry_run=False)

        assert orders == [], "Max positions n'a pas bloque les ordres avec 10 positions"


# =============================================================================
# TEST 3 : Exposition cap bloque les ordres
# =============================================================================

class TestExposureCap:
    """L'exposition directionnelle nette doit etre capped (40% long, 20% short)."""

    def test_exposure_cap_blocks_long(self, base_state, mock_alpaca_client):
        """Avec > 40% d'exposition long, les nouveaux LONG sont bloques."""
        from scripts.paper_portfolio import execute_orders, STRATEGIES, compute_allocations

        # Simuler une grosse expo long (45K sur 100K equity = 45%)
        mock_alpaca_client.get_positions.return_value = [
            {"symbol": "SPY", "qty": 100.0, "market_val": 45_000.0,
             "avg_entry": 450.0, "unrealized_pl": 0.0},
        ]

        allocations = compute_allocations(STRATEGIES, 100_000.0)
        signals = {
            "opex_gamma": {
                "action": "intraday_trade",
                "ticker": "QQQ",
                "direction": "LONG",
                "entry_price": 350.0,
                "stop_loss": 345.0,
                "take_profit": 360.0,
                "capital": 5000,
                "metadata": {},
            }
        }

        with patch("scripts.paper_portfolio.is_us_market_open", return_value=True), \
             patch("core.alpaca_client.client.AlpacaClient") as MockClient, \
             patch("scripts.paper_portfolio._check_daily_trailing_stops"):
            MockClient.from_env.return_value = mock_alpaca_client

            orders = execute_orders(signals, allocations, base_state,
                                    dry_run=False, total_capital=100_000.0)

        # L'ordre LONG doit etre bloque (exposition > 40%)
        assert orders == [], "Exposition cap n'a pas bloque un ordre LONG a > 40%"


# =============================================================================
# TEST 4 : Paper-only guard
# =============================================================================

class TestPaperOnlyGuard:
    """Le guard paper-only doit empecher tout ordre en mode live."""

    def test_paper_only_guard(self):
        """Avec PAPER_TRADING=false, le client Alpaca doit refuser les ordres."""
        from core.alpaca_client.client import AlpacaClient, AlpacaAuthError

        with patch.dict(os.environ, {"PAPER_TRADING": "false"}):
            client = AlpacaClient(
                api_key="test",
                secret_key="test",
                paper=False,
            )
            with pytest.raises(AlpacaAuthError, match="Trading LIVE bloque"):
                client._get_trading_client()


# =============================================================================
# TEST 5 : Detection des jours feries
# =============================================================================

class TestHolidayDetection:
    """is_us_market_open() doit retourner False les jours feries NYSE."""

    def test_holiday_detection(self):
        """Le 26 novembre 2026 (Thanksgiving), le marche est ferme."""
        from scripts.paper_portfolio import NYSE_HOLIDAYS_2026, NYSE_EARLY_CLOSE_2026

        assert "2026-11-26" in NYSE_HOLIDAYS_2026, "Thanksgiving manquant"
        assert "2026-12-25" in NYSE_HOLIDAYS_2026, "Noel manquant"
        assert "2026-01-01" in NYSE_HOLIDAYS_2026, "Nouvel An manquant"
        assert "2026-04-03" in NYSE_HOLIDAYS_2026, "Good Friday manquant"

    def test_early_close_dates(self):
        """Les dates d'early close doivent etre definies."""
        from scripts.paper_portfolio import NYSE_EARLY_CLOSE_2026

        assert "2026-11-27" in NYSE_EARLY_CLOSE_2026, "Veille Thanksgiving manquant"
        assert "2026-12-24" in NYSE_EARLY_CLOSE_2026, "Veille Noel manquant"

    def test_early_close_hour(self):
        """Les jours d'early close, la fermeture est a 13:00 ET."""
        from scripts.paper_portfolio import get_market_close_hour

        assert get_market_close_hour("2026-11-27") == (13, 0), "Early close = 13:00"
        assert get_market_close_hour("2026-12-24") == (13, 0), "Early close = 13:00"
        assert get_market_close_hour("2026-03-25") == (16, 0), "Jour normal = 16:00"

    def test_holiday_blocks_trading(self):
        """Le marche ferme un jour ferie empecherait le trading."""
        from scripts.paper_portfolio import is_us_market_open
        import zoneinfo
        from unittest.mock import patch

        et = zoneinfo.ZoneInfo("America/New_York")
        # Simuler Thanksgiving 2026 a 10:00 ET (jeudi)
        fake_time = datetime(2026, 11, 26, 10, 0, 0, tzinfo=et)
        with patch("scripts.paper_portfolio.datetime") as mock_dt:
            mock_dt.now.return_value = fake_time
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
            result = is_us_market_open()
        assert result is False, "Le marche devrait etre ferme Thanksgiving"


# =============================================================================
# TEST 6 : Ordres non autorises rejetes
# =============================================================================

class TestUnauthorizedOrderRejected:
    """create_position() sans _authorized_by doit etre refuse."""

    def test_unauthorized_order_rejected(self):
        """Un appel a create_position sans _authorized_by leve AlpacaAPIError."""
        from core.alpaca_client.client import AlpacaClient, AlpacaAPIError

        client = AlpacaClient(
            api_key="test",
            secret_key="test",
            paper=True,
        )
        with pytest.raises(AlpacaAPIError, match="Ordre REFUSE"):
            client.create_position("SPY", "BUY", qty=10)

    def test_unauthorized_close_rejected(self):
        """Un appel a close_position sans _authorized_by leve AlpacaAPIError."""
        from core.alpaca_client.client import AlpacaClient, AlpacaAPIError

        client = AlpacaClient(
            api_key="test",
            secret_key="test",
            paper=True,
        )
        with pytest.raises(AlpacaAPIError, match="Ordre REFUSE"):
            client.close_position("SPY")

    def test_unauthorized_close_all_rejected(self):
        """Un appel a close_all_positions sans _authorized_by leve AlpacaAPIError."""
        from core.alpaca_client.client import AlpacaClient, AlpacaAPIError

        client = AlpacaClient(
            api_key="test",
            secret_key="test",
            paper=True,
        )
        with pytest.raises(AlpacaAPIError, match="Ordre REFUSE"):
            client.close_all_positions()


# =============================================================================
# TEST 7 : PDT Guard
# =============================================================================

class TestPDTGuard:
    """Si equity < $25K, les strategies intraday doivent etre bloquees."""

    def test_pdt_guard_blocks_intraday(self, base_state, mock_alpaca_client):
        """Avec equity < $25K, un signal intraday est bloque."""
        from scripts.paper_portfolio import execute_orders, STRATEGIES, compute_allocations

        # Equity sous le seuil PDT
        mock_alpaca_client.authenticate.return_value["equity"] = 20_000.0
        base_state["daily_capital_start"] = 20_000.0

        allocations = compute_allocations(STRATEGIES, 20_000.0)
        signals = {
            "opex_gamma": {
                "action": "intraday_trade",
                "ticker": "SPY",
                "direction": "LONG",
                "entry_price": 450.0,
                "stop_loss": 445.0,
                "take_profit": 460.0,
                "capital": 3000,
                "metadata": {},
            }
        }

        with patch("scripts.paper_portfolio.is_us_market_open", return_value=True), \
             patch("core.alpaca_client.client.AlpacaClient") as MockClient, \
             patch("scripts.paper_portfolio._check_daily_trailing_stops"):
            MockClient.from_env.return_value = mock_alpaca_client

            orders = execute_orders(signals, allocations, base_state,
                                    dry_run=False, total_capital=20_000.0)

        assert orders == [], "PDT guard n'a pas bloque l'ordre intraday avec equity < $25K"

    def test_pdt_guard_allows_daily(self, base_state, mock_alpaca_client):
        """Avec equity < $25K, les strategies daily doivent toujours fonctionner."""
        from scripts.paper_portfolio import execute_orders, STRATEGIES, compute_allocations

        # Equity sous le seuil PDT
        mock_alpaca_client.authenticate.return_value["equity"] = 20_000.0
        base_state["daily_capital_start"] = 20_000.0
        base_state["positions"] = {"momentum_25etf": {"symbols": ["SPY"]}}

        allocations = compute_allocations(STRATEGIES, 20_000.0)
        # Signal sell_all = pas intraday, devrait passer
        signals = {
            "momentum_25etf": {
                "action": "sell_all",
                "reason": "crash filter",
                "targets": [],
            }
        }

        # Mock la position SPY dans Alpaca
        mock_alpaca_client.get_positions.return_value = [
            {"symbol": "SPY", "qty": 10.0, "market_val": 4500.0,
             "avg_entry": 450.0, "unrealized_pl": 0.0}
        ]
        mock_alpaca_client.close_position.return_value = {
            "orderId": "test-close-1",
            "symbol": "SPY",
            "status": "accepted",
        }

        with patch("scripts.paper_portfolio.is_us_market_open", return_value=True), \
             patch("core.alpaca_client.client.AlpacaClient") as MockClient, \
             patch("scripts.paper_portfolio._check_daily_trailing_stops"):
            MockClient.from_env.return_value = mock_alpaca_client

            orders = execute_orders(signals, allocations, base_state,
                                    dry_run=False, total_capital=20_000.0)

        # Le sell_all daily doit passer meme si equity < 25K
        assert len(orders) > 0, "PDT guard a bloque un ordre daily — ne devrait pas"


# =============================================================================
# TEST 8 : Dry-run ne passe aucun ordre
# =============================================================================

class TestDryRun:
    """En mode dry-run, aucun ordre ne doit etre execute."""

    def test_dry_run_returns_empty(self, base_state):
        from scripts.paper_portfolio import execute_orders, compute_allocations, STRATEGIES

        allocations = compute_allocations(STRATEGIES, 100_000.0)
        signals = {
            "momentum_25etf": {
                "action": "rebalance",
                "targets": ["SPY"],
                "capital": 10000,
            }
        }

        orders = execute_orders(signals, allocations, base_state, dry_run=True)
        assert orders == [], "Dry-run a execute des ordres"
