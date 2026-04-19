"""Promotion gate regression tests (Phase 7 XXL)."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

import core.governance.promotion_gate as pg
from core.governance.promotion_gate import (
    PromotionResult,
    check_promotion,
    grant_greenlight,
)


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    """Redirect WHITELIST_PATH + GREENLIGHT_DIR + ROOT for kill switch lookups."""
    whitelist = tmp_path / "live_whitelist.yaml"
    greenlights = tmp_path / "greenlights"
    monkeypatch.setattr(pg, "WHITELIST_PATH", whitelist)
    monkeypatch.setattr(pg, "GREENLIGHT_DIR", greenlights)
    monkeypatch.setattr(pg, "ROOT", tmp_path)
    return tmp_path


def _write_whitelist(path: Path, entries_by_book: dict):
    """Write a minimal live_whitelist.yaml structure."""
    path.write_text(yaml.safe_dump(entries_by_book), encoding="utf-8")


def _write_paper_journal(tmp: Path, strategy_id: str, n_entries: int):
    """Create a paper_journal.jsonl with n_entries lines."""
    journal_dir = tmp / "data" / "state" / strategy_id
    journal_dir.mkdir(parents=True, exist_ok=True)
    journal = journal_dir / "paper_journal.jsonl"
    with journal.open("w", encoding="utf-8") as f:
        for i in range(n_entries):
            f.write(json.dumps({"i": i, "ts": "2026-04-19"}) + "\n")
    return journal


# ---------------------------------------------------------------------------
# Whitelist lookup
# ---------------------------------------------------------------------------

class TestWhitelistLookup:
    def test_strategy_not_in_whitelist(self, isolated_paths):
        _write_whitelist(isolated_paths / "live_whitelist.yaml", {
            "binance_crypto": [{"strategy_id": "other", "status": "paper_only"}],
        })
        result = check_promotion("missing_strat")
        assert not result.is_pass()
        assert result.checks[0].name == "whitelist_lookup"


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

class TestPromotionChecks:
    def test_paper_age_pass_after_30_days(self, isolated_paths):
        old_date = (datetime.now(UTC) - timedelta(days=35)).strftime("%Y-%m-%d")
        _write_whitelist(isolated_paths / "live_whitelist.yaml", {
            "binance_crypto": [{
                "strategy_id": "test_strat",
                "status": "paper_only",
                "wf_source": "data/wf.json",
                "notes": f"Some context. Start paper: {old_date}\nMore notes.",
            }],
        })
        _write_paper_journal(isolated_paths, "test_strat", 15)
        # No greenlight - should fail on that check but paper_age should pass
        result = check_promotion("test_strat")
        age_check = next(c for c in result.checks if c.name == "age_paper_days")
        assert age_check.passed
        assert "35j" in age_check.message

    def test_paper_age_fail_below_30_days(self, isolated_paths):
        recent = (datetime.now(UTC) - timedelta(days=5)).strftime("%Y-%m-%d")
        _write_whitelist(isolated_paths / "live_whitelist.yaml", {
            "binance_crypto": [{
                "strategy_id": "test_strat",
                "status": "paper_only",
                "wf_source": "data/wf.json",
                "notes": f"Start paper: {recent}",
            }],
        })
        _write_paper_journal(isolated_paths, "test_strat", 15)
        result = check_promotion("test_strat")
        age_check = next(c for c in result.checks if c.name == "age_paper_days")
        assert not age_check.passed

    def test_paper_age_fail_no_marker(self, isolated_paths):
        _write_whitelist(isolated_paths / "live_whitelist.yaml", {
            "binance_crypto": [{
                "strategy_id": "test_strat",
                "status": "paper_only",
                "notes": "No date marker here",
            }],
        })
        result = check_promotion("test_strat")
        age_check = next(c for c in result.checks if c.name == "age_paper_days")
        assert not age_check.passed

    def test_paper_journal_pass(self, isolated_paths):
        _write_whitelist(isolated_paths / "live_whitelist.yaml", {
            "binance_crypto": [{
                "strategy_id": "test_strat",
                "status": "paper_only",
                "notes": "Start paper: 2025-01-01",
            }],
        })
        _write_paper_journal(isolated_paths, "test_strat", 25)
        result = check_promotion("test_strat")
        journal_check = next(c for c in result.checks if c.name == "paper_journal_trades")
        assert journal_check.passed
        assert "25" in journal_check.message

    def test_paper_journal_fail_too_few_entries(self, isolated_paths):
        _write_whitelist(isolated_paths / "live_whitelist.yaml", {
            "binance_crypto": [{
                "strategy_id": "test_strat",
                "status": "paper_only",
                "notes": "Start paper: 2025-01-01",
            }],
        })
        _write_paper_journal(isolated_paths, "test_strat", 3)
        result = check_promotion("test_strat")
        journal_check = next(c for c in result.checks if c.name == "paper_journal_trades")
        assert not journal_check.passed

    def test_paper_journal_fail_missing(self, isolated_paths):
        _write_whitelist(isolated_paths / "live_whitelist.yaml", {
            "binance_crypto": [{
                "strategy_id": "test_strat",
                "status": "paper_only",
                "notes": "Start paper: 2025-01-01",
            }],
        })
        result = check_promotion("test_strat")
        journal_check = next(c for c in result.checks if c.name == "paper_journal_trades")
        assert not journal_check.passed
        assert "No paper_journal" in journal_check.message

    def test_kill_switch_clean_when_no_state_file(self, isolated_paths):
        _write_whitelist(isolated_paths / "live_whitelist.yaml", {
            "binance_crypto": [{"strategy_id": "test", "status": "paper_only"}],
        })
        result = check_promotion("test")
        ks_check = next(c for c in result.checks if c.name == "kill_switch_clean_24h")
        assert ks_check.passed

    def test_kill_switch_active_blocks_promotion(self, isolated_paths):
        ks_path = isolated_paths / "data" / "kill_switch_state.json"
        ks_path.parent.mkdir(parents=True, exist_ok=True)
        ks_path.write_text(json.dumps({
            "active": True,
            "trigger_reason": "daily_loss_-7%",
        }), encoding="utf-8")

        _write_whitelist(isolated_paths / "live_whitelist.yaml", {
            "binance_crypto": [{"strategy_id": "test", "status": "paper_only"}],
        })
        result = check_promotion("test")
        ks_check = next(c for c in result.checks if c.name == "kill_switch_clean_24h")
        assert not ks_check.passed
        assert "ACTIVE" in ks_check.message


# ---------------------------------------------------------------------------
# Greenlight management
# ---------------------------------------------------------------------------

class TestGreenlight:
    def test_grant_greenlight_creates_signed_file(self, isolated_paths):
        path = grant_greenlight(
            strategy_id="alt_rel_strength_14_60_7",
            target="live_probation",
            signer="marc",
            note="reviewed paper journal 30j",
        )
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["strategy_id"] == "alt_rel_strength_14_60_7"
        assert data["signed_by"] == "marc"
        assert data["note"] == "reviewed paper journal 30j"

    def test_promotion_check_pass_with_greenlight(self, isolated_paths):
        old_date = (datetime.now(UTC) - timedelta(days=40)).strftime("%Y-%m-%d")
        _write_whitelist(isolated_paths / "live_whitelist.yaml", {
            "binance_crypto": [{
                "strategy_id": "test_strat",
                "status": "paper_only",
                "wf_source": "data/wf.json",
                "notes": f"Start paper: {old_date}",
            }],
        })
        _write_paper_journal(isolated_paths, "test_strat", 20)
        grant_greenlight("test_strat", "live_probation", "marc", "test")
        result = check_promotion("test_strat", target="live_probation")
        assert result.is_pass(), f"Expected PASS, got:\n{result.summary()}"

    def test_promotion_check_fail_without_greenlight(self, isolated_paths):
        old_date = (datetime.now(UTC) - timedelta(days=40)).strftime("%Y-%m-%d")
        _write_whitelist(isolated_paths / "live_whitelist.yaml", {
            "binance_crypto": [{
                "strategy_id": "test_strat",
                "status": "paper_only",
                "wf_source": "data/wf.json",
                "notes": f"Start paper: {old_date}",
            }],
        })
        _write_paper_journal(isolated_paths, "test_strat", 20)
        # No greenlight granted
        result = check_promotion("test_strat", target="live_probation")
        assert not result.is_pass()
        gl = next(c for c in result.checks if c.name == "manual_greenlight")
        assert not gl.passed


# ---------------------------------------------------------------------------
# Result formatting
# ---------------------------------------------------------------------------

class TestPromotionResult:
    def test_summary_renders(self, isolated_paths):
        _write_whitelist(isolated_paths / "live_whitelist.yaml", {
            "binance_crypto": [{"strategy_id": "test", "status": "paper_only"}],
        })
        result = check_promotion("test")
        out = result.summary()
        assert "Promotion Gate" in out
        assert "test" in out
        assert "verdict" in out
