"""Tests Phase 3.5 desk productif 2026-04-22 - non-regression cleanup.

Valide que :
  - Les 13+ strats legacy ne sont plus importees/appelees depuis futures_runner
  - Seules les sleeves canoniques V16 (mes_calendar + mcl_overnight) restent
    dans le paper block
  - CAM.get_top_pick() nouvelle signature respecte la regle "reserve uniquement
    si position active OU rebal window ouverte"
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT_PATH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_PATH))


class TestLegacyStratsRemoved:
    """Les strats retirees du catalogue V16 ne doivent plus etre appelees."""

    def test_futures_runner_source_no_legacy_imports(self):
        src = (ROOT_PATH / "core" / "worker" / "cycles" / "futures_runner.py").read_text(
            encoding="utf-8",
        )
        # Chaque strat legacy DOIT etre absente du source (plus d'execution)
        legacy_imports = [
            "from strategies_v2.futures.mes_trend import MESTrend",
            "from strategies_v2.futures.mes_trend_mr import MESTrendMR",
            "from strategies_v2.futures.mes_3day_stretch import MES3DayStretch",
            "from strategies_v2.futures.overnight_buy_close import OvernightBuyClose",
            "from strategies_v2.futures.tsmom_multi import TSMOMMulti",
            "from strategies_v2.futures.m2k_orb import M2KORB",
            "from strategies_v2.futures.mcl_brent_lag import MCLBrentLag",
            "from strategies_v2.futures.mgc_vix_hedge import MGCVixHedge",
            "from strategies_v2.futures.thursday_rally import ThursdayRally",
            "from strategies_v2.futures.friday_monday_mnq import FridayMondayMNQ",
            "from strategies_v2.futures.multi_tf_mom_mes import MultiTFMomMES",
            "from strategies_v2.futures.bb_squeeze_mes import BBSqueezeMES",
            "from strategies_v2.futures.rs_mes_mnq_rotate import RSMesMnqRotate",
            "from strategies_v2.futures.commodity_season import CommoditySeason",
            "from strategies_v2.futures.mes_mnq_pairs import MESMNQPairs",
            "from strategies_v2.futures.mib_estx50_spread import MIBEstx50Spread",
        ]
        for imp in legacy_imports:
            assert imp not in src, f"Legacy import encore present: {imp}"

    def test_canonical_paper_sleeves_preserved(self):
        """MES calendar paper + mcl_overnight doivent rester."""
        src = (ROOT_PATH / "core" / "worker" / "cycles" / "futures_runner.py").read_text(
            encoding="utf-8",
        )
        assert "from strategies_v2.futures.mes_calendar_paper import" in src
        assert "MESMondayLong, MESWednesdayLong, MESPreHolidayLong" in src
        assert "from strategies_v2.futures.mcl_overnight_mon_trend import MCLOvernightMonTrend" in src


class TestCamGetTopPickRule:
    """Nouvelle regle CAM reserve uniquement si position active OR eligible."""

    def _make_cam(self, last_rebal_days_ago: int = None):
        """Helper: instantie CAM avec un last_rebal simule."""
        from strategies_v2.futures.cross_asset_momentum import CrossAssetMomentum
        import pandas as pd

        cam = CrossAssetMomentum()
        if last_rebal_days_ago is not None:
            now = pd.Timestamp.utcnow().tz_localize(None)
            cam._last_rebal_ts = now - pd.Timedelta(days=last_rebal_days_ago)
        return cam

    def test_no_data_feed_returns_none(self):
        cam = self._make_cam()
        assert cam.get_top_pick() is None

    def test_cooldown_without_position_returns_none(self, monkeypatch):
        """CAM en cooldown + pas de position => ne reserve rien."""
        import pandas as pd

        from core.backtester_v2.types import Bar, PortfolioState

        # last_rebal = 5j ago, rebal_days = 20 => days_since (5) < rebal_days (20) => cooldown actif
        cam = self._make_cam(last_rebal_days_ago=5)

        # Mock data_feed pour satisfaire la structure (pas utilise en cooldown)
        class _FakeFeed:
            def get_bars(self, sym, n):
                return None

            def get_latest_bar(self, sym):
                return None

        cam.set_data_feed(_FakeFeed())

        bar = Bar(
            timestamp=pd.Timestamp.utcnow().tz_localize(None),
            symbol="MES", open=0, high=0, low=0, close=100.0, volume=0,
        )
        portfolio_state = PortfolioState(cash=10000, positions={}, equity=10000)

        result = cam.get_top_pick(bar=bar, portfolio_state=portfolio_state)
        assert result is None, "Cooldown + pas de position doit donner None"

    def test_active_position_returns_symbol(self):
        """CAM avec position active => reserve ce symbole meme en cooldown."""
        import pandas as pd

        from core.backtester_v2.types import Bar, PortfolioState

        cam = self._make_cam(last_rebal_days_ago=5)  # cooldown actif

        class _FakeFeed:
            def get_bars(self, sym, n):
                return None

            def get_latest_bar(self, sym):
                return None

        cam.set_data_feed(_FakeFeed())

        bar = Bar(
            timestamp=pd.Timestamp.utcnow().tz_localize(None),
            symbol="MES", open=0, high=0, low=0, close=100.0, volume=0,
        )

        # Portfolio avec une position MCL detenue par CAM
        class _FakePosition:
            strategy_name = "cross_asset_mom"
            qty = 1

        portfolio_state = PortfolioState(
            cash=10000,
            positions={"MCL": _FakePosition()},
            equity=10000,
        )

        result = cam.get_top_pick(bar=bar, portfolio_state=portfolio_state)
        assert result == "MCL", f"Position active doit etre reservee, got {result}"

    def test_eligible_rebal_computes_top_pick(self):
        """CAM en fenetre rebal active sans position => calcule top_pick."""
        import pandas as pd
        import numpy as np

        from core.backtester_v2.types import Bar, PortfolioState
        from strategies_v2.futures.cross_asset_momentum import CrossAssetMomentum

        cam = CrossAssetMomentum(lookback_days=20, min_momentum=0.02, rebal_days=20)
        # Jamais rebal => eligible a entrer
        cam._last_rebal_ts = None

        # Mock data_feed avec MCL ayant le meilleur momentum (+10%)
        class _FakeFeed:
            def get_bars(self, sym, n):
                if sym == "MCL":
                    # 22 bars avec close 100 -> 110 (+10%)
                    data = {"close": np.linspace(100, 110, 22)}
                    return pd.DataFrame(data)
                elif sym == "MES":
                    data = {"close": np.linspace(100, 101, 22)}  # +1%
                    return pd.DataFrame(data)
                elif sym in ("MNQ", "M2K", "MGC"):
                    data = {"close": np.linspace(100, 100.5, 22)}
                    return pd.DataFrame(data)
                return None

        cam.set_data_feed(_FakeFeed())

        bar = Bar(
            timestamp=pd.Timestamp.utcnow().tz_localize(None),
            symbol="MES", open=0, high=0, low=0, close=100.0, volume=0,
        )
        portfolio_state = PortfolioState(cash=10000, positions={}, equity=10000)

        result = cam.get_top_pick(bar=bar, portfolio_state=portfolio_state)
        assert result == "MCL", f"Top momentum doit etre MCL (+10%), got {result}"

    def test_backward_compat_no_args(self):
        """get_top_pick() sans args doit fonctionner (backward compat ancien appel).

        Si bar est None, on skip le check cooldown et on calcule le pick theorique.
        Utile pour des audit reports ou tests isoles.
        """
        import pandas as pd
        import numpy as np

        from strategies_v2.futures.cross_asset_momentum import CrossAssetMomentum

        cam = CrossAssetMomentum()

        class _FakeFeed:
            def get_bars(self, sym, n):
                if sym == "MCL":
                    return pd.DataFrame({"close": np.linspace(100, 110, 22)})
                return pd.DataFrame({"close": np.linspace(100, 100.5, 22)})

        cam.set_data_feed(_FakeFeed())
        result = cam.get_top_pick()  # sans args => ancien comportement (ignore cooldown)
        assert result == "MCL"
