"""Tests for core/runtime/preflight.py (A5/E1/E3 plan 9.0)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
import yaml

import core.runtime.preflight as pre
from core.runtime.preflight import (
    PreflightCheck,
    PreflightResult,
    boot_preflight,
)


@pytest.fixture
def isolated_preflight(tmp_path, monkeypatch):
    """Redirect all preflight paths to tmp_path for isolation."""
    monkeypatch.setattr(pre, "ROOT", tmp_path)
    monkeypatch.setattr(pre, "BOOKS_REGISTRY", tmp_path / "config" / "books_registry.yaml")
    monkeypatch.setattr(pre, "LIVE_WHITELIST", tmp_path / "config" / "live_whitelist.yaml")
    monkeypatch.setattr(pre, "QUANT_REGISTRY", tmp_path / "config" / "quant_registry.yaml")
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _write_minimal_registries(tmp: Path, books: list[dict]):
    (tmp / "config" / "books_registry.yaml").write_text(
        yaml.safe_dump({"metadata": {"version": 1}, "books": books}),
        encoding="utf-8",
    )
    (tmp / "config" / "live_whitelist.yaml").write_text(
        yaml.safe_dump({"metadata": {"version": 1}}),
        encoding="utf-8",
    )
    (tmp / "config" / "quant_registry.yaml").write_text(
        yaml.safe_dump({"metadata": {"version": 1}, "strategies": []}),
        encoding="utf-8",
    )


class TestRegistryChecks:
    def test_all_registries_missing_fails(self, isolated_preflight):
        result = boot_preflight(
            check_equity_state=False,
            check_data_freshness=False,
            check_ibkr_gateway=False,
        )
        names = [c.name for c in result.checks if not c.passed]
        assert "registry::books_registry" in names
        assert "registry::live_whitelist" in names
        assert "registry::quant_registry" in names

    def test_registries_present_pass(self, isolated_preflight):
        _write_minimal_registries(isolated_preflight, [])
        result = boot_preflight(
            check_equity_state=False,
            check_data_freshness=False,
            check_ibkr_gateway=False,
        )
        assert result.all_passed, result.summary()

    def test_corrupted_registry_fails(self, isolated_preflight):
        _write_minimal_registries(isolated_preflight, [])
        # Corrupt the books_registry
        (isolated_preflight / "config" / "books_registry.yaml").write_text(
            "{ not: valid [yaml", encoding="utf-8"
        )
        result = boot_preflight(
            check_equity_state=False, check_data_freshness=False,
            check_ibkr_gateway=False,
        )
        fail = next(c for c in result.checks if c.name == "registry::books_registry")
        assert not fail.passed
        assert "parse error" in fail.message


class TestEquityStateCheck:
    def test_live_book_missing_equity_state_fails(self, isolated_preflight):
        _write_minimal_registries(isolated_preflight, [
            {"book_id": "ibkr_futures", "mode_authorized": "live_allowed"},
        ])
        result = boot_preflight(
            check_equity_state=True, check_data_freshness=False,
            check_ibkr_gateway=False,
        )
        equity_check = next(c for c in result.checks if c.name == "equity_state::ibkr_futures")
        assert not equity_check.passed
        assert equity_check.severity == "critical"

    def test_live_book_with_equity_state_passes(self, isolated_preflight):
        _write_minimal_registries(isolated_preflight, [
            {"book_id": "ibkr_futures", "mode_authorized": "live_allowed"},
        ])
        state_path = isolated_preflight / "data" / "state" / "ibkr_futures" / "equity_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({"equity": 11000}), encoding="utf-8")
        result = boot_preflight(
            check_equity_state=True, check_data_freshness=False,
            check_ibkr_gateway=False,
        )
        equity_check = next(c for c in result.checks if c.name == "equity_state::ibkr_futures")
        assert equity_check.passed

    def test_paper_book_missing_equity_state_tolerated(self, isolated_preflight):
        _write_minimal_registries(isolated_preflight, [
            {"book_id": "alpaca_us", "mode_authorized": "paper_only"},
        ])
        result = boot_preflight(
            check_equity_state=True, check_data_freshness=False,
            check_ibkr_gateway=False,
        )
        equity_check = next(c for c in result.checks if c.name == "equity_state::alpaca_us")
        assert equity_check.passed
        assert equity_check.severity == "info"

    def test_disabled_book_skipped(self, isolated_preflight):
        _write_minimal_registries(isolated_preflight, [
            {"book_id": "ibkr_fx", "mode_authorized": "disabled"},
        ])
        result = boot_preflight(
            check_equity_state=True, check_data_freshness=False,
            check_ibkr_gateway=False,
        )
        assert not any(c.name == "equity_state::ibkr_fx" for c in result.checks)


class TestFailClosed:
    def test_fail_closed_raises_systemexit_on_critical(self, isolated_preflight):
        _write_minimal_registries(isolated_preflight, [
            {"book_id": "ibkr_futures", "mode_authorized": "live_allowed"},
        ])
        # No equity_state -> critical failure
        with pytest.raises(SystemExit) as exc:
            boot_preflight(
                check_equity_state=True, check_data_freshness=False,
                check_ibkr_gateway=False,
                fail_closed=True,
            )
        assert exc.value.code == 2

    def test_fail_closed_returns_normally_on_pass(self, isolated_preflight):
        _write_minimal_registries(isolated_preflight, [])
        result = boot_preflight(
            check_equity_state=False, check_data_freshness=False,
            check_ibkr_gateway=False,
            fail_closed=True,
        )
        assert result.all_passed


class TestSummary:
    def test_summary_contains_sections(self, isolated_preflight):
        _write_minimal_registries(isolated_preflight, [])
        result = boot_preflight(
            check_equity_state=False, check_data_freshness=False,
            check_ibkr_gateway=False,
        )
        out = result.summary()
        assert "Boot Preflight" in out
        assert "critical failures" in out
        assert "registry::books_registry" in out
