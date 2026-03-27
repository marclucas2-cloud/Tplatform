"""
Tests unitaires du pipeline EU multi-strategies — paper_portfolio_eu.py (INFRA-005).

Verifie :
  - Chargement strategies depuis YAML
  - Validation du registre (champs obligatoires)
  - Allocations EU (Sharpe-weighted, caps)
  - Fenetres horaires par strategie
  - BCE day detection (bce_momentum_drift)
  - Auto sector event filter
  - Brent lag market hours
  - Cross-broker routing (eu_close_us_afternoon)
  - EU Gap Open signal generation
  - Circuit-breaker EU
  - Kill switch EU
  - Fermeture forcee positions EU
"""
import json
import os
import sys
import tempfile
from datetime import datetime, date, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

import pytest
import yaml

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
        "IBKR_HOST": "127.0.0.1",
        "IBKR_PORT": "7497",
        "IBKR_PAPER": "true",
        "ALPACA_API_KEY": "test-key",
        "ALPACA_SECRET_KEY": "test-secret",
    }):
        yield


@pytest.fixture
def sample_strategies_yaml(tmp_path):
    """Cree un fichier YAML temporaire avec les strategies de test."""
    data = {
        "strategies": {
            "bce_momentum_drift": {
                "name": "BCE Momentum Drift v2",
                "enabled": True,
                "sharpe": 14.93,
                "trades": 99,
                "wf_status": "VALIDATED",
                "edge_type": "event",
                "market_hours": {"start": "13:45", "end": "17:30", "tz": "Europe/Paris"},
                "tickers": ["BNP.PA", "GLE.PA", "DBK.DE", "ING.AS"],
                "allocation_pct": 0.06,
                "max_position_pct": 0.03,
                "sl_pct": 0.015,
                "tp_pct": 0.03,
            },
            "auto_sector_german": {
                "name": "Auto Sector German",
                "enabled": True,
                "sharpe": 13.43,
                "trades": 97,
                "wf_status": "VALIDATED",
                "edge_type": "event",
                "market_hours": {"start": "09:00", "end": "17:30", "tz": "Europe/Paris"},
                "tickers": ["CON.DE", "SHA.DE", "ZIL.DE"],
                "allocation_pct": 0.05,
                "max_position_pct": 0.03,
                "sl_pct": 0.02,
                "tp_pct": 0.035,
            },
            "brent_lag_play": {
                "name": "Brent Lag Play",
                "enabled": True,
                "sharpe": 4.08,
                "trades": 729,
                "wf_status": "VALIDATED",
                "edge_type": "momentum",
                "market_hours": {"start": "15:30", "end": "20:00", "tz": "Europe/Paris"},
                "tickers": ["BP.L", "SHEL.L", "TTE.PA"],
                "allocation_pct": 0.06,
                "max_position_pct": 0.03,
                "sl_pct": 0.015,
                "tp_pct": 0.025,
            },
            "eu_close_us_afternoon": {
                "name": "EU Close -> US Afternoon",
                "enabled": True,
                "sharpe": 2.43,
                "trades": 113,
                "wf_status": "VALIDATED",
                "edge_type": "cross_timezone",
                "market_hours": {"start": "15:30", "end": "21:00", "tz": "Europe/Paris"},
                "tickers": ["SPY", "QQQ", "IWM"],
                "execution_broker": "alpaca",
                "allocation_pct": 0.05,
                "max_position_pct": 0.03,
                "sl_pct": 0.01,
                "tp_pct": 0.02,
            },
            "eu_gap_open": {
                "name": "EU Gap Open",
                "enabled": True,
                "sharpe": 8.56,
                "trades": 150,
                "wf_status": "VALIDATED",
                "edge_type": "gap",
                "market_hours": {"start": "09:05", "end": "12:00", "tz": "Europe/Paris"},
                "tickers": ["MC.PA", "SAP.DE", "ASML.AS", "TTE.PA", "SIE.DE", "ALV.DE", "BNP.PA", "BMW.DE"],
                "allocation_pct": 0.06,
                "max_position_pct": 0.03,
                "sl_pct": 0.01,
                "tp_pct": 0.02,
            },
        }
    }
    yaml_path = tmp_path / "strategies_eu.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(data, f)
    return yaml_path


@pytest.fixture
def base_state():
    """State de base pour les tests EU."""
    return {
        "capital": 100_000.0,
        "positions": {},
        "allocations": {},
        "daily_capital_start": 100_000.0,
        "daily_pnl": 0.0,
        "last_run_date": None,
        "history": [],
        "intraday_positions": {},
        "strategy_pnl_log": {},
    }


@pytest.fixture
def mock_ibkr_broker():
    """Mock du broker IBKR."""
    broker = MagicMock()
    type(broker).name = PropertyMock(return_value="ibkr")
    type(broker).is_paper = PropertyMock(return_value=True)
    broker.authenticate.return_value = {
        "status": "ACTIVE",
        "equity": 100_000.0,
        "cash": 60_000.0,
        "buying_power": 200_000.0,
        "currency": "EUR",
        "paper": True,
        "account_number": "DU12345",
    }
    broker.get_positions.return_value = []
    broker.create_position.return_value = {
        "orderId": "ibkr-order-1",
        "symbol": "BNP.PA",
        "side": "buy",
        "status": "filled",
        "qty": 10,
        "filled_qty": 10,
        "filled_price": 55.0,
        "stop_loss": 54.0,
        "take_profit": 57.0,
        "bracket": True,
        "paper": True,
        "authorized_by": "paper_portfolio_eu",
    }
    broker.close_position.return_value = {
        "orderId": "ibkr-close-1",
        "symbol": "BNP.PA",
        "status": "filled",
    }
    # Default get_prices returns 5 bars
    broker.get_prices.return_value = {
        "bars": [
            {"t": "2026-03-20", "o": 50.0, "h": 51.0, "l": 49.5, "c": 50.5, "v": 100000},
            {"t": "2026-03-21", "o": 50.5, "h": 52.0, "l": 50.0, "c": 51.5, "v": 110000},
            {"t": "2026-03-22", "o": 51.5, "h": 53.0, "l": 51.0, "c": 52.0, "v": 120000},
            {"t": "2026-03-23", "o": 52.0, "h": 53.5, "l": 51.5, "c": 53.0, "v": 130000},
            {"t": "2026-03-24", "o": 53.0, "h": 54.0, "l": 52.5, "c": 53.5, "v": 140000},
        ],
        "symbol": "BNP.PA",
        "timeframe": "1D",
    }
    return broker


# =============================================================================
# TEST 1 : Chargement YAML
# =============================================================================

class TestLoadStrategiesFromYaml:

    def test_load_strategies_from_yaml(self, sample_strategies_yaml):
        """Charge les strategies depuis le fichier YAML et verifie la structure."""
        from scripts.paper_portfolio_eu import load_strategies_from_yaml

        strategies = load_strategies_from_yaml(sample_strategies_yaml)

        assert isinstance(strategies, dict)
        assert len(strategies) == 5
        assert "bce_momentum_drift" in strategies
        assert "auto_sector_german" in strategies
        assert "brent_lag_play" in strategies
        assert "eu_close_us_afternoon" in strategies
        assert "eu_gap_open" in strategies

        # Verifier un champ specifique
        bce = strategies["bce_momentum_drift"]
        assert bce["name"] == "BCE Momentum Drift v2"
        assert bce["enabled"] is True
        assert bce["sharpe"] == 14.93
        assert bce["wf_status"] == "VALIDATED"

    def test_load_yaml_missing_field_raises(self, tmp_path):
        """Verifie qu'un champ manquant leve une ValueError."""
        from scripts.paper_portfolio_eu import load_strategies_from_yaml

        bad_data = {
            "strategies": {
                "broken": {
                    "name": "Broken Strategy",
                    "enabled": True,
                    # Missing: sharpe, trades, wf_status, edge_type, etc.
                }
            }
        }
        yaml_path = tmp_path / "bad.yaml"
        with open(yaml_path, "w") as f:
            yaml.dump(bad_data, f)

        with pytest.raises(ValueError, match="champs manquants"):
            load_strategies_from_yaml(yaml_path)

    def test_load_yaml_missing_market_hours_tz(self, tmp_path):
        """Verifie qu'un market_hours sans tz leve une ValueError."""
        from scripts.paper_portfolio_eu import load_strategies_from_yaml

        bad_data = {
            "strategies": {
                "broken": {
                    "name": "Broken",
                    "enabled": True,
                    "sharpe": 1.0,
                    "trades": 10,
                    "wf_status": "VALIDATED",
                    "edge_type": "gap",
                    "market_hours": {"start": "09:00", "end": "17:30"},  # Missing tz
                    "tickers": ["X"],
                    "allocation_pct": 0.05,
                    "max_position_pct": 0.03,
                    "sl_pct": 0.01,
                    "tp_pct": 0.02,
                }
            }
        }
        yaml_path = tmp_path / "bad_tz.yaml"
        with open(yaml_path, "w") as f:
            yaml.dump(bad_data, f)

        with pytest.raises(ValueError, match="market_hours manque 'tz'"):
            load_strategies_from_yaml(yaml_path)


# =============================================================================
# TEST 2 : Registre — champs obligatoires
# =============================================================================

class TestStrategyRegistryFields:

    def test_strategy_registry_all_required_fields(self, sample_strategies_yaml):
        """Toutes les strategies du YAML ont les champs obligatoires."""
        from scripts.paper_portfolio_eu import load_strategies_from_yaml

        strategies = load_strategies_from_yaml(sample_strategies_yaml)
        required_fields = {
            "name", "enabled", "sharpe", "trades", "wf_status", "edge_type",
            "market_hours", "tickers", "allocation_pct", "max_position_pct",
            "sl_pct", "tp_pct",
        }

        for sid, cfg in strategies.items():
            for field in required_fields:
                assert field in cfg, f"Strategie '{sid}' manque le champ '{field}'"

            # market_hours doit avoir start, end, tz
            mh = cfg["market_hours"]
            assert "start" in mh, f"Strategie '{sid}' market_hours manque 'start'"
            assert "end" in mh, f"Strategie '{sid}' market_hours manque 'end'"
            assert "tz" in mh, f"Strategie '{sid}' market_hours manque 'tz'"

            # Types corrects
            assert isinstance(cfg["enabled"], bool)
            assert isinstance(cfg["sharpe"], (int, float))
            assert isinstance(cfg["trades"], int)
            assert isinstance(cfg["tickers"], list)
            assert cfg["allocation_pct"] > 0
            assert cfg["max_position_pct"] > 0
            assert cfg["sl_pct"] > 0
            assert cfg["tp_pct"] > 0

    def test_production_yaml_loads(self):
        """Le vrai fichier config/strategies_eu.yaml se charge sans erreur."""
        from scripts.paper_portfolio_eu import load_strategies_from_yaml

        yaml_path = ROOT / "config" / "strategies_eu.yaml"
        if not yaml_path.exists():
            pytest.skip("config/strategies_eu.yaml absent")

        strategies = load_strategies_from_yaml(yaml_path)
        assert len(strategies) >= 5
        for sid in ["bce_momentum_drift", "auto_sector_german", "brent_lag_play",
                     "eu_close_us_afternoon", "eu_gap_open"]:
            assert sid in strategies, f"Strategie '{sid}' absente du YAML de production"


# =============================================================================
# TEST 3 : Allocations EU
# =============================================================================

class TestComputeEuAllocations:

    def test_compute_eu_allocations(self, sample_strategies_yaml):
        """Allocations correctes pour 100K de capital."""
        from scripts.paper_portfolio_eu import load_strategies_from_yaml, compute_eu_allocations

        strategies = load_strategies_from_yaml(sample_strategies_yaml)
        allocs = compute_eu_allocations(strategies, 100_000.0)

        # Toutes les strategies enabled recoivent une allocation
        assert len(allocs) == 5

        # Chaque allocation a les bons champs
        for sid, alloc in allocs.items():
            assert "pct" in alloc
            assert "capital" in alloc
            assert "max_position" in alloc
            assert alloc["pct"] > 0
            assert alloc["capital"] > 0
            assert alloc["max_position"] > 0
            assert alloc["max_position"] <= 10_000  # 10% de 100K

        # Total des pourcentages = ~100%
        total_pct = sum(a["pct"] for a in allocs.values())
        assert abs(total_pct - 1.0) < 0.01

        # Total capital ~ 100K
        total_capital = sum(a["capital"] for a in allocs.values())
        assert abs(total_capital - 100_000) < 10  # tolerance arrondi

    def test_allocations_empty_if_all_disabled(self, tmp_path):
        """Aucune allocation si toutes les strategies sont disabled."""
        from scripts.paper_portfolio_eu import load_strategies_from_yaml, compute_eu_allocations

        data = {
            "strategies": {
                "test": {
                    "name": "Test",
                    "enabled": False,
                    "sharpe": 1.0,
                    "trades": 10,
                    "wf_status": "VALIDATED",
                    "edge_type": "gap",
                    "market_hours": {"start": "09:00", "end": "17:30", "tz": "Europe/Paris"},
                    "tickers": ["X"],
                    "allocation_pct": 0.05,
                    "max_position_pct": 0.03,
                    "sl_pct": 0.01,
                    "tp_pct": 0.02,
                }
            }
        }
        yaml_path = tmp_path / "disabled.yaml"
        with open(yaml_path, "w") as f:
            yaml.dump(data, f)

        strategies = load_strategies_from_yaml(yaml_path)
        allocs = compute_eu_allocations(strategies, 100_000.0)
        assert allocs == {}

    def test_max_position_respects_cap(self, sample_strategies_yaml):
        """max_position ne depasse jamais 10% du capital total."""
        from scripts.paper_portfolio_eu import load_strategies_from_yaml, compute_eu_allocations

        strategies = load_strategies_from_yaml(sample_strategies_yaml)
        capital = 100_000.0
        allocs = compute_eu_allocations(strategies, capital)

        for sid, alloc in allocs.items():
            assert alloc["max_position"] <= capital * 0.10 + 1  # +1 tolerance arrondi


# =============================================================================
# TEST 4 : Fenetres horaires par strategie
# =============================================================================

class TestMarketHoursPerStrategy:

    def test_market_hours_check_per_strategy(self, sample_strategies_yaml):
        """Chaque strategie a sa propre fenetre horaire."""
        from scripts.paper_portfolio_eu import load_strategies_from_yaml, is_strategy_active

        strategies = load_strategies_from_yaml(sample_strategies_yaml)

        # Mocker un mardi a 10:00 CET
        mock_now = datetime(2026, 3, 24, 10, 0, 0)  # mardi

        with patch("scripts.paper_portfolio_eu.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            # eu_gap_open (9:05-12:00) => actif
            assert is_strategy_active("eu_gap_open", strategies["eu_gap_open"]) is True

            # bce_momentum_drift (13:45-17:30) => inactif a 10:00
            assert is_strategy_active("bce_momentum_drift", strategies["bce_momentum_drift"]) is False

            # brent_lag_play (15:30-20:00) => inactif a 10:00
            assert is_strategy_active("brent_lag_play", strategies["brent_lag_play"]) is False

    def test_market_hours_weekend_blocked(self, sample_strategies_yaml):
        """Aucune strategie ne s'active le weekend."""
        from scripts.paper_portfolio_eu import load_strategies_from_yaml, is_strategy_active

        strategies = load_strategies_from_yaml(sample_strategies_yaml)

        # Samedi 14:00
        mock_now = datetime(2026, 3, 28, 14, 0, 0)  # samedi

        with patch("scripts.paper_portfolio_eu.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            for sid, cfg in strategies.items():
                assert is_strategy_active(sid, cfg) is False, \
                    f"Strategie '{sid}' ne devrait pas etre active le samedi"

    def test_market_hours_holiday_blocked(self, sample_strategies_yaml):
        """Aucune strategie ne s'active un jour ferie EU."""
        from scripts.paper_portfolio_eu import load_strategies_from_yaml, is_strategy_active

        strategies = load_strategies_from_yaml(sample_strategies_yaml)

        # 1er mai 2026 = vendredi ferie
        mock_now = datetime(2026, 5, 1, 14, 0, 0)

        with patch("scripts.paper_portfolio_eu.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            for sid, cfg in strategies.items():
                assert is_strategy_active(sid, cfg) is False, \
                    f"Strategie '{sid}' ne devrait pas etre active le 1er mai"


# =============================================================================
# TEST 5 : BCE day detection
# =============================================================================

class TestBceMomentumDrift:

    def test_bce_only_active_on_bce_days(self, mock_ibkr_broker, base_state):
        """bce_momentum_drift ne genere des signaux que les jours BCE."""
        from scripts.paper_portfolio_eu import signal_bce_momentum_drift

        config = {
            "tickers": ["BNP.PA", "GLE.PA"],
            "sl_pct": 0.015,
            "tp_pct": 0.03,
        }

        # Mock EventCalendar pour retourner False (pas de BCE)
        mock_cal = MagicMock()
        mock_cal.is_bce_day.return_value = False

        with patch("core.event_calendar.EventCalendar", return_value=mock_cal):
            signals = signal_bce_momentum_drift(
                mock_ibkr_broker, config, 5000.0, base_state,
            )

        assert signals == []
        mock_cal.is_bce_day.assert_called_once()

    def test_bce_generates_signals_on_bce_day(self, mock_ibkr_broker, base_state):
        """bce_momentum_drift genere des signaux quand c'est un jour BCE."""
        from scripts.paper_portfolio_eu import signal_bce_momentum_drift

        config = {
            "tickers": ["BNP.PA"],
            "sl_pct": 0.015,
            "tp_pct": 0.03,
        }

        # Bars avec momentum positif (close[-1] > close[-6])
        mock_ibkr_broker.get_prices.return_value = {
            "bars": [
                {"t": "d1", "o": 50, "h": 51, "l": 49, "c": 50, "v": 100000},
                {"t": "d2", "o": 50, "h": 51, "l": 49, "c": 50.5, "v": 100000},
                {"t": "d3", "o": 50, "h": 52, "l": 50, "c": 51, "v": 100000},
                {"t": "d4", "o": 51, "h": 52, "l": 50, "c": 51.5, "v": 100000},
                {"t": "d5", "o": 51, "h": 53, "l": 51, "c": 52, "v": 100000},
                {"t": "d6", "o": 52, "h": 54, "l": 52, "c": 53, "v": 100000},
                {"t": "d7", "o": 53, "h": 55, "l": 53, "c": 54, "v": 100000},
                {"t": "d8", "o": 54, "h": 56, "l": 54, "c": 55, "v": 100000},
                {"t": "d9", "o": 55, "h": 57, "l": 55, "c": 56, "v": 100000},
                {"t": "d10", "o": 56, "h": 58, "l": 56, "c": 57, "v": 100000},
            ],
        }

        mock_cal = MagicMock()
        mock_cal.is_bce_day.return_value = True

        with patch("core.event_calendar.EventCalendar", return_value=mock_cal):
            signals = signal_bce_momentum_drift(
                mock_ibkr_broker, config, 5000.0, base_state,
            )

        assert len(signals) >= 1
        sig = signals[0]
        assert sig["strategy"] == "bce_momentum_drift"
        assert sig["ticker"] == "BNP.PA"
        assert sig["direction"] in ("BUY", "SELL")
        assert sig["qty"] > 0
        assert "stop_loss" in sig
        assert "take_profit" in sig


# =============================================================================
# TEST 6 : Auto sector event filter
# =============================================================================

class TestAutoSectorGerman:

    def test_auto_sector_event_filter(self, mock_ibkr_broker, base_state):
        """auto_sector_german filtre les tickers sans gap significatif."""
        from scripts.paper_portfolio_eu import signal_auto_sector_german

        config = {
            "tickers": ["CON.DE", "SHA.DE"],
            "sl_pct": 0.02,
            "tp_pct": 0.035,
        }

        # Bars avec gap minuscule (< 0.3%)
        mock_ibkr_broker.get_prices.return_value = {
            "bars": [
                *[{"t": f"d{i}", "o": 50, "h": 51, "l": 49, "c": 50, "v": 80000}
                  for i in range(23)],
                {"t": "d24", "o": 50.0, "h": 51, "l": 49.5, "c": 50.1, "v": 100000},
                {"t": "d25", "o": 50.12, "h": 51, "l": 49.5, "c": 50.2, "v": 200000},
            ],
        }

        signals = signal_auto_sector_german(
            mock_ibkr_broker, config, 5000.0, base_state,
        )

        # Gap trop petit => pas de signal
        assert signals == []

    def test_auto_sector_generates_on_big_gap(self, mock_ibkr_broker, base_state):
        """auto_sector_german genere un signal avec un gap > 0.3% et volume eleve."""
        from scripts.paper_portfolio_eu import signal_auto_sector_german

        config = {
            "tickers": ["CON.DE"],
            "sl_pct": 0.02,
            "tp_pct": 0.035,
        }

        # Bars avec gap de ~2% et volume eleve
        bars = [{"t": f"d{i}", "o": 50, "h": 51, "l": 49, "c": 50, "v": 100000}
                for i in range(23)]
        bars.append({"t": "d24", "o": 50, "h": 51, "l": 49, "c": 50.0, "v": 100000})
        bars.append({"t": "d25", "o": 51.0, "h": 52, "l": 50.5, "c": 51.5, "v": 300000})

        mock_ibkr_broker.get_prices.return_value = {"bars": bars}

        signals = signal_auto_sector_german(
            mock_ibkr_broker, config, 5000.0, base_state,
        )

        assert len(signals) == 1
        assert signals[0]["strategy"] == "auto_sector_german"
        assert signals[0]["direction"] == "BUY"  # gap positif


# =============================================================================
# TEST 7 : Brent Lag market hours
# =============================================================================

class TestBrentLagPlay:

    def test_brent_lag_market_hours(self, sample_strategies_yaml):
        """brent_lag_play n'est actif que 15:30-20:00 CET."""
        from scripts.paper_portfolio_eu import load_strategies_from_yaml, is_strategy_active

        strategies = load_strategies_from_yaml(sample_strategies_yaml)
        cfg = strategies["brent_lag_play"]

        # 10:00 CET => inactif
        mock_10 = datetime(2026, 3, 24, 10, 0, 0)
        with patch("scripts.paper_portfolio_eu.datetime") as mock_dt:
            mock_dt.now.return_value = mock_10
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert is_strategy_active("brent_lag_play", cfg) is False

        # 16:00 CET => actif
        mock_16 = datetime(2026, 3, 24, 16, 0, 0)
        with patch("scripts.paper_portfolio_eu.datetime") as mock_dt:
            mock_dt.now.return_value = mock_16
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert is_strategy_active("brent_lag_play", cfg) is True

        # 20:30 CET => inactif
        mock_2030 = datetime(2026, 3, 24, 20, 30, 0)
        with patch("scripts.paper_portfolio_eu.datetime") as mock_dt:
            mock_dt.now.return_value = mock_2030
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert is_strategy_active("brent_lag_play", cfg) is False

    def test_brent_lag_no_signal_if_oil_flat(self, mock_ibkr_broker, base_state):
        """brent_lag_play ne genere rien si le mouvement oil est < 1%."""
        from scripts.paper_portfolio_eu import signal_brent_lag_play

        config = {
            "tickers": ["BP.L", "SHEL.L", "TTE.PA"],
            "sl_pct": 0.015,
            "tp_pct": 0.025,
        }

        # TTE.PA proxy oil : mouvement plat
        mock_ibkr_broker.get_prices.return_value = {
            "bars": [
                {"t": "d1", "o": 50, "h": 51, "l": 49, "c": 50, "v": 100000},
                {"t": "d2", "o": 50, "h": 51, "l": 49, "c": 50, "v": 100000},
                {"t": "d3", "o": 50, "h": 51, "l": 49, "c": 50, "v": 100000},
                {"t": "d4", "o": 50, "h": 51, "l": 49, "c": 50.2, "v": 100000},
                {"t": "d5", "o": 50.2, "h": 51, "l": 49.5, "c": 50.3, "v": 100000},
            ],
        }

        signals = signal_brent_lag_play(
            mock_ibkr_broker, config, 6000.0, base_state,
        )

        assert signals == []


# =============================================================================
# TEST 8 : Cross-broker routing
# =============================================================================

class TestEuCloseUsCrossBroker:

    def test_eu_close_cross_broker_routing(self, mock_ibkr_broker, base_state):
        """eu_close_us_afternoon route vers Alpaca (execution_broker=alpaca)."""
        from scripts.paper_portfolio_eu import signal_eu_close_us_afternoon

        config = {
            "tickers": ["SPY", "QQQ", "IWM"],
            "sl_pct": 0.01,
            "tp_pct": 0.02,
        }

        # EU momentum > 0.5% => signal BUY
        mock_ibkr_broker.get_prices.return_value = {
            "bars": [
                {"t": "d1", "o": 200.0, "h": 205, "l": 199, "c": 204.0, "v": 500000},
            ],
        }

        signals = signal_eu_close_us_afternoon(
            mock_ibkr_broker, config, 5000.0, base_state,
        )

        assert len(signals) == 3  # SPY, QQQ, IWM
        for sig in signals:
            assert sig["execution_broker"] == "alpaca"
            assert sig["strategy"] == "eu_close_us_afternoon"
            assert sig["direction"] == "BUY"  # EU momentum positif
            assert sig["ticker"] in ("SPY", "QQQ", "IWM")

    def test_eu_close_no_signal_if_eu_flat(self, mock_ibkr_broker, base_state):
        """Pas de signal cross-broker si momentum EU < 0.5%."""
        from scripts.paper_portfolio_eu import signal_eu_close_us_afternoon

        config = {
            "tickers": ["SPY"],
            "sl_pct": 0.01,
            "tp_pct": 0.02,
        }

        # Momentum quasi nul
        mock_ibkr_broker.get_prices.return_value = {
            "bars": [
                {"t": "d1", "o": 200.0, "h": 201, "l": 199.5, "c": 200.2, "v": 500000},
            ],
        }

        signals = signal_eu_close_us_afternoon(
            mock_ibkr_broker, config, 5000.0, base_state,
        )

        assert signals == []


# =============================================================================
# TEST 9 : EU Gap Open signal generation
# =============================================================================

class TestEuGapOpen:

    def test_eu_gap_open_signal_generation(self, mock_ibkr_broker, base_state):
        """EU Gap Open genere des signaux sur gap > 0.5%."""
        from scripts.paper_portfolio_eu import signal_eu_gap_open

        config = {
            "tickers": ["MC.PA", "SAP.DE"],
            "sl_pct": 0.01,
            "tp_pct": 0.02,
        }

        # Gap de ~2% positif
        mock_ibkr_broker.get_prices.return_value = {
            "bars": [
                {"t": "d1", "o": 100, "h": 102, "l": 99, "c": 100, "v": 50000},
                {"t": "d2", "o": 100, "h": 103, "l": 99, "c": 101, "v": 55000},
                {"t": "d3", "o": 101, "h": 104, "l": 100, "c": 102, "v": 60000},
                {"t": "d4", "o": 102, "h": 105, "l": 101, "c": 103, "v": 65000},
                {"t": "d5", "o": 105.1, "h": 107, "l": 104, "c": 106, "v": 70000},
            ],
        }

        signals = signal_eu_gap_open(
            mock_ibkr_broker, config, 6000.0, base_state,
        )

        assert len(signals) == 2  # MC.PA et SAP.DE
        for sig in signals:
            assert sig["strategy"] == "eu_gap_open"
            assert sig["direction"] == "BUY"  # gap positif
            assert sig["qty"] > 0
            assert sig["gap_pct"] > 0.5  # gap > 0.5%
            assert "stop_loss" in sig
            assert "take_profit" in sig

    def test_eu_gap_open_no_signal_small_gap(self, mock_ibkr_broker, base_state):
        """Pas de signal si gap < 0.5%."""
        from scripts.paper_portfolio_eu import signal_eu_gap_open

        config = {
            "tickers": ["MC.PA"],
            "sl_pct": 0.01,
            "tp_pct": 0.02,
        }

        # Gap de 0.1% (trop petit)
        mock_ibkr_broker.get_prices.return_value = {
            "bars": [
                {"t": "d1", "o": 100, "h": 102, "l": 99, "c": 100, "v": 50000},
                {"t": "d2", "o": 100.1, "h": 101, "l": 99.5, "c": 100.5, "v": 50000},
            ],
        }

        signals = signal_eu_gap_open(
            mock_ibkr_broker, config, 6000.0, base_state,
        )

        assert signals == []


# =============================================================================
# TEST 10 : Circuit-breaker EU
# =============================================================================

class TestCircuitBreakerEu:

    def test_circuit_breaker_eu(self, base_state):
        """Circuit-breaker declenche si drawdown > 5%."""
        from scripts.paper_portfolio_eu import check_circuit_breaker_eu

        base_state["daily_capital_start"] = 100_000.0

        # Drawdown de 6% => circuit-breaker
        assert check_circuit_breaker_eu(base_state, 94_000.0) is True

        # Drawdown de 3% => pas de circuit-breaker
        assert check_circuit_breaker_eu(base_state, 97_000.0) is False

        # Drawdown de exactement 5% => circuit-breaker
        assert check_circuit_breaker_eu(base_state, 95_000.0) is False
        assert check_circuit_breaker_eu(base_state, 94_999.0) is True

    def test_circuit_breaker_no_start(self, base_state):
        """Circuit-breaker ne declenche pas si daily_capital_start = 0."""
        from scripts.paper_portfolio_eu import check_circuit_breaker_eu

        base_state["daily_capital_start"] = 0
        assert check_circuit_breaker_eu(base_state, 50_000.0) is False


# =============================================================================
# TEST 11 : Kill switch EU
# =============================================================================

class TestKillSwitchEu:

    def test_kill_switch_eu(self, base_state):
        """Kill switch declenche si PnL rolling 5j < -2% du capital alloue."""
        from scripts.paper_portfolio_eu import check_kill_switch_eu

        allocated_capital = 10_000.0  # -2% = -$200

        # PnL rolling 5j = -$250 (< -$200) => KILL
        base_state["strategy_pnl_log"] = {
            "eu_gap_open": [
                {"date": "2026-03-20", "pnl": -50.0},
                {"date": "2026-03-21", "pnl": -50.0},
                {"date": "2026-03-22", "pnl": -50.0},
                {"date": "2026-03-23", "pnl": -50.0},
                {"date": "2026-03-24", "pnl": -50.0},
            ]
        }
        assert check_kill_switch_eu(base_state, "eu_gap_open", allocated_capital) is True

        # PnL rolling 5j = -$100 (> -$200) => OK
        base_state["strategy_pnl_log"] = {
            "eu_gap_open": [
                {"date": "2026-03-20", "pnl": -20.0},
                {"date": "2026-03-21", "pnl": -20.0},
                {"date": "2026-03-22", "pnl": -20.0},
                {"date": "2026-03-23", "pnl": -20.0},
                {"date": "2026-03-24", "pnl": -20.0},
            ]
        }
        assert check_kill_switch_eu(base_state, "eu_gap_open", allocated_capital) is False

    def test_kill_switch_not_enough_history(self, base_state):
        """Kill switch ne declenche pas si < 5 jours d'historique."""
        from scripts.paper_portfolio_eu import check_kill_switch_eu

        base_state["strategy_pnl_log"] = {
            "eu_gap_open": [
                {"date": "2026-03-23", "pnl": -500.0},
                {"date": "2026-03-24", "pnl": -500.0},
            ]
        }
        # Seulement 2 jours => pas assez => False
        assert check_kill_switch_eu(base_state, "eu_gap_open", 10_000.0) is False

    def test_kill_switch_positive_pnl(self, base_state):
        """Kill switch ne declenche pas avec PnL positif."""
        from scripts.paper_portfolio_eu import check_kill_switch_eu

        base_state["strategy_pnl_log"] = {
            "bce_momentum_drift": [
                {"date": f"2026-03-{20+i}", "pnl": 100.0}
                for i in range(5)
            ]
        }
        assert check_kill_switch_eu(base_state, "bce_momentum_drift", 10_000.0) is False


# =============================================================================
# TEST 12 : Fermeture forcee positions EU
# =============================================================================

class TestForceCloseEuPositions:

    def test_force_close_eu_positions(self, base_state):
        """close_eu_positions ferme les positions EU via IBKR."""
        from scripts.paper_portfolio_eu import close_eu_positions

        # Simuler une position ouverte
        base_state["intraday_positions"] = {
            "BNP.PA": {
                "strategy": "bce_momentum_drift",
                "direction": "BUY",
                "entry_price": 55.0,
                "broker": "ibkr",
                "opened_at": "2026-03-24T10:00:00",
            }
        }

        mock_ibkr = MagicMock()
        mock_ibkr.close_position.return_value = {"orderId": "close-1", "status": "filled"}

        # Mock is_force_close_time pour ne pas bloquer (pas cross-tz)
        with patch("scripts.paper_portfolio_eu._get_ibkr", return_value=mock_ibkr), \
             patch("scripts.paper_portfolio_eu.is_force_close_time", return_value=False):
            close_eu_positions(base_state, dry_run=False)

        # Position fermee
        mock_ibkr.close_position.assert_called_once_with(
            "BNP.PA", _authorized_by="paper_portfolio_eu_close",
        )
        assert "BNP.PA" not in base_state["intraday_positions"]

    def test_force_close_cross_tz_delayed(self, base_state):
        """Positions cross-timezone ne sont pas fermees a 17:35 CET."""
        from scripts.paper_portfolio_eu import close_eu_positions, EU_STRATEGIES

        base_state["intraday_positions"] = {
            "SPY": {
                "strategy": "eu_close_us_afternoon",
                "direction": "BUY",
                "entry_price": 450.0,
                "broker": "alpaca",
                "opened_at": "2026-03-24T16:00:00",
            }
        }

        # is_force_close_time retourne False pour cross-tz (pas encore 22:00)
        with patch("scripts.paper_portfolio_eu.is_force_close_time", return_value=False), \
             patch("scripts.paper_portfolio_eu._get_ibkr") as mock_ibkr_fn, \
             patch("scripts.paper_portfolio_eu._get_smart_router") as mock_router_fn:
            close_eu_positions(base_state, dry_run=False)

        # Position PAS fermee (cross-tz, pas l'heure)
        assert "SPY" in base_state["intraday_positions"]

    def test_force_close_dry_run(self, base_state):
        """Dry-run ne ferme pas reellement les positions."""
        from scripts.paper_portfolio_eu import close_eu_positions

        base_state["intraday_positions"] = {
            "MC.PA": {
                "strategy": "eu_gap_open",
                "direction": "BUY",
                "entry_price": 800.0,
                "broker": "ibkr",
                "opened_at": "2026-03-24T09:10:00",
            }
        }

        with patch("scripts.paper_portfolio_eu.is_force_close_time", return_value=False):
            close_eu_positions(base_state, dry_run=True)

        # Position marquee comme fermee dans le state (dry-run clean)
        assert "MC.PA" not in base_state["intraday_positions"]

    def test_force_close_empty_positions(self, base_state):
        """Aucune erreur si pas de positions a fermer."""
        from scripts.paper_portfolio_eu import close_eu_positions

        base_state["intraday_positions"] = {}
        close_eu_positions(base_state, dry_run=False)
        # Pas d'exception


# =============================================================================
# TEST 13 : Execution avec _authorized_by
# =============================================================================

class TestExecutionAuthorization:

    def test_authorized_by_on_all_orders(self, mock_ibkr_broker, base_state):
        """Tous les ordres passent avec _authorized_by='paper_portfolio_eu'."""
        from scripts.paper_portfolio_eu import execute_eu_signals

        signals = {
            "eu_gap_open": [{
                "ticker": "MC.PA",
                "direction": "BUY",
                "qty": 5,
                "entry_price": 800.0,
                "stop_loss": 792.0,
                "take_profit": 816.0,
                "strategy": "eu_gap_open",
            }]
        }

        orders = execute_eu_signals(mock_ibkr_broker, signals, base_state, dry_run=False)

        mock_ibkr_broker.create_position.assert_called_once_with(
            symbol="MC.PA",
            direction="BUY",
            qty=5,
            stop_loss=792.0,
            take_profit=816.0,
            _authorized_by="paper_portfolio_eu",
        )
        assert len(orders) == 1
        assert orders[0]["action"] == "executed"

    def test_execution_dry_run_no_orders(self, mock_ibkr_broker, base_state):
        """Dry-run ne soumet pas d'ordres reels."""
        from scripts.paper_portfolio_eu import execute_eu_signals

        signals = {
            "eu_gap_open": [{
                "ticker": "MC.PA",
                "direction": "BUY",
                "qty": 5,
                "entry_price": 800.0,
                "stop_loss": 792.0,
                "take_profit": 816.0,
                "strategy": "eu_gap_open",
            }]
        }

        orders = execute_eu_signals(mock_ibkr_broker, signals, base_state, dry_run=True)

        mock_ibkr_broker.create_position.assert_not_called()
        assert len(orders) == 1
        assert orders[0]["action"] == "dry_run"


# =============================================================================
# TEST 14 : State persistence
# =============================================================================

class TestStatePersistence:

    def test_log_strategy_daily_pnl_eu(self, base_state):
        """log_strategy_daily_pnl_eu ajoute le PnL au log."""
        from scripts.paper_portfolio_eu import log_strategy_daily_pnl_eu

        log_strategy_daily_pnl_eu(base_state, "eu_gap_open", 150.0)
        log_strategy_daily_pnl_eu(base_state, "eu_gap_open", -50.0)

        log = base_state["strategy_pnl_log"]["eu_gap_open"]
        assert len(log) == 1  # meme jour => cumule
        assert log[0]["pnl"] == 100.0  # 150 - 50

    def test_state_save_load_roundtrip(self, base_state, tmp_path):
        """Le state se sauvegarde et se recharge correctement."""
        from scripts.paper_portfolio_eu import save_state, STATE_FILE

        state_path = tmp_path / "test_state.json"
        base_state["capital"] = 95_000.0
        base_state["allocations"] = {"eu_gap_open": {"pct": 0.2, "capital": 19000}}

        with patch("scripts.paper_portfolio_eu.STATE_FILE", state_path):
            save_state(base_state)
            assert state_path.exists()

            with open(state_path, "r") as f:
                loaded = json.load(f)

            assert loaded["capital"] == 95_000.0
            assert "eu_gap_open" in loaded["allocations"]


# =============================================================================
# TEST 15 : Signal dispatch map
# =============================================================================

class TestSignalDispatch:

    def test_all_strategies_have_signal_function(self, sample_strategies_yaml):
        """Chaque strategie du YAML a une fonction signal correspondante."""
        from scripts.paper_portfolio_eu import load_strategies_from_yaml, SIGNAL_DISPATCH

        strategies = load_strategies_from_yaml(sample_strategies_yaml)

        for sid in strategies:
            assert sid in SIGNAL_DISPATCH, \
                f"Strategie '{sid}' n'a pas de fonction signal dans SIGNAL_DISPATCH"
            assert callable(SIGNAL_DISPATCH[sid])
