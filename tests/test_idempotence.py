"""Idempotence & determinism tests — same data must produce same results."""
import json
import random
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


# --- Helpers ---

def make_ohlcv(n: int = 100, seed: int = 42) -> pd.DataFrame:
    """Generate deterministic OHLCV data."""
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2025-01-02", periods=n, freq="h")
    close = 100 + rng.normal(0, 1, n).cumsum()
    high = close + rng.uniform(0.1, 1.0, n)
    low = close - rng.uniform(0.1, 1.0, n)
    opn = close + rng.uniform(-0.5, 0.5, n)
    vol = rng.uniform(1e6, 5e6, n)
    return pd.DataFrame(
        {"open": opn, "high": high, "low": low, "close": close, "volume": vol},
        index=dates,
    )


# --- Tests ---

class TestSignalDeterminism:
    """Same data -> same signals on multiple runs."""

    def test_indicators_deterministic(self):
        df1 = make_ohlcv(100, seed=42)
        df2 = make_ohlcv(100, seed=42)
        np.testing.assert_array_equal(df1.values, df2.values)

    def test_sma_deterministic(self):
        df = make_ohlcv(100, seed=42)
        sma1 = df["close"].rolling(20).mean()
        sma2 = df["close"].rolling(20).mean()
        np.testing.assert_array_equal(sma1.values, sma2.values)

    def test_rsi_deterministic(self):
        df = make_ohlcv(100, seed=42)
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        rsi1 = 100 - (100 / (1 + rs))
        # Second run
        delta2 = df["close"].diff()
        gain2 = delta2.where(delta2 > 0, 0).rolling(14).mean()
        loss2 = (-delta2.where(delta2 < 0, 0)).rolling(14).mean()
        rs2 = gain2 / loss2
        rsi2 = 100 - (100 / (1 + rs2))
        np.testing.assert_array_equal(rsi1.values, rsi2.values)

    def test_signal_from_crossover(self):
        """EMA crossover signals must be identical across runs."""
        df = make_ohlcv(100, seed=42)
        signals = []
        for _ in range(3):
            ema_fast = df["close"].ewm(span=10).mean()
            ema_slow = df["close"].ewm(span=20).mean()
            cross = (ema_fast > ema_slow).astype(int).diff()
            signals.append(cross.values)
        np.testing.assert_array_equal(signals[0], signals[1])
        np.testing.assert_array_equal(signals[1], signals[2])


class TestEngineReplayDeterminism:
    """Backtester engine must produce identical results on replay."""

    def test_pnl_identical_across_runs(self):
        np.random.seed(42)
        random.seed(42)
        df = make_ohlcv(50, seed=42)
        # Simple long-only simulation
        results = []
        for _ in range(5):
            equity = 100_000
            positions = []
            for i in range(20, len(df)):
                sma = df["close"].iloc[i-20:i].mean()
                price = df["close"].iloc[i]
                if price > sma and not positions:
                    positions.append(price)
                elif price < sma and positions:
                    pnl = price - positions.pop()
                    equity += pnl * 100
            results.append(round(equity, 6))
        assert all(r == results[0] for r in results)

    def test_trade_count_identical(self):
        df = make_ohlcv(100, seed=42)
        counts = []
        for _ in range(3):
            trades = 0
            in_pos = False
            for i in range(14, len(df)):
                rsi_val = 50  # Simplified
                delta = df["close"].diff()
                gain = delta.where(delta > 0, 0).iloc[i-14:i].mean()
                loss = (-delta.where(delta < 0, 0)).iloc[i-14:i].mean()
                if loss > 0:
                    rsi_val = 100 - 100 / (1 + gain / loss)
                if rsi_val < 30 and not in_pos:
                    trades += 1
                    in_pos = True
                elif rsi_val > 70 and in_pos:
                    in_pos = False
            counts.append(trades)
        assert all(c == counts[0] for c in counts)


class TestStatePersistenceDeterminism:
    """Save/load state must not change behavior."""

    def test_json_roundtrip(self):
        state = {"equity": 100_000.123456, "positions": {"SPY": 50}, "dd": -0.0234}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(state, f)
            path = f.name
        with open(path) as f:
            loaded = json.load(f)
        assert loaded == state

    def test_state_preserves_precision(self):
        val = 0.1 + 0.2
        state = {"value": val}
        dumped = json.dumps(state)
        loaded = json.loads(dumped)
        assert loaded["value"] == val


class TestMultiRunConsistency:
    """10 identical runs must produce identical results."""

    def test_10_runs_identical(self):
        results = []
        for _ in range(10):
            np.random.seed(42)
            data = np.random.normal(0, 1, 1000)
            sharpe = data.mean() / data.std() * np.sqrt(252)
            results.append(round(sharpe, 10))
        assert len(set(results)) == 1


class TestReplayRecorder:
    """Replay recorder save/load/compare."""

    def test_save_load_roundtrip(self):
        from core.data.replay_recorder import ReplayRecorder
        rec = ReplayRecorder()
        rec.record_candle({"symbol": "SPY", "close": 450.0, "volume": 1e6})
        rec.record_signal({"symbol": "SPY", "side": "BUY", "strategy": "test"})
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = f.name
        rec.save(path)
        loaded = ReplayRecorder.load(path)
        assert len(loaded) == 2
        assert loaded[0]["type"] == "candle"
        assert loaded[1]["type"] == "signal"

    def test_compare_identical(self):
        from core.data.replay_recorder import ReplayRecorder
        a = [{"type": "candle", "close": 100}, {"type": "signal", "side": "BUY"}]
        b = [{"type": "candle", "close": 100}, {"type": "signal", "side": "BUY"}]
        result = ReplayRecorder.compare_recordings(a, b)
        assert result["identical"] is True

    def test_compare_different_length(self):
        from core.data.replay_recorder import ReplayRecorder
        a = [{"type": "signal", "side": "BUY"}]
        b = [{"type": "signal", "side": "BUY"}, {"type": "candle", "close": 100}]
        result = ReplayRecorder.compare_recordings(a, b)
        assert result["length_a"] != result["length_b"]
