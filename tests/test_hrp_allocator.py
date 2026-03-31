"""Tests for HRP Allocator — clustering, weight computation, constraints."""
import numpy as np
import pandas as pd
import pytest

from core.alloc.hrp_allocator import HRPAllocator


@pytest.fixture
def hrp():
    return HRPAllocator(min_weight=0.05, max_weight=0.30)


def make_pnl(n_strats=5, n_days=60, seed=42):
    """Generate synthetic strategy PnLs with 2 clusters."""
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2025-10-01", periods=n_days)
    # Cluster 1: correlated
    f1 = rng.normal(0.001, 0.01, n_days)
    # Cluster 2: uncorrelated
    pnl = {}
    for i in range(3):
        pnl[f"strat_A{i}"] = pd.Series(f1 + rng.normal(0, 0.003, n_days), index=dates)
    for i in range(2):
        pnl[f"strat_B{i}"] = pd.Series(rng.normal(0.0005, 0.008, n_days), index=dates)
    return pnl


class TestPnLMatrix:
    def test_build_matrix(self, hrp):
        pnl = make_pnl()
        matrix = hrp.build_pnl_matrix(pnl, lookback_days=20)
        assert isinstance(matrix, pd.DataFrame)
        assert matrix.shape[1] == 5

    def test_empty_pnl(self, hrp):
        matrix = hrp.build_pnl_matrix({}, lookback_days=20)
        assert matrix.empty


class TestClustering:
    def test_cluster_strategies(self, hrp):
        pnl = make_pnl()
        matrix = hrp.build_pnl_matrix(pnl, lookback_days=40)
        corr = matrix.corr()
        result = hrp.cluster_strategies(corr)
        assert "clusters" in result
        assert result["n_clusters"] >= 1

    def test_two_clusters_detected(self, hrp):
        pnl = make_pnl()
        matrix = hrp.build_pnl_matrix(pnl, lookback_days=40)
        corr = matrix.corr()
        result = hrp.cluster_strategies(corr)
        # Should detect 2 groups (A and B)
        assert result["n_clusters"] >= 2


class TestWeightComputation:
    def test_weights_sum_to_one(self, hrp):
        pnl = make_pnl()
        weights = hrp.compute_weights(pnl)
        assert abs(sum(weights.values()) - 1.0) < 0.01

    def test_all_strategies_have_weight(self, hrp):
        pnl = make_pnl()
        weights = hrp.compute_weights(pnl)
        assert len(weights) == 5
        for w in weights.values():
            assert w > 0

    def test_constraints_respected(self, hrp):
        pnl = make_pnl()
        weights = hrp.compute_weights(pnl)
        for w in weights.values():
            assert w >= 0.05 - 0.001  # min_weight
            assert w <= 0.30 + 0.001  # max_weight


class TestRebalanceThreshold:
    def test_no_rebalance_if_stable(self, hrp):
        w1 = {"A": 0.20, "B": 0.30, "C": 0.50}
        w2 = {"A": 0.21, "B": 0.29, "C": 0.50}
        assert not hrp.should_rebalance(w1, w2, threshold=0.05)

    def test_rebalance_if_changed(self, hrp):
        w1 = {"A": 0.20, "B": 0.30, "C": 0.50}
        w2 = {"A": 0.35, "B": 0.15, "C": 0.50}
        assert hrp.should_rebalance(w1, w2, threshold=0.05)


class TestTurnoverCost:
    def test_zero_turnover(self, hrp):
        w = {"A": 0.5, "B": 0.5}
        cost = hrp.get_turnover_cost(w, w, 100_000, cost_bps=5.0)
        assert cost == 0.0

    def test_full_turnover(self, hrp):
        w1 = {"A": 1.0, "B": 0.0}
        w2 = {"A": 0.0, "B": 1.0}
        cost = hrp.get_turnover_cost(w1, w2, 100_000, cost_bps=5.0)
        assert cost > 0


class TestHRPVsEqualWeight:
    def test_hrp_reduces_concentration(self, hrp):
        pnl = make_pnl()
        weights = hrp.compute_weights(pnl)
        max_w = max(weights.values())
        # HRP should not give > 50% to any single strategy
        assert max_w < 0.50
