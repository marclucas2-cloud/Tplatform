"""Test _disabled_whitelist_strategy_ids() de worker.py.

Regression bug 2026-04-20: STRAT-005 btc_dominance_rotation_v2 REJECTED
apparaissait 96x/24h dans crypto cycle log malgre status=disabled dans
live_whitelist.yaml. Fix: skip strats dont canonical_id est dans le set.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def worker_module():
    """Import worker.py en isole (pas executer __main__)."""
    spec = importlib.util.spec_from_file_location("worker_mod", ROOT / "worker.py")
    mod = importlib.util.module_from_spec(spec)
    # Pas executer spec.loader.exec_module -> trop de side effects.
    # On lit directement le source pour tester la fonction.
    return mod


class TestDisabledWhitelistStrategyIds:

    def test_returns_btc_dominance_disabled(self):
        """Le fichier canonique live_whitelist.yaml actuel marque
        btc_dominance_rotation_v2 + fx_carry_momentum_filter en disabled."""
        import yaml
        data = yaml.safe_load(
            (ROOT / "config" / "live_whitelist.yaml").read_text(encoding="utf-8")
        ) or {}
        disabled = set()
        for book, entries in data.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, dict) and entry.get("status") == "disabled":
                    sid = entry.get("strategy_id")
                    if sid:
                        disabled.add(sid)
        # Au moins btc_dominance doit etre disabled (REJECTED 2026-04-19)
        assert "btc_dominance_rotation_v2" in disabled
        # fx_carry aussi disabled (ESMA EU leverage limits)
        assert "fx_carry_momentum_filter" in disabled

    def test_crypto_strat_id_map_canonical_matches_whitelist(self):
        """Ligne rouge: STRAT-005 -> btc_dominance_rotation_v2 mapping
        binance_broker._CRYPTO_STRAT_ID_MAP doit pointer vers canonical
        present dans live_whitelist (sinon le skip ne fonctionne pas)."""
        from core.broker.binance_broker import _CRYPTO_STRAT_ID_MAP
        assert _CRYPTO_STRAT_ID_MAP["STRAT-005"] == "btc_dominance_rotation_v2"

    def test_loads_worker_helper_real_cached(self):
        """Le vrai helper dans worker.py renvoie un frozenset incluant
        btc_dominance_rotation_v2."""
        # Minimal exec: on execute seulement jusqu'aux imports + helper
        source = (ROOT / "worker.py").read_text(encoding="utf-8")
        # Extract helper function + stub ROOT + logger
        stub = """
import logging
import yaml
from pathlib import Path
from functools import lru_cache
ROOT = Path(r'""" + str(ROOT).replace("\\", "/") + """')
logger = logging.getLogger('test')
"""
        # Find the helper function body in worker.py
        start = source.index("@lru_cache(maxsize=1)\ndef _disabled_whitelist_strategy_ids")
        end = source.index("\nlog_dir = ROOT", start)
        helper_src = source[start:end]
        ns: dict = {}
        exec(stub + helper_src, ns)
        result = ns["_disabled_whitelist_strategy_ids"]()
        assert isinstance(result, frozenset)
        assert "btc_dominance_rotation_v2" in result
