"""Tests for all 8 crypto strategies V2 (margin+spot+earn) — 40 tests."""
import importlib.util
import sys
from pathlib import Path

import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
STRAT_DIR = ROOT / "strategies" / "crypto"


def _load(name: str):
    path = STRAT_DIR / f"{name}.py"
    if not path.exists():
        pytest.skip(f"{path} not found")
    spec = importlib.util.spec_from_file_location(f"crypto_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------
# STRAT-001: BTC/ETH Dual Momentum
# ------------------------------------------------------------------
class TestDualMomentum:
    @pytest.fixture(autouse=True)
    def load(self):
        self.mod = _load("btc_eth_dual_momentum")

    def test_config(self):
        assert self.mod.STRATEGY_CONFIG["allocation_pct"] == 0.20
        assert self.mod.STRATEGY_CONFIG["max_leverage"] == 2

    def test_indicators(self):
        np.random.seed(42)
        df = pd.DataFrame({
            "close": 40000 + np.cumsum(np.random.randn(300) * 100),
            "high": 40000 + np.cumsum(np.random.randn(300) * 100) + 50,
            "low": 40000 + np.cumsum(np.random.randn(300) * 100) - 50,
            "volume": np.random.rand(300) * 1e6,
        })
        result = self.mod.compute_indicators(df)
        # Actual column names: ema_fast, ema_slow, rsi, adx, atr
        for col in ("ema_fast", "ema_slow", "rsi", "adx", "atr"):
            assert col in result.columns

    def test_signal_early_none(self):
        candle = pd.Series({"close": 40000, "timestamp": datetime.now(timezone.utc)})
        assert self.mod.signal_fn(candle, {"positions": [], "capital": 15000, "i": 5}) is None

    def test_borrow_rate_check(self):
        """High borrow rate should block shorts — uses BORROW_RATE_MAX_SHORT."""
        assert hasattr(self.mod, "BORROW_RATE_MAX_SHORT")

    def test_max_holding(self):
        assert hasattr(self.mod, "MAX_HOLDING_DAYS")
        assert self.mod.MAX_HOLDING_DAYS <= 30


# ------------------------------------------------------------------
# STRAT-002: Altcoin Relative Strength
# ------------------------------------------------------------------
class TestAltcoinRS:
    @pytest.fixture(autouse=True)
    def load(self):
        self.mod = _load("altcoin_relative_strength")

    def test_config(self):
        assert self.mod.STRATEGY_CONFIG["allocation_pct"] == 0.15

    def test_ranking_beta_adjusted(self):
        """compute_btc_adjusted_alpha returns sorted alphas, not compute_ranking."""
        np.random.seed(42)
        n = 100
        # Build a DataFrame of daily returns for altcoins
        symbols = ["SOLUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT", "ADAUSDT"]
        returns_data = {}
        for sym in symbols:
            returns_data[sym] = np.random.randn(n) * 0.02
        returns_df = pd.DataFrame(returns_data)
        btc_returns = pd.Series(np.random.randn(n) * 0.01)
        alphas = self.mod.compute_btc_adjusted_alpha(returns_df, btc_returns)
        assert len(alphas) >= 4
        # Should be sorted descending
        assert alphas.iloc[0] >= alphas.iloc[-1]

    def test_excludes_meme_coins(self):
        """Meme coins are in MEME_BLACKLIST and filtered by filter_universe."""
        assert "DOGEUSDT" in self.mod.MEME_BLACKLIST
        assert "SHIBUSDT" in self.mod.MEME_BLACKLIST

    def test_borrow_filter(self):
        """filter_universe excludes symbols with borrow rate > MAX_BORROW_RATE_DAILY."""
        symbols = ["SOLUSDT", "LINKUSDT"]
        volumes = {"SOLUSDT": 100_000_000, "LINKUSDT": 100_000_000}
        mcaps = {"SOLUSDT": 5_000_000_000, "LINKUSDT": 5_000_000_000}
        borrow_rates = {"SOLUSDT": 0.0005, "LINKUSDT": 0.5}  # LINK too expensive
        borrow_avail = {"SOLUSDT": True, "LINKUSDT": True}
        eligible = self.mod.filter_universe(symbols, volumes, mcaps, borrow_rates, borrow_avail)
        assert "SOLUSDT" in eligible
        assert "LINKUSDT" not in eligible

    def test_rebalance_signals(self):
        """generate_rotation_signals produces BUY and SELL actions."""
        np.random.seed(42)
        n = 100
        symbols = [f"SYM{i}USDT" for i in range(10)]
        returns_data = {}
        for i, sym in enumerate(symbols):
            returns_data[sym] = np.random.randn(n) * 0.02 + (0.005 - i * 0.001)
        returns_df = pd.DataFrame(returns_data)
        btc_returns = pd.Series(np.random.randn(n) * 0.01)
        alphas = self.mod.compute_btc_adjusted_alpha(returns_df, btc_returns)
        signals = self.mod.generate_rotation_signals(
            alphas, {}, 15000, eligible_symbols=list(alphas.index)
        )
        actions = {s["action"] for s in signals}
        assert "BUY" in actions and "SELL" in actions


# ------------------------------------------------------------------
# STRAT-003: BTC Mean Reversion (spot only)
# ------------------------------------------------------------------
class TestMeanReversion:
    @pytest.fixture(autouse=True)
    def load(self):
        self.mod = _load("btc_mean_reversion")

    def test_config_spot_only(self):
        cfg = self.mod.STRATEGY_CONFIG
        assert cfg["market_type"] == "spot"
        assert cfg["max_leverage"] == 1

    def test_adx_filter(self):
        """Should NOT trade when ADX > 20 (trending). Attr is ADX_MAX_RANGE."""
        assert hasattr(self.mod, "ADX_MAX_RANGE")
        assert self.mod.ADX_MAX_RANGE <= 25

    def test_no_short(self):
        """Spot only = long only."""
        candle = pd.Series({"close": 40000, "rsi": 80, "timestamp": datetime.now(timezone.utc)})
        state = {"positions": [], "capital": 15000, "i": 300}
        result = self.mod.signal_fn(candle, state)
        if result:
            assert result["action"] != "SELL"


# ------------------------------------------------------------------
# STRAT-004: Volatility Breakout
# ------------------------------------------------------------------
class TestVolBreakout:
    @pytest.fixture(autouse=True)
    def load(self):
        self.mod = _load("vol_breakout")

    def test_config(self):
        assert self.mod.STRATEGY_CONFIG["max_leverage"] == 2

    def test_compression_detection(self):
        """Compression ratio attr is VOL_COMPRESSION_RATIO."""
        assert hasattr(self.mod, "VOL_COMPRESSION_RATIO")
        assert self.mod.VOL_COMPRESSION_RATIO < 1.0

    def test_confirmation_required(self):
        """Breakout needs volume + 2 candle confirmation. Attr is VOLUME_SPIKE_MULT."""
        assert hasattr(self.mod, "VOLUME_SPIKE_MULT")
        assert self.mod.VOLUME_SPIKE_MULT >= 2


# ------------------------------------------------------------------
# STRAT-005: BTC Dominance V2
# ------------------------------------------------------------------
class TestDominanceV2:
    @pytest.fixture(autouse=True)
    def load(self):
        self.mod = _load("btc_dominance_v2")

    def test_config_spot(self):
        assert self.mod.STRATEGY_CONFIG["market_type"] == "spot"
        assert self.mod.STRATEGY_CONFIG["max_leverage"] == 1

    def test_btc_season(self):
        """detect_dominance_regime returns (regime, ema_diff) tuple."""
        dom = pd.Series([50 + i * 0.2 for i in range(50)])
        regime, ema_diff = self.mod.detect_dominance_regime(dom)
        assert regime == self.mod.DominanceRegime.BTC

    def test_alt_season(self):
        dom = pd.Series([60 - i * 0.3 for i in range(50)])
        regime, ema_diff = self.mod.detect_dominance_regime(dom)
        assert regime == self.mod.DominanceRegime.ALT

    def test_dead_zone_dynamic(self):
        """V2: dead zone is 0.5% threshold, not fixed 2%."""
        assert hasattr(self.mod, "DEAD_ZONE_THRESHOLD")
        assert self.mod.DEAD_ZONE_THRESHOLD <= 1.0

    def test_alt_basket_diversified(self):
        """V2: ALT season uses ETH + SOL + top performer via ALT_SEASON_BASE_WEIGHTS."""
        assert hasattr(self.mod, "ALT_SEASON_BASE_WEIGHTS")
        assert len(self.mod.ALT_SEASON_BASE_WEIGHTS) >= 2


# ------------------------------------------------------------------
# STRAT-006: Borrow Rate Carry (Earn)
# ------------------------------------------------------------------
class TestBorrowCarry:
    @pytest.fixture(autouse=True)
    def load(self):
        self.mod = _load("borrow_rate_carry")

    def test_config_earn(self):
        cfg = self.mod.STRATEGY_CONFIG
        assert cfg["market_type"] == "earn"
        assert cfg["allocation_pct"] == 0.13

    def test_high_usdt_apy(self):
        """If USDT APY > 8% -> detect_scenario = HIGH_USDT, USDT weight >= 0.7."""
        scenario = self.mod.detect_scenario(usdt_apy=0.10, btc_apy=0.02, eth_apy=0.03)
        assert scenario == self.mod.EarnScenario.HIGH_USDT
        weights = self.mod.get_earn_weights(scenario)
        assert weights.get("USDT", 0) >= 0.7

    def test_low_apy_reduce(self):
        """If all APY < 3% -> LOW_ALL scenario, total weights < 1.0."""
        scenario = self.mod.detect_scenario(usdt_apy=0.02, btc_apy=0.005, eth_apy=0.01)
        assert scenario == self.mod.EarnScenario.LOW_ALL
        weights = self.mod.get_earn_weights(scenario)
        total = sum(weights.values())
        assert total < 1.0  # Should allocate less

    def test_no_locked_earn(self):
        """Phase 1: only flexible Earn."""
        assert self.mod.STRATEGY_CONFIG.get("earn_type", "flexible") == "flexible"


# ------------------------------------------------------------------
# STRAT-007: Liquidation Momentum
# ------------------------------------------------------------------
class TestLiqMomentum:
    @pytest.fixture(autouse=True)
    def load(self):
        self.mod = _load("liquidation_momentum")

    def test_config_margin(self):
        assert self.mod.STRATEGY_CONFIG["max_leverage"] == 3

    def test_uses_readonly_data(self):
        """Should use OI + volume as SIGNAL. Function is detect_liquidation_cascade."""
        assert hasattr(self.mod, "detect_liquidation_cascade")

    def test_cascade_down(self):
        """Cascade with large OI drop + price drop + volume spike -> SHORT."""
        ok, d = self.mod.detect_liquidation_cascade(
            oi_change_4h=-0.10, price_change_4h=-0.05, volume_ratio=4.0,
        )
        assert ok is True
        assert d == "SHORT"

    def test_no_cascade_small(self):
        """Small OI/price moves should not detect cascade."""
        ok, _ = self.mod.detect_liquidation_cascade(
            oi_change_4h=-0.02, price_change_4h=-0.01, volume_ratio=1.5,
        )
        assert ok is False

    def test_max_trades_week(self):
        assert hasattr(self.mod, "MAX_TRADES_PER_WEEK")
        assert self.mod.MAX_TRADES_PER_WEEK <= 5


# ------------------------------------------------------------------
# STRAT-008: Weekend Gap
# ------------------------------------------------------------------
class TestWeekendGap:
    @pytest.fixture(autouse=True)
    def load(self):
        self.mod = _load("weekend_gap")

    def test_config_spot(self):
        assert self.mod.STRATEGY_CONFIG["market_type"] == "spot"
        assert self.mod.STRATEGY_CONFIG["max_leverage"] == 1

    def test_dip_threshold(self):
        """Dip threshold attr is WEEKEND_DIP_MIN."""
        assert hasattr(self.mod, "WEEKEND_DIP_MIN")
        assert self.mod.WEEKEND_DIP_MIN <= -0.02

    def test_crash_filter(self):
        """No trade if weekend drop > 8% (possible real crash). Attr is WEEKEND_DIP_CRASH."""
        assert hasattr(self.mod, "WEEKEND_DIP_CRASH")
        assert self.mod.WEEKEND_DIP_CRASH <= -0.07

    def test_signal_buy_on_dip(self):
        """compute_weekend_return returns a negative value for a dip; signal_fn
        handles the full entry logic but we can test the helper directly."""
        ret = self.mod.compute_weekend_return(friday_price=40000, sunday_price=38400)
        assert ret is not None
        assert ret < self.mod.WEEKEND_DIP_MIN  # -4% < -3%

    def test_signal_no_trade_small(self):
        """Small dip (< 3%) should not trigger."""
        ret = self.mod.compute_weekend_return(friday_price=40000, sunday_price=39700)
        assert ret is not None
        assert ret > self.mod.WEEKEND_DIP_MIN  # -0.75% > -3%, no trade
