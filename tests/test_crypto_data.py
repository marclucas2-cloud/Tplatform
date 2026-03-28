"""Tests for CryptoDataPipeline V2 (margin + earn) — 16 tests."""
import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timezone

from core.crypto.data_pipeline import CryptoDataPipeline


@pytest.fixture
def pipeline():
    return CryptoDataPipeline()


@pytest.fixture
def sample_candles():
    dates = pd.date_range("2024-01-01", periods=500, freq="1h", tz=timezone.utc)
    np.random.seed(42)
    close = 40000 + np.cumsum(np.random.randn(500) * 100)
    return pd.DataFrame({
        "timestamp": dates,
        "open": close - np.random.rand(500) * 50,
        "high": close + np.abs(np.random.randn(500)) * 100,
        "low": close - np.abs(np.random.randn(500)) * 100,
        "close": close,
        "volume": np.random.rand(500) * 1e6 + 1e5,
    })


class TestUniverse:
    def test_all_symbols_non_empty(self, pipeline):
        assert len(pipeline.all_symbols) > 0

    def test_btc_eth_in_tier1(self, pipeline):
        assert "BTCUSDT" in pipeline.UNIVERSE["tier_1"]
        assert "ETHUSDT" in pipeline.UNIVERSE["tier_1"]

    def test_get_tier(self, pipeline):
        assert pipeline.get_tier("BTCUSDT") == "tier_1"
        assert pipeline.get_tier("SOLUSDT") == "tier_2"
        assert pipeline.get_tier("UNKNOWN") == "unknown"

    def test_doge_in_tier2(self, pipeline):
        """V2: DOGE added to tier 2 for momentum plays."""
        assert "DOGEUSDT" in pipeline.UNIVERSE["tier_2"]


class TestCleaning:
    def test_clean_removes_zero_volume(self, pipeline):
        df = pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=5, freq="1h", tz=timezone.utc),
            "open": [100, 101, 102, 103, 104],
            "high": [105, 106, 107, 108, 109],
            "low": [95, 96, 97, 98, 99],
            "close": [102, 103, 104, 105, 106],
            "volume": [1000, 0, 2000, 0, 3000],
        })
        cleaned = pipeline.clean_candles(df)
        assert (cleaned["volume"] > 0).all()

    def test_clean_flags_flash_crash(self, pipeline, sample_candles):
        df = sample_candles.copy()
        df.loc[50, "high"] = df.loc[50, "close"] * 1.15
        df.loc[50, "low"] = df.loc[50, "close"] * 0.85
        cleaned = pipeline.clean_candles(df)
        assert "flash_crash_flag" in cleaned.columns
        assert cleaned["flash_crash_flag"].any()

    def test_clean_empty(self, pipeline):
        assert pipeline.clean_candles(pd.DataFrame()).empty

    def test_clean_validates_ohlc(self, pipeline):
        df = pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=5, freq="1h", tz=timezone.utc),
            "open": [100, 101, 102, 103, 104],
            "high": [90, 106, 107, 108, 109],
            "low": [95, 96, 97, 98, 99],
            "close": [102, 103, 104, 105, 106],
            "volume": [1000, 2000, 3000, 4000, 5000],
        })
        cleaned = pipeline.clean_candles(df)
        assert len(cleaned) < 5


class TestFeatures:
    def test_compute_features(self, pipeline, sample_candles, tmp_path):
        import core.crypto.data_pipeline as dp
        original = dp.DATA_DIR
        dp.DATA_DIR = tmp_path / "data" / "crypto"
        dp.DATA_DIR.mkdir(parents=True, exist_ok=True)
        (dp.DATA_DIR / "candles").mkdir()
        pipeline.save_candles("BTCUSDT", "1h", sample_candles)
        features = pipeline.compute_features("BTCUSDT", "1h")
        dp.DATA_DIR = original
        assert not features.empty
        for col in ("ema_20", "ema_50", "ema_200", "rsi_14", "adx_14", "atr_14", "bb_mid", "bb_lower", "bb_upper"):
            assert col in features.columns

    def test_features_empty(self, pipeline):
        assert pipeline.compute_features("NONEXISTENT").empty

    def test_adx_range(self, pipeline, sample_candles):
        adx = CryptoDataPipeline._compute_adx(sample_candles, 14)
        valid = adx.dropna()
        assert len(valid) > 0
        assert (valid >= 0).all()


class TestStorage:
    def test_save_load_candles(self, pipeline, sample_candles, tmp_path):
        import core.crypto.data_pipeline as dp
        original = dp.DATA_DIR
        dp.DATA_DIR = tmp_path / "data" / "crypto"
        dp.DATA_DIR.mkdir(parents=True, exist_ok=True)
        (dp.DATA_DIR / "candles").mkdir()
        pipeline.save_candles("BTCUSDT", "1h", sample_candles)
        loaded = pipeline.load_candles("BTCUSDT", "1h")
        dp.DATA_DIR = original
        assert len(loaded) == len(sample_candles)

    def test_load_nonexistent(self, pipeline):
        assert pipeline.load_candles("NONE", "99h").empty

    def test_borrow_rates_default(self, pipeline):
        """Load borrow rates from nonexistent DB returns empty."""
        df = pipeline.load_borrow_rates("BTC")
        assert df.empty
