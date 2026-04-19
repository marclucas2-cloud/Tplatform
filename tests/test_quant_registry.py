"""Tests for core.governance.quant_registry.

B2 plan 9.0 (2026-04-19): canonical registry replaces regex on live_whitelist
notes text. Tests verify: schema parse, paper_start_at typing, wf_manifest_path
resolution, is_live flag, archived_rejected lookup.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import yaml

import core.governance.quant_registry as qr
from core.governance.quant_registry import (
    QuantEntry,
    archived_rejected_ids,
    get_entry,
    load_registry,
)


@pytest.fixture
def isolated_registry(tmp_path, monkeypatch):
    """Redirect REGISTRY_PATH + ROOT to tmp_path for isolation."""
    registry = tmp_path / "quant_registry.yaml"
    monkeypatch.setattr(qr, "REGISTRY_PATH", registry)
    monkeypatch.setattr(qr, "ROOT", tmp_path)
    qr._load_registry_cached.cache_clear()
    return tmp_path


def _write_registry(tmp: Path, strategies: list[dict], archived: list[str] | None = None):
    registry = tmp / "quant_registry.yaml"
    payload = {
        "metadata": {"version": 1},
        "strategies": strategies,
        "archived_rejected": archived or [],
    }
    registry.write_text(yaml.safe_dump(payload), encoding="utf-8")
    qr._load_registry_cached.cache_clear()


class TestRegistryLoad:
    def test_load_empty_returns_empty_dict(self, isolated_registry):
        _write_registry(isolated_registry, [])
        assert load_registry() == {}

    def test_load_parses_single_strategy(self, isolated_registry):
        _write_registry(isolated_registry, [{
            "strategy_id": "test_strat",
            "book": "ibkr_futures",
            "status": "paper_only",
            "paper_start_at": "2026-04-18",
            "live_start_at": None,
            "wf_manifest_path": None,
            "grade": "B",
            "last_wf_run_at": "2026-04-19",
            "is_live": False,
            "infra_gaps": [],
        }])
        registry = load_registry()
        assert "test_strat" in registry
        e = registry["test_strat"]
        assert isinstance(e, QuantEntry)
        assert e.book == "ibkr_futures"
        assert e.paper_start_at == date(2026, 4, 18)
        assert e.live_start_at is None
        assert e.grade == "B"
        assert e.is_live is False

    def test_paper_start_at_null_stays_none(self, isolated_registry):
        _write_registry(isolated_registry, [{
            "strategy_id": "live_strat",
            "book": "ibkr_futures",
            "status": "live_core",
            "paper_start_at": None,
            "live_start_at": "2026-04-07",
            "wf_manifest_path": None,
            "grade": "A",
            "is_live": True,
        }])
        e = get_entry("live_strat")
        assert e.paper_start_at is None
        assert e.live_start_at == date(2026, 4, 7)


class TestAgePaperDays:
    def test_age_paper_days_computed(self, isolated_registry):
        _write_registry(isolated_registry, [{
            "strategy_id": "s", "book": "x", "status": "paper_only",
            "paper_start_at": "2026-04-05",
            "wf_manifest_path": None, "grade": "B", "is_live": False,
        }])
        e = get_entry("s")
        age = e.age_paper_days(now=datetime(2026, 4, 19, tzinfo=UTC))
        assert age == 14

    def test_age_paper_days_none_when_no_start(self, isolated_registry):
        _write_registry(isolated_registry, [{
            "strategy_id": "s", "book": "x", "status": "live_core",
            "paper_start_at": None,
            "wf_manifest_path": None, "grade": "A", "is_live": True,
        }])
        e = get_entry("s")
        assert e.age_paper_days() is None


class TestWfManifestPath:
    def test_has_wf_artifact_true_when_file_exists(self, isolated_registry):
        (isolated_registry / "data" / "research" / "wf_manifests").mkdir(parents=True)
        manifest = isolated_registry / "data" / "research" / "wf_manifests" / "s.json"
        manifest.write_text("{}")
        _write_registry(isolated_registry, [{
            "strategy_id": "s", "book": "x", "status": "paper_only",
            "paper_start_at": "2026-04-18",
            "wf_manifest_path": "data/research/wf_manifests/s.json",
            "grade": "B", "is_live": False,
        }])
        assert get_entry("s").has_wf_artifact() is True

    def test_has_wf_artifact_false_when_path_null(self, isolated_registry):
        _write_registry(isolated_registry, [{
            "strategy_id": "s", "book": "x", "status": "paper_only",
            "paper_start_at": "2026-04-18",
            "wf_manifest_path": None,
            "grade": None, "is_live": False,
        }])
        assert get_entry("s").has_wf_artifact() is False

    def test_has_wf_artifact_false_when_file_missing(self, isolated_registry):
        _write_registry(isolated_registry, [{
            "strategy_id": "s", "book": "x", "status": "paper_only",
            "paper_start_at": "2026-04-18",
            "wf_manifest_path": "data/research/wf_manifests/nope.json",
            "grade": "B", "is_live": False,
        }])
        assert get_entry("s").has_wf_artifact() is False


class TestArchivedRejected:
    def test_archived_ids_loaded(self, isolated_registry):
        _write_registry(
            isolated_registry,
            [],
            archived=["btc_eth_dual_momentum", "vol_breakout", "eu_gap_open"],
        )
        ids = archived_rejected_ids()
        assert "btc_eth_dual_momentum" in ids
        assert "vol_breakout" in ids
        assert "eu_gap_open" in ids

    def test_archived_empty_when_no_key(self, isolated_registry):
        _write_registry(isolated_registry, [{
            "strategy_id": "s", "book": "x", "status": "paper_only",
            "paper_start_at": "2026-04-18",
            "wf_manifest_path": None, "grade": "B", "is_live": False,
        }])
        assert archived_rejected_ids() == set()


class TestCanonicalRegistryConsistency:
    """Verify shipped config/quant_registry.yaml is parseable + has expected strats."""

    def test_canonical_registry_loads_without_error(self):
        qr._load_registry_cached.cache_clear()
        reg = load_registry()
        # Live_core must be present
        assert "cross_asset_momentum" in reg, "CAM live_core must exist in registry"
        assert "gold_oil_rotation" in reg, "GOR live_core must exist in registry"
        assert reg["cross_asset_momentum"].is_live is True
        assert reg["gold_oil_rotation"].is_live is True

    def test_archived_includes_post_drain_strats(self):
        qr._load_registry_cached.cache_clear()
        archived = archived_rejected_ids()
        # Bucket A (binance REJECTED)
        assert "btc_eth_dual_momentum" in archived
        assert "vol_breakout" in archived
        # Bucket C (ibkr_eu INSUFFICIENT/REJECTED)
        assert "eu_gap_open" in archived
        assert "vix_mean_reversion" in archived
