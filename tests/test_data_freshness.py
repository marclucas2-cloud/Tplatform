"""Data freshness gate regression tests (Phase 11 XXL)."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

import core.governance.data_freshness as df
from core.governance.data_freshness import (
    check_data_freshness,
    is_data_freshness_gate_enabled,
)


@pytest.fixture
def isolated_root(tmp_path, monkeypatch):
    """Redirect data_freshness.ROOT to tmp."""
    monkeypatch.setattr(df, "ROOT", tmp_path)
    return tmp_path


def _touch(path: Path, age_hours: float = 0):
    """Create a file with a given age in hours."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    if age_hours > 0:
        old_ts = time.time() - age_hours * 3600
        os.utime(path, (old_ts, old_ts))


# ---------------------------------------------------------------------------
# check_data_freshness
# ---------------------------------------------------------------------------

class TestDataFreshness:
    def test_fresh_when_all_files_recent(self, isolated_root, monkeypatch):
        monkeypatch.setattr(df, "FRESHNESS_REQUIREMENTS", {
            "binance_crypto": [
                ("data/crypto/btc.parquet", 36),
                ("data/crypto/eth.parquet", 36),
            ],
        })
        _touch(isolated_root / "data" / "crypto" / "btc.parquet", age_hours=2)
        _touch(isolated_root / "data" / "crypto" / "eth.parquet", age_hours=5)

        fresh, details = check_data_freshness("binance_crypto")
        assert fresh is True
        assert details["data/crypto/btc.parquet"]["status"] == "fresh"
        assert details["data/crypto/eth.parquet"]["status"] == "fresh"

    def test_stale_when_one_file_too_old(self, isolated_root, monkeypatch):
        monkeypatch.setattr(df, "FRESHNESS_REQUIREMENTS", {
            "binance_crypto": [("data/crypto/btc.parquet", 36)],
        })
        _touch(isolated_root / "data" / "crypto" / "btc.parquet", age_hours=50)

        fresh, details = check_data_freshness("binance_crypto")
        assert fresh is False
        assert details["data/crypto/btc.parquet"]["status"] == "stale"
        assert details["data/crypto/btc.parquet"]["age_hours"] >= 49

    def test_missing_file_marks_book_stale(self, isolated_root, monkeypatch):
        monkeypatch.setattr(df, "FRESHNESS_REQUIREMENTS", {
            "binance_crypto": [("data/crypto/missing.parquet", 36)],
        })
        fresh, details = check_data_freshness("binance_crypto")
        assert fresh is False
        assert details["data/crypto/missing.parquet"]["status"] == "missing"

    def test_unknown_book_returns_fresh(self, isolated_root):
        fresh, details = check_data_freshness("nonexistent_book")
        assert fresh is True
        assert "no freshness requirements" in details["note"]

    def test_alpaca_us_no_static_files(self):
        """Alpaca utilise yfinance live, pas de parquet statique."""
        fresh, details = check_data_freshness("alpaca_us")
        assert fresh is True

    def test_age_hours_computed_correctly(self, isolated_root, monkeypatch):
        monkeypatch.setattr(df, "FRESHNESS_REQUIREMENTS", {
            "binance_crypto": [("data/crypto/btc.parquet", 100)],
        })
        _touch(isolated_root / "data" / "crypto" / "btc.parquet", age_hours=24)

        fresh, details = check_data_freshness("binance_crypto")
        assert fresh is True  # 24h < 100h
        # age_hours close to 24 (allow small slack)
        assert 23.5 <= details["data/crypto/btc.parquet"]["age_hours"] <= 24.5


# ---------------------------------------------------------------------------
# Gate enable flag
# ---------------------------------------------------------------------------

class TestGateEnableFlag:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("DATA_FRESHNESS_GATE", raising=False)
        assert is_data_freshness_gate_enabled() is False

    def test_enabled_when_env_set_true(self, monkeypatch):
        monkeypatch.setenv("DATA_FRESHNESS_GATE", "true")
        assert is_data_freshness_gate_enabled() is True

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("DATA_FRESHNESS_GATE", "TRUE")
        assert is_data_freshness_gate_enabled() is True
        monkeypatch.setenv("DATA_FRESHNESS_GATE", "True")
        assert is_data_freshness_gate_enabled() is True

    def test_disabled_for_non_true_values(self, monkeypatch):
        for v in ("false", "0", "no", "1", "yes", ""):
            monkeypatch.setenv("DATA_FRESHNESS_GATE", v)
            assert is_data_freshness_gate_enabled() is False, f"v={v}"


# ---------------------------------------------------------------------------
# Integration: per-book requirements have valid structure
# ---------------------------------------------------------------------------

class TestRequirementsStructure:
    def test_all_books_have_valid_structure(self):
        """FRESHNESS_REQUIREMENTS structure: {book: [(path, max_hours), ...]}"""
        for book, reqs in df.FRESHNESS_REQUIREMENTS.items():
            assert isinstance(reqs, list)
            for req in reqs:
                assert len(req) == 2
                path, max_hours = req
                assert isinstance(path, str) and path
                assert isinstance(max_hours, (int, float)) and max_hours > 0

    def test_max_hours_reasonable_per_book(self):
        """Sanity: weekly markets should have >= 48h tolerance."""
        for book, reqs in df.FRESHNESS_REQUIREMENTS.items():
            for path, max_hours in reqs:
                # Weekend tolerance
                if "ibkr" in book or "alpaca" in book:
                    assert max_hours >= 36, (
                        f"{book}: {path} max_hours={max_hours}, need >=36 for weekend"
                    )
