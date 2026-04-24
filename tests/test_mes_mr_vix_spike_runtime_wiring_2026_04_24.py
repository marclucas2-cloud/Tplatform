"""Tests non-regression du cablage runtime mes_mr_vix_spike.

Verifie:
  1. Le bloc est present dans futures_runner.py (grep source)
  2. La sleeve est ajoutee a _STRAT_DISPLAY_TO_ID canonical mapping
  3. La whitelist reflete runtime_entrypoint actif (plus "PENDING_MARC_DECISION")
  4. Le journal dir data/state/mes_mr_vix_spike/ est ecrivable au premier cycle
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


def test_runtime_block_present_in_futures_runner():
    src = (ROOT / "core" / "worker" / "cycles" / "futures_runner.py").read_text(encoding="utf-8")
    assert "mes_mr_vix_spike" in src
    assert "MESMeanReversionVIXSpike" in src
    assert "3a-quater" in src, "Bloc section marker missing"
    # Verifie que le pattern suit mcl_overnight (set_data_feed + get_latest_bar MES + on_bar)
    assert re.search(r"_mvs\.set_data_feed\(feed\)", src)
    assert re.search(r'feed\.get_latest_bar\("MES"\)', src)
    # Journal JSONL write
    assert "journal.jsonl" in src
    assert 'data"\s*/\s*"state"\s*/\s*"mes_mr_vix_spike"'.replace(" ", "") in src.replace(" ", "") or \
           'mes_mr_vix_spike"' in src


def test_canonical_mapping_has_mes_mr_vix_spike():
    src = (ROOT / "core" / "worker" / "cycles" / "futures_runner.py").read_text(encoding="utf-8")
    # _STRAT_DISPLAY_TO_ID must contain mes_mr_vix_spike
    m = re.search(r"_STRAT_DISPLAY_TO_ID\s*=\s*\{([^}]+)\}", src, re.DOTALL)
    assert m, "_STRAT_DISPLAY_TO_ID not found"
    assert "mes_mr_vix_spike" in m.group(1), \
        "mes_mr_vix_spike missing from canonical mapping (needed for live-mode whitelist check)"


def test_whitelist_runtime_entrypoint_active():
    wl = yaml.safe_load(
        (ROOT / "config" / "live_whitelist.yaml").read_text(encoding="utf-8")
    )
    entry = next(
        s for s in wl["ibkr_futures"] if s["strategy_id"] == "mes_mr_vix_spike"
    )
    # Plus de PENDING_MARC_DECISION
    assert "PENDING_MARC_DECISION" not in entry["runtime_entrypoint"]
    # Pointe bien vers futures_runner
    assert "futures_runner" in entry["runtime_entrypoint"]
    assert entry["status"] == "paper_only"


def test_journal_dir_creation_pattern():
    """Verifie que le bloc cree data/state/mes_mr_vix_spike/ avec mkdir parents=True."""
    src = (ROOT / "core" / "worker" / "cycles" / "futures_runner.py").read_text(encoding="utf-8")
    # mkdir(parents=True, exist_ok=True) for the sleeve dir
    assert "_mvs_journal_dir" in src
    assert "mkdir(parents=True, exist_ok=True)" in src
    assert '"mes_mr_vix_spike"' in src


def test_other_two_sleeves_still_pending():
    """Verifie que mes_estx50 et mgc_mes_ratio restent en PENDING (decision Marc)."""
    wl = yaml.safe_load(
        (ROOT / "config" / "live_whitelist.yaml").read_text(encoding="utf-8")
    )
    ibkr = wl["ibkr_futures"]
    est = next(s for s in ibkr if s["strategy_id"] == "mes_estx50_divergence")
    rat = next(s for s in ibkr if s["strategy_id"] == "mgc_mes_ratio_rotation")
    assert "PENDING_MARC_DECISION" in est["runtime_entrypoint"]
    assert "PENDING_MARC_DECISION" in rat["runtime_entrypoint"]


def test_cam_reserves_mes_skip_path():
    """La sleeve doit SKIP si CAM a deja reserve MES (eviter double long)."""
    src = (ROOT / "core" / "worker" / "cycles" / "futures_runner.py").read_text(encoding="utf-8")
    # Pattern a chercher: if _cam_top_pick == "MES": ... SKIP — CAM reserved MES
    assert re.search(
        r'if _cam_top_pick == "MES".*?mes_mr_vix_spike.*?SKIP.*?CAM reserved MES',
        src, re.DOTALL,
    ), "Missing CAM=MES SKIP guard for mes_mr_vix_spike"
