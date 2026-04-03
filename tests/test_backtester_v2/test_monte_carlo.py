"""Tests for MonteCarloEngine.

Validates simulation distributions, reproducibility, edge cases,
and probability bounds.
"""

from __future__ import annotations


from core.backtester_v2.monte_carlo import MCResult, MonteCarloEngine

# ─── Helpers ─────────────────────────────────────────────────────────


def _winning_trades(n: int = 50) -> list[dict]:
    """Generate a trade log where every trade is a winner."""
    return [{"pnl": 50.0 + i * 2} for i in range(n)]


def _losing_trades(n: int = 50) -> list[dict]:
    """Generate a trade log where every trade is a loser."""
    return [{"pnl": -50.0 - i * 2} for i in range(n)]


def _mixed_trades(n: int = 100) -> list[dict]:
    """Generate a mixed trade log (60% winners, 40% losers)."""
    trades = []
    for i in range(n):
        if i % 5 < 3:
            trades.append({"pnl": 30.0 + (i % 10)})
        else:
            trades.append({"pnl": -20.0 - (i % 10)})
    return trades


# ─── Tests ───────────────────────────────────────────────────────────


class TestMonteCarlo:
    """Core Monte Carlo simulation tests."""

    def test_run_10k_simulations(self) -> None:
        """Should run 10k sims and return correct n_simulations."""
        engine = MonteCarloEngine()
        result = engine.run(_mixed_trades(), n_simulations=10_000, seed=42)

        assert isinstance(result, MCResult)
        assert result.n_simulations == 10_000
        assert len(result.distributions["sharpes"]) == 10_000
        assert len(result.distributions["max_dds"]) == 10_000
        assert len(result.distributions["final_equities"]) == 10_000

    def test_reproducible(self) -> None:
        """Same seed should produce identical results."""
        engine = MonteCarloEngine()
        r1 = engine.run(_mixed_trades(), n_simulations=1000, seed=42)
        r2 = engine.run(_mixed_trades(), n_simulations=1000, seed=42)

        assert r1.median_sharpe == r2.median_sharpe
        assert r1.p5_sharpe == r2.p5_sharpe
        assert r1.p95_sharpe == r2.p95_sharpe
        assert r1.median_max_dd == r2.median_max_dd
        assert r1.prob_profitable == r2.prob_profitable
        assert r1.prob_ruin == r2.prob_ruin

    def test_different_seed_different_result(self) -> None:
        """Different seeds should produce different distributions."""
        engine = MonteCarloEngine()
        r1 = engine.run(_mixed_trades(), n_simulations=1000, seed=42)
        r2 = engine.run(_mixed_trades(), n_simulations=1000, seed=99)

        # Medians might be close but distributions should differ
        assert r1.distributions["sharpes"] != r2.distributions["sharpes"]

    def test_prob_profitable_all_winners(self) -> None:
        """All winning trades should yield prob_profitable ~ 1.0."""
        engine = MonteCarloEngine()
        result = engine.run(_winning_trades(50), n_simulations=5000, seed=42)

        assert result.prob_profitable >= 0.99

    def test_prob_profitable_all_losers(self) -> None:
        """All losing trades should yield prob_profitable ~ 0.0."""
        engine = MonteCarloEngine()
        result = engine.run(_losing_trades(50), n_simulations=5000, seed=42)

        assert result.prob_profitable <= 0.01

    def test_p5_less_than_median_less_than_p95(self) -> None:
        """Percentiles should be ordered: p5 <= median <= p95."""
        engine = MonteCarloEngine()
        result = engine.run(_mixed_trades(), n_simulations=5000, seed=42)

        assert result.p5_sharpe <= result.median_sharpe
        assert result.median_sharpe <= result.p95_sharpe

    def test_max_dd_always_positive(self) -> None:
        """Max drawdown should always be >= 0."""
        engine = MonteCarloEngine()
        result = engine.run(_mixed_trades(), n_simulations=1000, seed=42)

        assert result.median_max_dd >= 0.0
        assert result.p95_max_dd >= 0.0
        for dd in result.distributions["max_dds"]:
            assert dd >= 0.0

    def test_prob_ruin_bounded(self) -> None:
        """prob_ruin should be between 0.0 and 1.0."""
        engine = MonteCarloEngine()
        result = engine.run(_mixed_trades(), n_simulations=1000, seed=42)

        assert 0.0 <= result.prob_ruin <= 1.0

    def test_empty_trade_log(self) -> None:
        """Empty trade log should return zeros without error."""
        engine = MonteCarloEngine()
        result = engine.run([], n_simulations=100, seed=42)

        assert result.median_sharpe == 0.0
        assert result.p5_sharpe == 0.0
        assert result.p95_sharpe == 0.0
        assert result.prob_profitable == 0.0
        assert result.prob_ruin == 0.0
        assert result.n_simulations == 100
        assert result.distributions["sharpes"] == []

    def test_single_trade(self) -> None:
        """Single trade should run without error."""
        engine = MonteCarloEngine()
        result = engine.run([{"pnl": 100.0}], n_simulations=500, seed=42)

        assert result.n_simulations == 500
        # Single trade: all permutations are identical
        assert result.prob_profitable == 1.0
        # Sharpe = 0 (std = 0 with single trade)
        assert result.median_sharpe == 0.0

    def test_prob_ruin_all_losers(self) -> None:
        """Heavy losses should have high ruin probability."""
        # Losses that total more than 50% of capital
        heavy_losses = [{"pnl": -200.0} for _ in range(50)]
        engine = MonteCarloEngine()
        result = engine.run(
            heavy_losses, n_simulations=1000,
            initial_capital=10_000, seed=42,
        )

        # Total loss = -10000, initial = 10000 => final ~ 0 => ruin
        assert result.prob_ruin >= 0.99

    def test_distributions_keys(self) -> None:
        """Result distributions should have expected keys."""
        engine = MonteCarloEngine()
        result = engine.run(_mixed_trades(20), n_simulations=100, seed=42)

        assert "sharpes" in result.distributions
        assert "max_dds" in result.distributions
        assert "final_equities" in result.distributions
