"""Tests for ExecutionSimulator and cost models — 20+ test cases."""

from __future__ import annotations

import sys
import types as _types_mod
from dataclasses import dataclass
from datetime import datetime

import pytest

# -------------------------------------------------------------------------
# Lightweight stubs — mirror the types API being built concurrently.
# Patched into sys.modules BEFORE any backtester_v2 code is imported.
# -------------------------------------------------------------------------

@dataclass
class _StubOrder:
    symbol: str = "AAPL"
    quantity: float = 100.0
    side: str = "BUY"
    order_type: str = "MARKET"
    asset_class: str = "EQUITY_US"
    broker: str = "IBKR"
    limit_price: float | None = None


@dataclass
class _StubMarketState:
    mid_price: float = 150.0
    is_open: bool = True
    volatility: float = 0.02
    adv: float = 1e8
    hour: int = 12
    available_cash: float = 1_000_000.0


@dataclass
class _StubPosition:
    symbol: str = "BTCUSDT"
    quantity: float = 1.0
    avg_price: float = 60_000.0


class _RejectedDescriptor:
    """Descriptor that acts as classmethod for Fill.rejected(...) calls
    and as a bool property on instances."""

    def __get__(self, obj, objtype=None):
        if obj is None:
            # Class-level access: return factory function
            return objtype._create_rejected
        # Instance-level access: return bool
        return obj._is_rejected


class _FillResult:
    """Minimal Fill for tests."""

    rejected = _RejectedDescriptor()

    def __init__(
        self,
        order: object = None,
        fill_price: float = 0.0,
        quantity: float = 0.0,
        commission: float = 0.0,
        latency_ms: int = 0,
        spread_bps: float = 0.0,
        impact: float = 0.0,
        side: str = "BUY",
        _is_rejected: bool = False,
        reject_reason: str = "",
    ):
        self.order = order
        self.fill_price = fill_price
        self.quantity = quantity
        self.commission = commission
        self.latency_ms = latency_ms
        self.spread_bps = spread_bps
        self.impact = impact
        self.side = side
        self._is_rejected = _is_rejected
        self.reject_reason = reject_reason

    @classmethod
    def _create_rejected(cls, order, reason: str = "", latency_ms: int = 0):
        """Create a rejected fill."""
        return cls(
            order=order,
            _is_rejected=True,
            reject_reason=reason,
            latency_ms=latency_ms,
        )


# Stub modules for types and other not-yet-built backtester_v2 submodules.
# Save originals so we can restore them after importing execution_simulator —
# otherwise these stubs poison sys.modules for other test files in the suite.
_fake_types = _types_mod.ModuleType("core.backtester_v2.types")
_fake_types.Order = _StubOrder
_fake_types.Fill = _FillResult
_fake_types.MarketState = _StubMarketState
_fake_types.EventType = type("EventType", (), {})
_fake_types.Event = type("Event", (), {})
_fake_types.Bar = type("Bar", (), {})
_fake_types.Signal = type("Signal", (), {})
_fake_types.PortfolioState = type("PortfolioState", (), {})
_fake_types.BacktestConfig = type("BacktestConfig", (), {})
_fake_types.BacktestResults = type("BacktestResults", (), {})

# Save original modules before patching
_saved_modules: dict = {}
_modules_to_patch = [
    "core.backtester_v2.types",
    "core.backtester_v2",
]
for _mod_name in ("event_queue", "data_feed", "engine", "strategy_base"):
    _modules_to_patch.append(f"core.backtester_v2.{_mod_name}")

for _key in _modules_to_patch:
    if _key in sys.modules:
        _saved_modules[_key] = sys.modules[_key]

# Pre-register stubs before any backtester_v2 imports
sys.modules["core.backtester_v2.types"] = _fake_types
# Stub other missing modules referenced by __init__.py
for _mod_name in ("event_queue", "data_feed", "engine", "strategy_base"):
    _fqn = f"core.backtester_v2.{_mod_name}"
    if _fqn not in sys.modules:
        _stub = _types_mod.ModuleType(_fqn)
        # Add dummy classes so __init__.py imports don't fail
        _stub.EventQueue = type("EventQueue", (), {})
        _stub.DataFeed = type("DataFeed", (), {})
        _stub.BacktesterV2 = type("BacktesterV2", (), {})
        _stub.StrategyBase = type("StrategyBase", (), {})
        sys.modules[_fqn] = _stub

# Now safe to import the real code (binds stub types into execution_simulator)
from core.backtester_v2.cost_models.binance_costs import BinanceCostModel
from core.backtester_v2.cost_models.funding_model import FundingCostModel
from core.backtester_v2.cost_models.ibkr_costs import IBKRCostModel
from core.backtester_v2.execution_simulator import (
    LATENCY,
    ExecutionSimulator,
)

# Restore original modules so other test files get the real types
for _key, _orig in _saved_modules.items():
    sys.modules[_key] = _orig
# Remove stub entries that didn't exist before
for _key in _modules_to_patch:
    if _key not in _saved_modules and _key in sys.modules:
        del sys.modules[_key]


# -------------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------------

@pytest.fixture
def sim() -> ExecutionSimulator:
    return ExecutionSimulator(seed=42)


@pytest.fixture
def ibkr_cost() -> IBKRCostModel:
    return IBKRCostModel()


@pytest.fixture
def binance_cost() -> BinanceCostModel:
    return BinanceCostModel(bnb_discount=False)


@pytest.fixture
def binance_bnb() -> BinanceCostModel:
    return BinanceCostModel(bnb_discount=True)


@pytest.fixture
def market() -> _StubMarketState:
    return _StubMarketState()


@pytest.fixture
def order() -> _StubOrder:
    return _StubOrder()


# =========================================================================
# 1. Latency tests
# =========================================================================

class TestLatency:
    def test_latency_within_range(self, sim: ExecutionSimulator):
        """Latency should be clipped to [min_ms, max_ms]."""
        cfg = LATENCY["IBKR"]
        for _ in range(200):
            lat = sim._simulate_latency("IBKR")
            assert cfg.min_ms <= lat <= cfg.max_ms

    def test_latency_binance_faster_on_average(self):
        """Binance latencies should average lower than IBKR."""
        sim = ExecutionSimulator(seed=7)
        ibkr = [sim._simulate_latency("IBKR") for _ in range(500)]
        bnb = [sim._simulate_latency("BINANCE") for _ in range(500)]
        assert sum(bnb) / len(bnb) < sum(ibkr) / len(ibkr)


# =========================================================================
# 2. Spread tests
# =========================================================================

class TestSpread:
    def test_spread_fx_vs_crypto(self, sim: ExecutionSimulator, market):
        """Crypto ALT_T3 should have wider spread than FX_MAJOR."""
        fx_order = _StubOrder(asset_class="FX_MAJOR")
        crypto_order = _StubOrder(asset_class="CRYPTO_ALT_T3")
        sp_fx = sim._calculate_spread(fx_order, market)
        sp_crypto = sim._calculate_spread(crypto_order, market)
        assert sp_crypto > sp_fx

    def test_spread_equity_vs_crypto_btc(self, sim, market):
        """BTC spread should be >= equity large spread at normal conditions."""
        eq = _StubOrder(asset_class="EQUITY_LARGE")
        btc = _StubOrder(asset_class="CRYPTO_BTC")
        assert sim._calculate_spread(btc, market) >= sim._calculate_spread(eq, market)

    def test_spread_increases_off_peak(self, sim: ExecutionSimulator):
        """Spread should be wider during off-peak hours."""
        order_fx = _StubOrder(asset_class="FX_MAJOR")
        peak = _StubMarketState(hour=10)
        off_peak = _StubMarketState(hour=3)
        assert sim._calculate_spread(order_fx, off_peak) > sim._calculate_spread(order_fx, peak)

    def test_spread_increases_with_volatility(self, sim):
        """Higher volatility should widen spread."""
        order_eq = _StubOrder(asset_class="EQUITY_US")
        low_vol = _StubMarketState(volatility=0.01)
        high_vol = _StubMarketState(volatility=0.05)
        assert sim._calculate_spread(order_eq, high_vol) > sim._calculate_spread(order_eq, low_vol)

    def test_spread_increases_with_low_liquidity(self, sim):
        """Lower ADV should widen spread."""
        order_eq = _StubOrder(asset_class="EQUITY_US")
        liq = _StubMarketState(adv=1e10)
        illiq = _StubMarketState(adv=1e6)
        assert sim._calculate_spread(order_eq, illiq) > sim._calculate_spread(order_eq, liq)


# =========================================================================
# 3. Market impact tests
# =========================================================================

class TestImpact:
    def test_market_impact_proportional_to_size(self, sim):
        """Larger orders should have more impact."""
        mkt = _StubMarketState(mid_price=100.0, volatility=0.02, adv=1e6)
        small = _StubOrder(quantity=100)
        large = _StubOrder(quantity=10_000)
        assert sim._calculate_impact(large, mkt) > sim._calculate_impact(small, mkt)

    def test_impact_zero_for_tiny_order(self, sim):
        """Very tiny order relative to ADV has negligible impact."""
        mkt = _StubMarketState(mid_price=100.0, adv=1e10)
        tiny = _StubOrder(quantity=1)
        impact = sim._calculate_impact(tiny, mkt)
        assert impact < 0.001  # sub-penny


# =========================================================================
# 4. Fill direction tests
# =========================================================================

class TestFillDirection:
    def test_buy_fills_above_mid(self, sim, ibkr_cost):
        """BUY market orders should fill above mid price."""
        mkt = _StubMarketState(mid_price=100.0)
        order = _StubOrder(side="BUY", quantity=100, asset_class="EQUITY_US")
        fill = sim.simulate_fill(order, mkt, ibkr_cost)
        assert not fill.rejected
        assert fill.fill_price > 100.0

    def test_sell_fills_below_mid(self, sim, ibkr_cost):
        """SELL market orders should fill below mid price."""
        mkt = _StubMarketState(mid_price=100.0)
        order = _StubOrder(side="SELL", quantity=100, asset_class="EQUITY_US")
        fill = sim.simulate_fill(order, mkt, ibkr_cost)
        assert not fill.rejected
        assert fill.fill_price < 100.0


# =========================================================================
# 5. Limit order tests
# =========================================================================

class TestLimitOrders:
    def test_limit_order_rejection(self, sim, ibkr_cost):
        """Limit BUY below market fill price should be rejected."""
        mkt = _StubMarketState(mid_price=100.0)
        order = _StubOrder(
            side="BUY", order_type="LIMIT", limit_price=99.0,
            quantity=100, asset_class="EQUITY_US",
        )
        fill = sim.simulate_fill(order, mkt, ibkr_cost)
        assert fill.rejected
        assert fill.reject_reason == "limit_not_reached"

    def test_limit_order_fill_at_limit_price(self, sim, ibkr_cost):
        """Limit BUY above market fill price should fill at limit price."""
        mkt = _StubMarketState(mid_price=100.0)
        order = _StubOrder(
            side="BUY", order_type="LIMIT", limit_price=105.0,
            quantity=100, asset_class="EQUITY_US",
        )
        fill = sim.simulate_fill(order, mkt, ibkr_cost)
        assert not fill.rejected
        assert fill.fill_price == 105.0


# =========================================================================
# 6. Market closed / margin rejection
# =========================================================================

class TestRejections:
    def test_market_closed_rejection(self, sim, ibkr_cost):
        """Orders should be rejected when market is closed."""
        mkt = _StubMarketState(is_open=False)
        order = _StubOrder()
        fill = sim.simulate_fill(order, mkt, ibkr_cost)
        assert fill.rejected
        assert fill.reject_reason == "market_closed"

    def test_margin_rejection(self, sim, ibkr_cost):
        """Orders exceeding 2x available cash should be rejected."""
        mkt = _StubMarketState(mid_price=100.0, available_cash=1_000.0)
        order = _StubOrder(quantity=100)  # notional = 10,000 > 2*1000
        fill = sim.simulate_fill(order, mkt, ibkr_cost)
        assert fill.rejected
        assert fill.reject_reason == "insufficient_margin"


# =========================================================================
# 7. Commission in fill
# =========================================================================

class TestFillCommission:
    def test_fill_has_commission(self, sim, ibkr_cost):
        """Filled orders should have positive commission."""
        mkt = _StubMarketState(mid_price=100.0)
        order = _StubOrder(quantity=100, asset_class="EQUITY_US")
        fill = sim.simulate_fill(order, mkt, ibkr_cost)
        assert not fill.rejected
        assert fill.commission > 0


# =========================================================================
# 8. IBKR cost model
# =========================================================================

class TestIBKRCosts:
    def test_ibkr_fx_commission(self, ibkr_cost):
        """FX: max($2, 0.2 bps * notional) + spread ~1 bps."""
        order = _StubOrder(asset_class="FX", quantity=10_000, symbol="EURUSD")
        # notional = 10,000 * 1.10 = 11,000
        # commission = max($2, 0.2bps*11000=$0.022) + spread(1bps*11000=$1.10) = $3.10
        comm = ibkr_cost.calculate_commission(order, fill_price=1.10)
        assert comm == 3.1  # flat minimum + spread cost

        # Large notional
        big = _StubOrder(asset_class="FX", quantity=1_000_000, symbol="EURUSD")
        comm_big = ibkr_cost.calculate_commission(big, fill_price=1.10)
        assert comm_big > 2.0  # 0.2 bps wins

    def test_ibkr_equity_commission(self, ibkr_cost):
        """US equity: $0.005/share, min $1, max 1%."""
        order = _StubOrder(asset_class="EQUITY_US", quantity=500)
        comm = ibkr_cost.calculate_commission(order, fill_price=150.0)
        expected = 500 * 0.005  # $2.50
        assert comm == expected

    def test_ibkr_equity_min_commission(self, ibkr_cost):
        """US equity: minimum $1 for small orders."""
        order = _StubOrder(asset_class="EQUITY_US", quantity=10)
        comm = ibkr_cost.calculate_commission(order, fill_price=150.0)
        assert comm == 1.0  # min applies (10 * 0.005 = $0.05 < $1)

    def test_ibkr_equity_max_commission(self, ibkr_cost):
        """US equity: max 1% of trade for penny stocks."""
        order = _StubOrder(asset_class="EQUITY_US", quantity=10_000)
        comm = ibkr_cost.calculate_commission(order, fill_price=0.10)
        # raw = 10,000 * 0.005 = $50, 1% cap = 10,000 * 0.10 * 0.01 = $10
        assert comm == 10.0

    def test_ibkr_eu_equity_commission(self, ibkr_cost):
        """EU equity: 0.05%, min EUR 3."""
        order = _StubOrder(asset_class="EQUITY_EU", quantity=100)
        comm = ibkr_cost.calculate_commission(order, fill_price=50.0)
        # 100 * 50 * 0.0005 = $2.50 < min $3
        assert comm == 3.0

    def test_ibkr_futures_commission(self, ibkr_cost):
        """Micro futures: $0.62/contract."""
        order = _StubOrder(asset_class="FUTURES_MICRO", quantity=5)
        comm = ibkr_cost.calculate_commission(order, fill_price=5000.0)
        assert comm == pytest.approx(5 * 0.62)


# =========================================================================
# 9. Binance cost model
# =========================================================================

class TestBinanceCosts:
    def test_binance_spot_commission(self, binance_cost):
        """Spot market order: 0.10% of notional."""
        order = _StubOrder(
            asset_class="CRYPTO_BTC", quantity=0.5,
            order_type="MARKET", broker="BINANCE",
        )
        comm = binance_cost.calculate_commission(order, fill_price=60_000.0)
        expected = 0.5 * 60_000.0 * 0.0010  # = $30
        assert comm == pytest.approx(expected)

    def test_binance_bnb_discount(self, binance_bnb):
        """BNB discount should reduce commission by 25%."""
        order = _StubOrder(
            asset_class="CRYPTO_BTC", quantity=0.5,
            order_type="MARKET", broker="BINANCE",
        )
        comm = binance_bnb.calculate_commission(order, fill_price=60_000.0)
        expected = 0.5 * 60_000.0 * 0.0010 * 0.75  # = $22.50
        assert comm == pytest.approx(expected)

    def test_binance_limit_uses_maker_rate(self, binance_cost):
        """Limit orders should use maker rate."""
        order = _StubOrder(
            asset_class="CRYPTO_ETH", quantity=10.0,
            order_type="LIMIT", broker="BINANCE",
        )
        comm = binance_cost.calculate_commission(order, fill_price=3_000.0)
        expected = 10.0 * 3_000.0 * 0.0010  # maker = 0.10%
        assert comm == pytest.approx(expected)


# =========================================================================
# 10. Funding / borrow model
# =========================================================================

class TestFundingModel:
    def test_borrow_interest_hourly(self):
        """Hourly interest should be daily_rate / 24 * notional."""
        model = FundingCostModel()
        pos = _StubPosition(symbol="BTCUSDT", quantity=1.0, avg_price=60_000.0)
        cost = model.apply_hourly_interest(pos, datetime(2025, 6, 1, 12, 0))
        daily_rate = 0.0002  # BTC default
        expected = 60_000.0 * (daily_rate / 24.0)
        assert cost == pytest.approx(expected)

    def test_eth_borrow_rate(self):
        """ETH rate should be higher than BTC."""
        model = FundingCostModel()
        assert model.get_rate("ETH") > model.get_rate("BTC")

    def test_sol_borrow_rate(self):
        """SOL rate should be higher than ETH (more volatile)."""
        model = FundingCostModel()
        assert model.get_rate("SOL") > model.get_rate("ETH")

    def test_unknown_asset_uses_default(self):
        """Unknown asset falls back to default rate."""
        model = FundingCostModel(default_rate=0.001)
        assert model.get_rate("OBSCURECOIN") == 0.001

    def test_rate_override(self):
        """Custom rate overrides take precedence."""
        model = FundingCostModel(rate_overrides={"BTC": 0.0005})
        assert model.get_rate("BTC") == 0.0005

    def test_extract_base_from_pair(self):
        """Symbol parsing should extract correct base asset."""
        assert FundingCostModel._extract_base("BTCUSDT") == "BTC"
        assert FundingCostModel._extract_base("ETH/USDT") == "ETH"
        assert FundingCostModel._extract_base("SOL-PERP") == "SOL"
        assert FundingCostModel._extract_base("AVAX") == "AVAX"
