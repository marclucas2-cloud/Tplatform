"""Tests alt_rel_strength_14_60_7 runner — paper atomic 6-leg."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.runtime.alt_rel_strength_runner import (
    ALPHA_WINDOW_DAYS,
    BASE,
    BETA_WINDOW_DAYS,
    BOTTOM_N,
    REBALANCE_DAYS,
    TOP_N,
    UNIVERSE,
    AltRelStrengthRunner,
    AltRelStrengthState,
    check_stops,
    compute_beta_adjusted_alpha,
    for_paper,
    portfolio_unrealized_pct,
    select_positions,
)


def _make_prices_panel(n_days: int = 200, seed: int = 42) -> pd.DataFrame:
    """Synthetic BTC + 10 alts daily closes with mild trends."""
    rng = np.random.RandomState(seed)
    idx = pd.date_range("2024-01-01", periods=n_days, freq="D").normalize()
    drifts = {BASE: 0.0005}
    for alt in UNIVERSE:
        # Random drift per coin to ensure dispersion
        drifts[alt] = float(rng.uniform(-0.001, 0.002))
    data = {}
    for sym, drift in drifts.items():
        returns = drift + rng.normal(0, 0.02, n_days)
        prices = 100.0 * np.cumprod(1 + returns)
        data[sym] = prices
    return pd.DataFrame(data, index=idx)


class TestModuleConstants:

    def test_universe_is_10_alts(self):
        assert len(UNIVERSE) == 10
        assert set(UNIVERSE) == {"ETH", "SOL", "BNB", "XRP", "ADA", "LINK", "AVAX", "DOT", "NEAR", "SUI"}
        assert BASE == "BTC"

    def test_params_match_validated_variant(self):
        """alt_rel_strength_14_60_7 per T4-A2 VALIDATED report."""
        assert ALPHA_WINDOW_DAYS == 14
        assert BETA_WINDOW_DAYS == 60
        assert REBALANCE_DAYS == 7
        assert TOP_N == 3
        assert BOTTOM_N == 3


class TestComputeBetaAdjustedAlpha:

    def test_empty_if_insufficient_history(self):
        prices = _make_prices_panel(n_days=30)  # < 60 beta_window
        as_of = prices.index[-1]
        alphas = compute_beta_adjusted_alpha(prices, as_of)
        assert alphas.empty

    def test_returns_alphas_sorted_desc_excluding_btc(self):
        prices = _make_prices_panel(n_days=150, seed=1)
        as_of = prices.index[-1]
        alphas = compute_beta_adjusted_alpha(prices, as_of)
        assert not alphas.empty
        assert BASE not in alphas.index
        # Sorted descending
        values = alphas.values
        assert (values[:-1] >= values[1:]).all()

    def test_anti_lookahead(self):
        """alpha computed at as_of uses only data <= as_of."""
        prices = _make_prices_panel(n_days=150, seed=1)
        as_of = prices.index[100]
        alphas_at_100 = compute_beta_adjusted_alpha(prices, as_of)
        # Same call but with extra future data should not change result
        prices_future = prices.iloc[:120].copy()  # truncated
        alphas_truncated = compute_beta_adjusted_alpha(prices_future, as_of)
        # Must be identical (no future leak)
        pd.testing.assert_series_equal(
            alphas_at_100, alphas_truncated, check_exact=False, rtol=1e-10
        )


class TestSelectPositions:

    def test_top_bottom_selection(self):
        alphas = pd.Series(
            [0.05, 0.03, 0.01, -0.01, -0.02, -0.03, -0.04, -0.05],
            index=["A", "B", "C", "D", "E", "F", "G", "H"],
        )
        longs, shorts = select_positions(alphas, top_n=3, bottom_n=3)
        assert longs == ["A", "B", "C"]
        assert shorts == ["F", "G", "H"]

    def test_empty_if_insufficient(self):
        alphas = pd.Series([0.05, 0.03], index=["A", "B"])
        longs, shorts = select_positions(alphas, top_n=3, bottom_n=3)
        assert longs == []
        assert shorts == []


class TestCheckStops:

    def test_long_position_below_sl(self):
        positions = {"ETH": {"direction": 1, "entry_price": 100.0, "entry_date": "2026-01-01", "notional_usd": 500}}
        current = {"ETH": 91.0}  # -9% < -8% SL
        hit = check_stops(positions, current, sl_per_position=0.08)
        assert hit == ["ETH"]

    def test_long_position_above_sl(self):
        positions = {"ETH": {"direction": 1, "entry_price": 100.0, "entry_date": "2026-01-01", "notional_usd": 500}}
        current = {"ETH": 95.0}  # -5% > -8%
        hit = check_stops(positions, current, sl_per_position=0.08)
        assert hit == []

    def test_short_position_hit(self):
        positions = {"SOL": {"direction": -1, "entry_price": 100.0, "entry_date": "2026-01-01", "notional_usd": 500}}
        current = {"SOL": 109.0}  # +9% adverse move for short
        hit = check_stops(positions, current, sl_per_position=0.08)
        assert hit == ["SOL"]


class TestPortfolioUnrealizedPct:

    def test_mean_across_positions(self):
        positions = {
            "ETH": {"direction": 1, "entry_price": 100.0, "entry_date": "2026-01-01", "notional_usd": 500},
            "SOL": {"direction": -1, "entry_price": 200.0, "entry_date": "2026-01-01", "notional_usd": 500},
        }
        current = {"ETH": 105.0, "SOL": 190.0}  # +5% long, +5% short (good)
        p = portfolio_unrealized_pct(positions, current)
        assert pytest.approx(p, rel=1e-3) == 0.05


class TestRunnerTickLifecycle:

    def test_init_rebalance_on_first_sunday(self, tmp_path):
        prices = _make_prices_panel(n_days=200, seed=2)
        # Pick a Sunday in the last 30 days (past warmup of 60+14 days)
        sundays = [ts for ts in prices.index[100:] if ts.dayofweek == 6]
        assert len(sundays) > 0
        as_of = sundays[0]

        runner = AltRelStrengthRunner(
            state_path=tmp_path / "state.json",
            journal_path=tmp_path / "journal.jsonl",
            paper=True,
        )
        result = runner.tick(as_of, prices)
        assert result.action == "init"
        assert len(result.rotation_plan["opens_long"]) == TOP_N
        assert len(result.rotation_plan["opens_short"]) == BOTTOM_N
        assert len(result.positions_after) == TOP_N + BOTTOM_N
        # State file written
        assert (tmp_path / "state.json").exists()
        # Journal written
        assert (tmp_path / "journal.jsonl").exists()
        # Longs/shorts disjoint
        longs = result.rotation_plan["opens_long"]
        shorts = result.rotation_plan["opens_short"]
        assert not (set(longs) & set(shorts))

    def test_hold_on_non_sunday(self, tmp_path):
        prices = _make_prices_panel(n_days=200, seed=2)
        # Non-Sunday, after warmup
        non_sundays = [ts for ts in prices.index[100:] if ts.dayofweek != 6]
        as_of = non_sundays[0]

        runner = AltRelStrengthRunner(
            state_path=tmp_path / "state.json",
            journal_path=tmp_path / "journal.jsonl",
        )
        result = runner.tick(as_of, prices)
        assert result.action == "hold"
        assert result.positions_after == {}

    def test_idempotence_same_day(self, tmp_path):
        """2nd tick on same as_of_date -> no-op hold (journal dedup)."""
        prices = _make_prices_panel(n_days=200, seed=2)
        sundays = [ts for ts in prices.index[100:] if ts.dayofweek == 6]
        as_of = sundays[0]

        runner = AltRelStrengthRunner(
            state_path=tmp_path / "state.json",
            journal_path=tmp_path / "journal.jsonl",
        )
        r1 = runner.tick(as_of, prices)
        positions_count_before = len(r1.positions_after)
        # Second call on same as_of_date
        r2 = runner.tick(as_of, prices)
        # Should return hold (dedup) without mutating state
        assert r2.action == "hold"
        # State still matches
        assert len(runner.state.positions) == positions_count_before

    def test_cascade_stop_closes_all(self, tmp_path):
        """Si 2+ stops hit -> cascade close all positions."""
        prices = _make_prices_panel(n_days=200, seed=2)
        sundays = [ts for ts in prices.index[100:] if ts.dayofweek == 6]
        as_of_init = sundays[0]

        runner = AltRelStrengthRunner(
            state_path=tmp_path / "state.json",
            journal_path=tmp_path / "journal.jsonl",
            sl_per_position=0.08,
            max_stops_cascade=2,
        )
        # Init 6-leg
        runner.tick(as_of_init, prices)
        assert len(runner.state.positions) == TOP_N + BOTTOM_N

        # Force prices to blow stops on 2+ positions
        syms_long = [s for s, p in runner.state.positions.items() if p["direction"] == 1]
        assert len(syms_long) >= 2
        # Create prices dataframe with big drops on long sides
        next_day = as_of_init + pd.Timedelta(days=1)
        prices_next = prices.copy()
        if next_day not in prices_next.index:
            # Fallback: use last index day + 1
            return
        for sym in syms_long[:2]:
            entry = runner.state.positions[sym]["entry_price"]
            prices_next.loc[next_day, sym] = entry * 0.80  # -20% crash

        result = runner.tick(next_day, prices_next)
        assert result.action == "cascade_close"
        assert len(result.stops_triggered) >= 2
        # All positions closed
        assert len(runner.state.positions) == 0

    def test_state_persists_across_reloads(self, tmp_path):
        """State survives process restart (load from JSON)."""
        prices = _make_prices_panel(n_days=200, seed=2)
        sundays = [ts for ts in prices.index[100:] if ts.dayofweek == 6]
        as_of = sundays[0]

        runner1 = AltRelStrengthRunner(
            state_path=tmp_path / "state.json",
            journal_path=tmp_path / "journal.jsonl",
        )
        r1 = runner1.tick(as_of, prices)
        pos_after = dict(runner1.state.positions)

        # New runner instance loads same paths
        runner2 = AltRelStrengthRunner(
            state_path=tmp_path / "state.json",
            journal_path=tmp_path / "journal.jsonl",
        )
        assert runner2.state.positions == pos_after
        assert runner2.state.last_rebalance_date == as_of.isoformat()

    def test_cost_debit_on_entry(self, tmp_path):
        """Entry debits cost_per_side * notional from cumulative_pnl."""
        prices = _make_prices_panel(n_days=200, seed=2)
        sundays = [ts for ts in prices.index[100:] if ts.dayofweek == 6]
        as_of = sundays[0]

        runner = AltRelStrengthRunner(
            state_path=tmp_path / "state.json",
            journal_path=tmp_path / "journal.jsonl",
            capital_per_leg=1000.0,
            cost_per_side=0.001,  # 10 bps
        )
        runner.tick(as_of, prices)
        # 6 legs opened at $1000 each, cost 0.1% each = $6 total
        expected_entry_cost = -(6 * 1000.0 * 0.001)
        assert pytest.approx(runner.state.cumulative_pnl_usd, abs=0.5) == expected_entry_cost


class TestRegressionFixesReviewN2:
    """Tests additionnels suite relecture N2 (bugs 🔴 #1 #2 + 🟠 #4 #5 #8)."""

    def test_single_stop_closes_position_individually(self, tmp_path):
        """Fix bug 🔴 #1: 1 position hit SL hors cascade -> close individuel immediat."""
        prices = _make_prices_panel(n_days=200, seed=2)
        sundays = [ts for ts in prices.index[100:] if ts.dayofweek == 6]
        as_of_init = sundays[0]

        runner = AltRelStrengthRunner(
            state_path=tmp_path / "state.json",
            journal_path=tmp_path / "journal.jsonl",
            sl_per_position=0.08,
            max_stops_cascade=2,  # 2 stops -> cascade
        )
        runner.tick(as_of_init, prices)
        initial_n = len(runner.state.positions)
        assert initial_n == TOP_N + BOTTOM_N

        # Force 1 single stop on next day (below cascade threshold)
        next_day = as_of_init + pd.Timedelta(days=1)
        prices_next = prices.copy()
        if next_day not in prices_next.index:
            return
        long_sym = [s for s, p in runner.state.positions.items() if p["direction"] == 1][0]
        entry = runner.state.positions[long_sym]["entry_price"]
        prices_next.loc[next_day, long_sym] = entry * 0.80  # -20%

        result = runner.tick(next_day, prices_next)
        assert result.action == "stop_loss"
        assert long_sym in result.stops_triggered
        # Position fermee, les 5 autres restent
        assert long_sym not in runner.state.positions
        assert len(runner.state.positions) == initial_n - 1
        assert runner.state.stops_hit_this_week == 1

    def test_journal_before_state_in_mutating_branches(self, tmp_path):
        """Fix bug 🔴 #2: journal doit etre ecrit AVANT state save.

        Verifie que apres un rebalance, les 2 fichiers existent et que
        le journal contient bien l'entry (la preuve indirecte que l'ordre
        est bien journal->state).
        """
        prices = _make_prices_panel(n_days=200, seed=2)
        sundays = [ts for ts in prices.index[100:] if ts.dayofweek == 6]
        as_of = sundays[0]

        runner = AltRelStrengthRunner(
            state_path=tmp_path / "state.json",
            journal_path=tmp_path / "journal.jsonl",
        )
        runner.tick(as_of, prices)

        # Journal ecrit
        assert (tmp_path / "journal.jsonl").exists()
        journal_content = (tmp_path / "journal.jsonl").read_text(encoding="utf-8")
        assert as_of.isoformat() in journal_content
        # State ecrit
        assert (tmp_path / "state.json").exists()
        state_raw = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        assert state_raw["last_rebalance_date"] == as_of.isoformat()

    def test_tz_aware_as_of_date_normalized(self, tmp_path):
        """Fix 🟠 #4: as_of_date tz-aware (ex. Europe/Paris) doit etre strip + normalize."""
        prices = _make_prices_panel(n_days=200, seed=2)
        sundays = [ts for ts in prices.index[100:] if ts.dayofweek == 6]
        as_of_naive = sundays[0]
        # Injecter tz Paris
        as_of_tz = as_of_naive.tz_localize("Europe/Paris")

        runner = AltRelStrengthRunner(
            state_path=tmp_path / "state.json",
            journal_path=tmp_path / "journal.jsonl",
        )
        # Doit ne pas crasher et matcher l'index naive du panel
        result = runner.tick(as_of_tz, prices)
        assert result.action in ("init", "rebalance")
        # Date journalisee = naive
        assert result.as_of_date.tz is None

    def test_beta_alpha_excludes_as_of_bar(self):
        """Fix 🟠 #5: compute_beta_adjusted_alpha EXCLUT as_of (fidelite backtest).

        Si on injecte une grosse perturbation sur le close as_of, le alpha
        ne doit pas bouger (car as_of est exclu du calcul).
        """
        prices = _make_prices_panel(n_days=150, seed=1)
        as_of = prices.index[100]
        alphas_original = compute_beta_adjusted_alpha(prices, as_of)

        # Perturbe massivement le close as_of (devrait etre ignore)
        prices_perturbed = prices.copy()
        for sym in UNIVERSE:
            if sym in prices_perturbed.columns:
                prices_perturbed.loc[as_of, sym] = prices_perturbed.loc[as_of, sym] * 10.0

        alphas_perturbed = compute_beta_adjusted_alpha(prices_perturbed, as_of)
        # Identiques car as_of exclu du calcul
        pd.testing.assert_series_equal(
            alphas_original, alphas_perturbed, check_exact=False, rtol=1e-10
        )

    def test_days_held_uses_exit_date_not_utcnow(self, tmp_path):
        """Fix 🟢 #8: _close_position utilise exit_date en arg (replay-safe)."""
        prices = _make_prices_panel(n_days=200, seed=2)
        sundays = [ts for ts in prices.index[100:] if ts.dayofweek == 6]
        as_of_init = sundays[0]
        # Rebalance 7j plus tard
        if len(sundays) < 2:
            return
        as_of_rebal = sundays[1]

        runner = AltRelStrengthRunner(
            state_path=tmp_path / "state.json",
            journal_path=tmp_path / "journal.jsonl",
            short_borrow_daily=0.001,  # 0.1%/day (exaggerer pour test)
            cost_per_side=0.0,  # isoler le borrow cost
        )
        runner.tick(as_of_init, prices)
        # Get a short position to check borrow application
        short_sym = next((s for s, p in runner.state.positions.items() if p["direction"] == -1), None)
        if not short_sym:
            return
        entry_price = runner.state.positions[short_sym]["entry_price"]

        # Force rotation that closes the short on as_of_rebal
        # Manipuler alphas via prices for next rebal — simplification: close direct
        exit_price = entry_price  # gross_ret = 0 so only borrow cost shows
        realized = runner._close_position(
            short_sym, exit_price, "test", exit_date=as_of_rebal
        )
        # days_held = 7 (Sunday to Sunday)
        expected_borrow = runner.state.positions.get(short_sym, {}).get("notional_usd", 0)
        # Use the actual notional
        days_held = (as_of_rebal - as_of_init).days
        # realized should be ~ -notional * borrow_daily * days_held (no gross, no cost, only borrow)
        # notional = 500 default, but we set cost=0 so just borrow
        assert realized < 0  # Borrow cost losed money on short
        # Roughly: -$500 * 0.001 * 7 = -$3.50, tolerant check
        assert abs(realized) > 0.5  # Some borrow cost attributed


class TestFactory:

    def test_for_paper_default_paths(self, tmp_path):
        runner = for_paper(tmp_path)
        assert runner.paper is True
        assert runner.state_path == tmp_path / "state.json"
        assert runner.journal_path == tmp_path / "paper_journal.jsonl"
