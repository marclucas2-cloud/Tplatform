"""Tests pour VixStressGuard -- reduction de sizing en cas de stress marche."""

from unittest.mock import MagicMock, patch

import pytest

from core.vix_stress_guard import CRITICAL, HALT, NORMAL, WARN, VixStressGuard

# --- Fixtures ---

@pytest.fixture
def guard():
    """Guard avec seuils par defaut."""
    return VixStressGuard()


@pytest.fixture
def custom_guard():
    """Guard avec seuils custom pour tests specifiques."""
    return VixStressGuard(vix_warn=25, vix_critical=35, spy_dd_warn=2.0, spy_dd_halt=4.0)


# --- Tests ---

class TestNormalConditions:
    """Conditions normales : sizing inchange."""

    def test_normal_conditions(self, guard):
        """VIX bas + SPY stable = sizing 100%."""
        result = guard.check(vix_level=18.0, spy_change_pct=-0.5)
        assert result["sizing_factor"] == 1.0
        assert result["level"] == NORMAL
        assert "normales" in result["reason"].lower() or "normal" in result["reason"].lower()

    def test_normal_vix_below_warn(self, guard):
        """VIX = 29.9 (juste sous le seuil) = pas de reduction."""
        result = guard.check(vix_level=29.9, spy_change_pct=0.0)
        assert result["sizing_factor"] == 1.0
        assert result["level"] == NORMAL

    def test_normal_spy_positive(self, guard):
        """SPY en hausse = pas de reduction."""
        result = guard.check(vix_level=20.0, spy_change_pct=1.5)
        assert result["sizing_factor"] == 1.0
        assert result["level"] == NORMAL


class TestVixWarn30:
    """VIX > 30 : reduction 50%."""

    def test_vix_warn_30(self, guard):
        """VIX = 32 -> sizing x0.50."""
        result = guard.check(vix_level=32.0, spy_change_pct=0.0)
        assert result["sizing_factor"] == 0.50
        assert result["level"] == WARN
        assert "VIX" in result["reason"]

    def test_vix_exactly_30_no_trigger(self, guard):
        """VIX = 30.0 exactement : ne declenche pas (>30, pas >=30)."""
        result = guard.check(vix_level=30.0, spy_change_pct=0.0)
        assert result["sizing_factor"] == 1.0
        assert result["level"] == NORMAL

    def test_vix_30_1_triggers(self, guard):
        """VIX = 30.1 -> warn declenche."""
        result = guard.check(vix_level=30.1, spy_change_pct=0.0)
        assert result["sizing_factor"] == 0.50
        assert result["level"] == WARN


class TestVixCritical40:
    """VIX > 40 : reduction 75%."""

    def test_vix_critical_40(self, guard):
        """VIX = 45 -> sizing x0.25."""
        result = guard.check(vix_level=45.0, spy_change_pct=0.0)
        assert result["sizing_factor"] == 0.25
        assert result["level"] == CRITICAL
        assert "critical" in result["reason"].lower()

    def test_vix_41_triggers_critical(self, guard):
        """VIX = 41 -> critical (pas juste warn)."""
        result = guard.check(vix_level=41.0, spy_change_pct=0.0)
        assert result["sizing_factor"] == 0.25
        assert result["level"] == CRITICAL


class TestSpyDdWarn3Pct:
    """SPY drawdown > 3% : reduction 50%."""

    def test_spy_dd_warn_3pct(self, guard):
        """SPY -3.5% -> sizing x0.50."""
        result = guard.check(vix_level=20.0, spy_change_pct=-3.5)
        assert result["sizing_factor"] == 0.50
        assert result["level"] == WARN
        assert "SPY" in result["reason"]

    def test_spy_dd_exactly_3_no_trigger(self, guard):
        """SPY -3.0% exactement : ne declenche pas (>3, pas >=3)."""
        result = guard.check(vix_level=20.0, spy_change_pct=-3.0)
        assert result["sizing_factor"] == 1.0
        assert result["level"] == NORMAL

    def test_spy_dd_3_1_triggers(self, guard):
        """SPY -3.1% -> warn declenche."""
        result = guard.check(vix_level=20.0, spy_change_pct=-3.1)
        assert result["sizing_factor"] == 0.50
        assert result["level"] == WARN


class TestSpyDdHalt5Pct:
    """SPY drawdown > 5% : HALT complet."""

    def test_spy_dd_halt_5pct(self, guard):
        """SPY -6% -> sizing = 0 (HALT)."""
        result = guard.check(vix_level=20.0, spy_change_pct=-6.0)
        assert result["sizing_factor"] == 0.0
        assert result["level"] == HALT
        assert "HALT" in result["reason"]

    def test_spy_dd_5_1_triggers_halt(self, guard):
        """SPY -5.1% -> HALT."""
        result = guard.check(vix_level=20.0, spy_change_pct=-5.1)
        assert result["sizing_factor"] == 0.0
        assert result["level"] == HALT


class TestCombinedVixAndSpy:
    """Combinaison VIX + SPY : prend le plus restrictif."""

    def test_combined_vix_and_spy(self, guard):
        """VIX warn (x0.50) + SPY DD halt (x0.0) -> sizing = 0.0, level = HALT."""
        result = guard.check(vix_level=35.0, spy_change_pct=-6.0)
        assert result["sizing_factor"] == 0.0
        assert result["level"] == HALT

    def test_combined_vix_critical_spy_warn(self, guard):
        """VIX critical (x0.25) + SPY warn (x0.50) -> sizing = 0.25, level = CRITICAL."""
        result = guard.check(vix_level=42.0, spy_change_pct=-3.5)
        assert result["sizing_factor"] == 0.25
        assert result["level"] == CRITICAL

    def test_combined_both_warn(self, guard):
        """VIX warn (x0.50) + SPY warn (x0.50) -> sizing = 0.50, level = WARN."""
        result = guard.check(vix_level=32.0, spy_change_pct=-3.5)
        assert result["sizing_factor"] == 0.50
        assert result["level"] == WARN

    def test_combined_vix_warn_spy_normal(self, guard):
        """VIX warn (x0.50) + SPY normal (x1.0) -> sizing = 0.50."""
        result = guard.check(vix_level=35.0, spy_change_pct=-1.0)
        assert result["sizing_factor"] == 0.50
        assert result["level"] == WARN


class TestSizingFactorCached:
    """get_sizing_factor retourne le resultat cache du dernier check."""

    def test_sizing_factor_cached(self, guard):
        """Le facteur est cache entre deux appels a check()."""
        # Avant tout check : defaut = 1.0
        assert guard.get_sizing_factor() == 1.0

        # Premier check : warn
        guard.check(vix_level=35.0, spy_change_pct=0.0)
        assert guard.get_sizing_factor() == 0.50

        # Deuxieme check : critical
        guard.check(vix_level=45.0, spy_change_pct=0.0)
        assert guard.get_sizing_factor() == 0.25

        # Retour a la normale
        guard.check(vix_level=18.0, spy_change_pct=0.5)
        assert guard.get_sizing_factor() == 1.0

    def test_get_status_after_check(self, guard):
        """get_status retourne l'etat complet apres un check."""
        guard.check(vix_level=35.0, spy_change_pct=-4.0)
        status = guard.get_status()

        assert status["level"] == WARN
        assert status["sizing_factor"] == 0.50
        assert status["vix_level"] == 35.0
        assert status["spy_change_pct"] == -4.0
        assert status["last_check"] is not None


class TestFetchReturnsNoneGracefully:
    """fetch_vix_level / fetch_spy_change retournent None sans crash."""

    def test_fetch_returns_none_gracefully(self, guard):
        """Si les deux fetches retournent None, le guard reste en mode NORMAL."""
        # Mock les fetches pour isoler du reseau
        with patch.object(guard, "fetch_vix_level", return_value=None), \
             patch.object(guard, "fetch_spy_change", return_value=None):
            result = guard.check(vix_level=None, spy_change_pct=None)
        assert result["sizing_factor"] == 1.0
        assert result["level"] == NORMAL

    @patch("core.vix_stress_guard._HAS_YFINANCE", False)
    def test_fetch_vix_without_yfinance(self):
        """Sans yfinance installe, fetch_vix_level retourne None."""
        guard = VixStressGuard()
        assert guard.fetch_vix_level() is None

    @patch("core.vix_stress_guard._HAS_YFINANCE", False)
    def test_fetch_spy_without_yfinance(self):
        """Sans yfinance installe, fetch_spy_change retourne None."""
        guard = VixStressGuard()
        assert guard.fetch_spy_change() is None

    @patch("core.vix_stress_guard._HAS_YFINANCE", True)
    @patch("core.vix_stress_guard.yf")
    def test_fetch_vix_yfinance_error(self, mock_yf):
        """Si yfinance leve une exception, retourne None sans crash."""
        mock_yf.Ticker.side_effect = Exception("API error")
        guard = VixStressGuard()
        assert guard.fetch_vix_level() is None

    @patch("core.vix_stress_guard._HAS_YFINANCE", True)
    @patch("core.vix_stress_guard.yf")
    def test_fetch_spy_yfinance_error(self, mock_yf):
        """Si yfinance leve une exception, retourne None sans crash."""
        mock_yf.Ticker.side_effect = Exception("API error")
        guard = VixStressGuard()
        assert guard.fetch_spy_change() is None

    @patch("core.vix_stress_guard._HAS_YFINANCE", True)
    @patch("core.vix_stress_guard.yf")
    def test_fetch_vix_empty_history(self, mock_yf):
        """Si yfinance retourne un DataFrame vide, retourne None."""
        import pandas as pd
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame()
        mock_yf.Ticker.return_value = mock_ticker
        guard = VixStressGuard()
        assert guard.fetch_vix_level() is None

    @patch("core.vix_stress_guard._HAS_YFINANCE", True)
    @patch("core.vix_stress_guard.yf")
    def test_auto_fetch_in_check(self, mock_yf):
        """check() sans args tente l'auto-fetch et gere None gracieusement."""
        mock_yf.Ticker.side_effect = Exception("No network")
        guard = VixStressGuard()
        # Ne devrait pas crash
        result = guard.check()
        assert result["sizing_factor"] == 1.0
        assert result["level"] == NORMAL
