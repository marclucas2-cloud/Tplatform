"""Tests unitaires pour mes_mr_vix_spike (research autonome 2026-04-23).

Verifications:
  1. Strategy instanciable + parametres par defaut corrects
  2. on_bar returns None si data_feed absent
  3. on_bar returns None si pas 3 down days consecutifs
  4. on_bar returns None si VIX < vix_min
  5. on_bar returns BUY Signal avec SL/TP corrects sur setup valide
  6. Non-regression: strategy registree dans quant_registry + whitelist
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


class TestMESMRVixSpikeStrategy:
    """Instance + on_bar semantics."""

    def _get_strategy(self):
        from strategies_v2.futures.mes_mr_vix_spike import MESMeanReversionVIXSpike
        return MESMeanReversionVIXSpike()

    def test_default_params_robust_config(self):
        s = self._get_strategy()
        assert s.consec_days == 3
        assert s.hold_days == 4
        assert s.vix_min == 15.0
        assert s.sl_points == 25.0
        assert s.tp_points == 50.0

    def test_symbol_futures_ibkr(self):
        s = self._get_strategy()
        assert s.SYMBOL == "MES"
        assert s.VIX_SYMBOL == "VIX"
        assert s.asset_class == "futures"
        assert s.broker == "ibkr"
        assert s.name == "mes_mr_vix_spike"

    def test_no_data_feed_returns_none(self):
        s = self._get_strategy()
        from core.backtester_v2.types import Bar
        bar = Bar(timestamp=pd.Timestamp("2026-04-23"), symbol="MES",
                  open=5000, high=5010, low=4990, close=4995, volume=100)
        assert s.on_bar(bar, MagicMock()) is None

    def test_only_2_down_days_no_signal(self):
        s = self._get_strategy()
        from core.backtester_v2.types import Bar
        feed = MagicMock()
        # 3 bars : only 2 red
        bars = pd.DataFrame([
            {"open": 5000, "high": 5010, "low": 4980, "close": 5005},  # green
            {"open": 5005, "high": 5010, "low": 4980, "close": 4990},  # red
            {"open": 4990, "high": 4995, "low": 4970, "close": 4975},  # red
        ])
        feed.get_bars.return_value = bars
        s.set_data_feed(feed)
        bar = Bar(timestamp=pd.Timestamp("2026-04-23"), symbol="MES",
                  open=4975, high=4980, low=4960, close=4970, volume=100)
        assert s.on_bar(bar, MagicMock()) is None

    def test_3_down_days_vix_low_no_signal(self):
        s = self._get_strategy()
        from core.backtester_v2.types import Bar
        feed = MagicMock()
        mes_bars = pd.DataFrame([
            {"open": 5000, "high": 5010, "low": 4980, "close": 4990},  # red
            {"open": 4990, "high": 4995, "low": 4970, "close": 4975},  # red
            {"open": 4975, "high": 4980, "low": 4955, "close": 4960},  # red
        ])
        vix_bars = pd.DataFrame([{"open": 13, "high": 14, "low": 12, "close": 12.5}])

        def side_effect(sym, n):
            return mes_bars if sym == "MES" else vix_bars
        feed.get_bars.side_effect = side_effect
        s.set_data_feed(feed)
        bar = Bar(timestamp=pd.Timestamp("2026-04-23"), symbol="MES",
                  open=4960, high=4965, low=4945, close=4950, volume=100)
        assert s.on_bar(bar, MagicMock()) is None  # VIX 12.5 <= 15

    def test_3_down_days_vix_high_produces_long_signal(self):
        s = self._get_strategy()
        from core.backtester_v2.types import Bar
        feed = MagicMock()
        feed.timestamp = pd.Timestamp("2026-04-24")
        mes_bars = pd.DataFrame([
            {"open": 5000, "high": 5010, "low": 4980, "close": 4990},
            {"open": 4990, "high": 4995, "low": 4970, "close": 4975},
            {"open": 4975, "high": 4980, "low": 4955, "close": 4960},
        ])
        vix_bars = pd.DataFrame([{"open": 18, "high": 20, "low": 17, "close": 19.5}])

        def side_effect(sym, n):
            return mes_bars if sym == "MES" else vix_bars
        feed.get_bars.side_effect = side_effect
        s.set_data_feed(feed)
        bar = Bar(timestamp=pd.Timestamp("2026-04-23"), symbol="MES",
                  open=4960, high=4965, low=4945, close=4950, volume=100)
        sig = s.on_bar(bar, MagicMock())
        assert sig is not None
        assert sig.symbol == "MES"
        assert sig.side == "BUY"
        assert sig.stop_loss == pytest.approx(4950 - 25.0)
        assert sig.take_profit == pytest.approx(4950 + 50.0)
        assert sig.strategy_name == "mes_mr_vix_spike"

    def test_no_signal_on_stale_bar_relative_to_feed_timestamp(self):
        s = self._get_strategy()
        from core.backtester_v2.types import Bar
        feed = MagicMock()
        feed.timestamp = pd.Timestamp("2026-04-25")
        mes_bars = pd.DataFrame([
            {"open": 5000, "high": 5010, "low": 4980, "close": 4990},
            {"open": 4990, "high": 4995, "low": 4970, "close": 4975},
            {"open": 4975, "high": 4980, "low": 4955, "close": 4960},
        ])
        vix_bars = pd.DataFrame([{"open": 18, "high": 20, "low": 17, "close": 19.5}])

        def side_effect(sym, n):
            return mes_bars if sym == "MES" else vix_bars

        feed.get_bars.side_effect = side_effect
        s.set_data_feed(feed)
        bar = Bar(
            timestamp=pd.Timestamp("2026-04-08"),
            symbol="MES",
            open=4960,
            high=4965,
            low=4945,
            close=4950,
            volume=100,
        )
        assert s.on_bar(bar, MagicMock()) is None


class TestRegistryIntegration:
    """Verifie que la strat est bien enregistree dans les configs."""

    def test_in_quant_registry(self):
        reg = yaml.safe_load((ROOT / "config" / "quant_registry.yaml").read_text(encoding="utf-8"))
        ids = [s["strategy_id"] for s in reg["strategies"]]
        assert "mes_mr_vix_spike" in ids
        entry = next(s for s in reg["strategies"] if s["strategy_id"] == "mes_mr_vix_spike")
        assert entry["status"] == "paper_only"
        assert entry["book"] == "ibkr_futures"
        assert entry["grade"] == "A"
        assert entry["paper_start_at"] == "2026-04-23"
        assert entry["is_live"] is False

    def test_in_live_whitelist(self):
        wl = yaml.safe_load((ROOT / "config" / "live_whitelist.yaml").read_text(encoding="utf-8"))
        ibkr = wl.get("ibkr_futures", [])
        ids = [s["strategy_id"] for s in ibkr]
        assert "mes_mr_vix_spike" in ids
        entry = next(s for s in ibkr if s["strategy_id"] == "mes_mr_vix_spike")
        assert entry["status"] == "paper_only"
        assert entry["consec_days"] == 3
        assert entry["hold_days"] == 4
        assert entry["vix_min"] == 15.0

    def test_wf_manifest_exists(self):
        manifest = ROOT / "data" / "research" / "wf_manifests" / "mes_mr_vix_spike_2026-04-23.json"
        assert manifest.exists(), f"WF manifest missing: {manifest}"
        import json
        data = json.loads(manifest.read_text(encoding="utf-8"))
        assert data["strategy_id"] == "mes_mr_vix_spike"
        assert data["summary"]["grade"] == "A"
        assert data["summary"]["verdict"] == "VALIDATED"
        assert data["summary"]["pass_rate"] == 1.00
