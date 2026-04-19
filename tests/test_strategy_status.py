"""Tests for strategy_status enum computation (D1 plan 9.0)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import core.governance.strategy_status as ss
import core.governance.quant_registry as qr
from core.governance.strategy_status import (
    StrategyStatus,
    compute_status,
)


@pytest.fixture
def isolated_registry(tmp_path, monkeypatch):
    registry = tmp_path / "quant_registry.yaml"
    monkeypatch.setattr(qr, "REGISTRY_PATH", registry)
    monkeypatch.setattr(qr, "ROOT", tmp_path)
    qr._load_registry_cached.cache_clear()
    return tmp_path


def _write(tmp: Path, strategies: list[dict], archived: list[str] | None = None):
    (tmp / "quant_registry.yaml").write_text(
        yaml.safe_dump({
            "metadata": {"version": 1},
            "strategies": strategies,
            "archived_rejected": archived or [],
        }),
        encoding="utf-8",
    )
    qr._load_registry_cached.cache_clear()


class TestSimpleCases:
    def test_unknown_strategy(self, isolated_registry):
        _write(isolated_registry, [])
        st = compute_status("nonexistent")
        assert st.status == StrategyStatus.UNKNOWN

    def test_archived_is_rejected(self, isolated_registry):
        _write(isolated_registry, [], archived=["btc_eth_dual_momentum"])
        st = compute_status("btc_eth_dual_momentum")
        assert st.status == StrategyStatus.REJECTED

    def test_disabled_strategy(self, isolated_registry):
        _write(isolated_registry, [{
            "strategy_id": "fx_carry", "book": "ibkr_fx", "status": "disabled",
            "paper_start_at": None, "live_start_at": None,
            "wf_manifest_path": None, "grade": None, "is_live": False,
            "infra_gaps": ["esma_limits"],
        }])
        st = compute_status("fx_carry")
        assert st.status == StrategyStatus.DISABLED

    def test_rejected_grade(self, isolated_registry):
        _write(isolated_registry, [{
            "strategy_id": "bad", "book": "x", "status": "paper_only",
            "paper_start_at": "2026-04-18",
            "wf_manifest_path": None, "grade": "REJECTED", "is_live": False,
        }])
        st = compute_status("bad")
        assert st.status == StrategyStatus.REJECTED


class TestActiveState:
    def test_live_strategy_is_active(self, isolated_registry):
        (isolated_registry / "data" / "research" / "wf_manifests").mkdir(parents=True)
        manifest = isolated_registry / "data" / "research" / "wf_manifests" / "cam.json"
        manifest.write_text("{}")
        _write(isolated_registry, [{
            "strategy_id": "cross_asset_momentum",
            "book": "ibkr_futures", "status": "live_core",
            "paper_start_at": None, "live_start_at": "2026-04-07",
            "wf_manifest_path": "data/research/wf_manifests/cam.json",
            "grade": "A", "is_live": True,
        }])
        st = compute_status("cross_asset_momentum")
        assert st.status == StrategyStatus.ACTIVE
        assert st.is_live is True


class TestReadyVsAuthorized:
    def test_paper_with_wf_artifact_and_gaps_is_ready(self, isolated_registry):
        (isolated_registry / "data" / "research" / "wf_manifests").mkdir(parents=True)
        manifest = isolated_registry / "data" / "research" / "wf_manifests" / "s.json"
        manifest.write_text("{}")
        _write(isolated_registry, [{
            "strategy_id": "s", "book": "ibkr_eu", "status": "paper_only",
            "paper_start_at": "2026-04-18",
            "wf_manifest_path": "data/research/wf_manifests/s.json",
            "grade": "S", "is_live": False,
            "infra_gaps": ["margin_eur_13500"],
        }])
        st = compute_status("s")
        assert st.status == StrategyStatus.READY
        assert "margin_eur_13500" in st.infra_gaps

    def test_paper_without_wf_artifact_is_authorized(self, isolated_registry):
        _write(isolated_registry, [{
            "strategy_id": "s", "book": "x", "status": "paper_only",
            "paper_start_at": "2026-04-18",
            "wf_manifest_path": None,
            "grade": None, "is_live": False,
        }])
        st = compute_status("s")
        assert st.status == StrategyStatus.AUTHORIZED
        assert "no wf_manifest_path" in st.reason or "no wf" in st.reason.lower()

    def test_paper_with_artifact_missing_file_is_authorized(self, isolated_registry):
        _write(isolated_registry, [{
            "strategy_id": "s", "book": "x", "status": "paper_only",
            "paper_start_at": "2026-04-18",
            "wf_manifest_path": "data/research/wf_manifests/nope.json",
            "grade": "B", "is_live": False,
        }])
        st = compute_status("s")
        assert st.status == StrategyStatus.AUTHORIZED


class TestSerialization:
    def test_to_dict_has_all_fields(self, isolated_registry):
        _write(isolated_registry, [{
            "strategy_id": "s", "book": "x", "status": "live_core",
            "paper_start_at": None, "live_start_at": "2026-04-07",
            "wf_manifest_path": None, "grade": "A", "is_live": True,
        }])
        d = compute_status("s").to_dict()
        for key in ["strategy_id", "status", "book", "grade", "is_live",
                    "infra_gaps", "promotable_blockers", "reason"]:
            assert key in d


class TestCanonicalRegistry:
    """Verify the shipped quant_registry gives coherent status for key strats."""

    def test_live_core_strats_are_active(self):
        qr._load_registry_cached.cache_clear()
        for sid in ("cross_asset_momentum", "gold_oil_rotation"):
            st = compute_status(sid)
            assert st.status == StrategyStatus.ACTIVE, \
                f"{sid} should be ACTIVE, got {st.status}: {st.reason}"

    def test_disabled_strats_are_disabled(self):
        qr._load_registry_cached.cache_clear()
        st = compute_status("btc_dominance_rotation_v2")
        # REJECTED car grade=REJECTED prend precedence sur status=disabled
        assert st.status in (StrategyStatus.DISABLED, StrategyStatus.REJECTED)

    def test_archived_bucket_a_is_rejected(self):
        qr._load_registry_cached.cache_clear()
        st = compute_status("btc_eth_dual_momentum")
        assert st.status == StrategyStatus.REJECTED
