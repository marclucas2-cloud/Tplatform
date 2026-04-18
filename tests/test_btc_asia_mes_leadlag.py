"""Tests btc_asia_mes_leadlag — paper-only T3-A2 validated strategy.

Couvre:
  - build_daily_dataset: alignement MES 15-21 UTC + BTC 0-8 UTC, shift(1)
  - compute_signal_for_date: quantile rolling strictement anterieur, anti-lookahead
  - compute_signal_for_date: BUY / SELL / NONE correct selon thresholds
  - simulate_paper_trade: PnL coherent long/short/none + cost
  - data_is_fresh: gating sur staleness MES ou BTC
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategies.crypto.btc_asia_mes_leadlag import (
    LeadlagSignal,
    PaperTrade,
    build_daily_dataset,
    compute_signal_for_date,
    data_is_fresh,
    simulate_paper_trade,
)


def _make_mes_hourly(n_days: int = 500, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic MES hourly OHLC for n_days UTC, covering each hour 0-23."""
    rng = np.random.RandomState(seed)
    start = pd.Timestamp("2024-01-01", tz="UTC")
    idx = pd.date_range(start=start, periods=n_days * 24, freq="1h", tz="UTC")
    base = 5000.0
    # Random walk with slight positive drift in US session hours
    closes = [base]
    for i in range(1, len(idx)):
        drift = 0.0003 if idx[i].hour in (15, 16, 17, 18, 19, 20, 21) else 0.0
        closes.append(closes[-1] * (1 + drift + rng.normal(0, 0.0015)))
    closes = np.array(closes)
    df = pd.DataFrame(
        {"open": closes, "high": closes * 1.002, "low": closes * 0.998, "close": closes, "volume": 100.0},
        index=idx,
    )
    df.index.name = "Datetime"
    return df


def _make_btc_hourly(n_days: int = 500, seed: int = 99) -> pd.DataFrame:
    """Generate synthetic BTC hourly OHLC with 'timestamp' UTC-aware col."""
    rng = np.random.RandomState(seed)
    start = pd.Timestamp("2024-01-01", tz="UTC")
    idx = pd.date_range(start=start, periods=n_days * 24, freq="1h", tz="UTC")
    base = 40000.0
    closes = [base]
    for _ in range(1, len(idx)):
        closes.append(closes[-1] * (1 + rng.normal(0, 0.002)))
    closes = np.array(closes)
    return pd.DataFrame({
        "timestamp": idx,
        "open": closes,
        "high": closes * 1.003,
        "low": closes * 0.997,
        "close": closes,
        "volume": 50.0,
        "quote_volume": closes * 50.0,
    })


class TestBuildDaily:

    def test_columns_present(self):
        mes = _make_mes_hourly(n_days=10)
        btc = _make_btc_hourly(n_days=10)
        daily = build_daily_dataset(mes, btc)
        assert set(daily.columns) == {
            "mes_sig", "mes_vol", "btc_asia_ret", "btc_entry_price", "btc_exit_price"
        }
        assert isinstance(daily.index, pd.DatetimeIndex)

    def test_shift_alignment(self):
        """mes_sig[D] dans daily == raw mes_sig du jour D-1 (shift(1) convention).

        Le test DOIT trouver un prev_date dans raw_sig, sinon la fixture est
        invalide. Pas de conditional skip silencieux (fix review N2).
        """
        mes = _make_mes_hourly(n_days=20)
        btc = _make_btc_hourly(n_days=20)
        daily = build_daily_dataset(mes, btc)
        # Re-compute raw mes_sig for each day (no shift) to check alignment
        mes_reset = mes.reset_index().rename(columns={mes.reset_index().columns[0]: "timestamp"})
        mes_reset["ret_bar"] = mes_reset["close"].pct_change()
        mes_reset["date"] = mes_reset["timestamp"].dt.floor("D").dt.tz_localize(None)
        raw_sig = (
            mes_reset[mes_reset["timestamp"].dt.hour.isin((15, 16, 17, 18, 19, 20, 21))]
            .groupby("date")["ret_bar"]
            .sum()
        )
        # Pick a daily date D such that D-1 has raw_sig (guaranteed by fixture size)
        # and assert alignment hard.
        for target in daily.index:
            prev_date = target - pd.Timedelta(days=1)
            if prev_date in raw_sig.index:
                assert pytest.approx(daily.loc[target, "mes_sig"], rel=1e-6) == raw_sig.loc[prev_date]
                return
        pytest.fail("No (D, D-1) pair found in fixture — data too short or alignment broken")


class TestComputeSignal:

    def _build_fixture(self, n_days=500):
        mes = _make_mes_hourly(n_days=n_days)
        btc = _make_btc_hourly(n_days=n_days)
        daily = build_daily_dataset(mes, btc)
        return daily

    def test_returns_none_if_date_not_in_daily(self):
        daily = self._build_fixture(n_days=50)
        future = pd.Timestamp("2099-01-01").normalize()
        assert compute_signal_for_date(daily, future) is None

    def test_returns_none_if_insufficient_history(self):
        """Rolling window=365 mais daily a 30 jours -> None."""
        daily = self._build_fixture(n_days=30)
        target = daily.index[-1]
        assert compute_signal_for_date(daily, target, rolling_window=365) is None

    def test_returns_signal_with_enough_history(self):
        """Avec 500 jours + rolling_window=100, doit retourner un LeadlagSignal."""
        daily = self._build_fixture(n_days=500)
        target = daily.index[-1]
        sig = compute_signal_for_date(daily, target, rolling_window=100)
        assert sig is not None
        assert isinstance(sig, LeadlagSignal)
        assert sig.side in ("BUY", "SELL", "NONE")
        assert sig.target_date == target
        assert sig.rolling_window_used == 100

    def test_quantile_is_rolling_not_future_leaking(self):
        """Le threshold doit utiliser UNIQUEMENT la window strictement avant target_date."""
        daily = self._build_fixture(n_days=500)
        target = daily.index[200]
        sig = compute_signal_for_date(daily, target, rolling_window=100)
        assert sig is not None
        # Compute expected threshold: quantile 0.70 of abs(mes_sig) in window [target-100d, target-1d]
        hist = daily.loc[daily.index < target].iloc[-100:]
        expected_pos_thr = float(hist["mes_sig"].abs().quantile(0.70))
        assert pytest.approx(sig.signal_thr, rel=1e-6) == expected_pos_thr

    def test_mode_long_only_suppresses_short(self):
        """mode=long_only ne devrait jamais renvoyer SELL."""
        daily = self._build_fixture(n_days=500)
        # Force a strong negative mes_sig that would normally trigger SELL in mode=both
        target = daily.index[200]
        daily_mod = daily.copy()
        daily_mod.loc[target, "mes_sig"] = -0.10  # very negative
        daily_mod.loc[target, "mes_vol"] = 0.001  # very low vol
        sig = compute_signal_for_date(daily_mod, target, rolling_window=100, mode="long_only")
        assert sig is not None
        assert sig.side in ("BUY", "NONE")

    def test_mode_short_only_suppresses_long(self):
        daily = self._build_fixture(n_days=500)
        target = daily.index[200]
        daily_mod = daily.copy()
        daily_mod.loc[target, "mes_sig"] = 0.10
        daily_mod.loc[target, "mes_vol"] = 0.001
        sig = compute_signal_for_date(daily_mod, target, rolling_window=100, mode="short_only")
        assert sig is not None
        assert sig.side in ("SELL", "NONE")

    def test_vol_filter_blocks(self):
        """Si mes_vol > vol_thr, side doit etre NONE meme avec mes_sig extreme."""
        daily = self._build_fixture(n_days=500)
        target = daily.index[200]
        daily_mod = daily.copy()
        # Force high vol to exceed any reasonable 0.80 quantile
        daily_mod.loc[target, "mes_vol"] = 10.0
        daily_mod.loc[target, "mes_sig"] = 10.0  # extreme positive
        sig = compute_signal_for_date(daily_mod, target, rolling_window=100, mode="both")
        assert sig is not None
        assert sig.side == "NONE"


class TestSimulatePaperTrade:

    def _fixture(self):
        mes = _make_mes_hourly(n_days=400)
        btc = _make_btc_hourly(n_days=400)
        return build_daily_dataset(mes, btc)

    def test_pnl_long_positive_on_up_session(self):
        daily = self._fixture()
        target = daily.index[200]
        # Pick a target where btc_asia_ret > 0
        positive_targets = daily.index[daily["btc_asia_ret"] > 0.01]
        if len(positive_targets) == 0:
            return
        target = positive_targets[-1]
        sig = LeadlagSignal(
            target_date=target, side="BUY",
            mes_sig=0.01, mes_vol=0.001,
            signal_thr=0.005, vol_thr=0.002, rolling_window_used=100,
        )
        trade = simulate_paper_trade(daily, sig, notional_usd=10_000.0, cost_rt_pct=0.0010)
        # Gross return should be positive, pnl = notional*(ret - cost)
        expected_ret = daily.loc[target, "btc_asia_ret"]
        assert pytest.approx(trade.gross_ret, rel=1e-6) == expected_ret
        assert trade.cost_pct == 0.0010
        assert pytest.approx(trade.pnl_usd, rel=1e-6) == 10_000.0 * (expected_ret - 0.0010)

    def test_pnl_short_is_negated(self):
        daily = self._fixture()
        positive_targets = daily.index[daily["btc_asia_ret"] > 0.01]
        if len(positive_targets) == 0:
            return
        target = positive_targets[-1]
        sig = LeadlagSignal(
            target_date=target, side="SELL",
            mes_sig=-0.01, mes_vol=0.001,
            signal_thr=0.005, vol_thr=0.002, rolling_window_used=100,
        )
        trade = simulate_paper_trade(daily, sig)
        # Short: gross_ret should be negative of btc_asia_ret
        expected_ret = -daily.loc[target, "btc_asia_ret"]
        assert pytest.approx(trade.gross_ret, rel=1e-6) == expected_ret

    def test_pnl_none_zero(self):
        daily = self._fixture()
        target = daily.index[200]
        sig = LeadlagSignal(
            target_date=target, side="NONE",
            mes_sig=0.001, mes_vol=0.001,
            signal_thr=0.005, vol_thr=0.002, rolling_window_used=100,
        )
        trade = simulate_paper_trade(daily, sig)
        assert trade.pnl_usd == 0.0
        assert trade.cost_pct == 0.0
        assert trade.gross_ret == 0.0


class TestDataFreshness:

    def test_fresh_returns_true(self):
        """Data recente -> True."""
        now = pd.Timestamp("2024-01-15", tz="UTC")
        mes = _make_mes_hourly(n_days=14)  # 2024-01-01 -> 2024-01-14
        btc = _make_btc_hourly(n_days=14)
        assert data_is_fresh(mes, btc, now_utc=now, max_age_days=3) is True

    def test_stale_mes_returns_false(self):
        now = pd.Timestamp("2024-02-15", tz="UTC")
        mes = _make_mes_hourly(n_days=14)  # stale (ends 2024-01-14)
        btc = _make_btc_hourly(n_days=45)  # fresh (ends 2024-02-14)
        assert data_is_fresh(mes, btc, now_utc=now, max_age_days=3) is False

    def test_stale_btc_returns_false(self):
        now = pd.Timestamp("2024-02-15", tz="UTC")
        mes = _make_mes_hourly(n_days=45)  # fresh
        btc = _make_btc_hourly(n_days=14)  # stale
        assert data_is_fresh(mes, btc, now_utc=now, max_age_days=3) is False
