"""P0 non-regression 2026-04-23: all MarketOrder entries must set TIF explicitly.

Contexte : IBKR Error 10349 "Order TIF was set to DAY based on order preset"
a cause l'annulation du BUY MCL live de CAM le 23/04 sur compte canonical
U25023333 (paper DUP573894 silently resubmitted et filled). Fix: forcer
tif="DAY" explicite sur tout MarketOrder entry/exit dans le code
(futures_runner.py, macro_ecb_runner.py, ibkr_adapter.py).

Ce test scanne le code source pour garantir qu'aucun pattern
`IbMarketOrder(...)` ou `MarketOrder(...)` construit sans suivi immediat
de `.tif =` n'existe dans les fichiers d'execution live.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Fichiers dans lesquels tout MarketOrder doit forcer tif explicite
FILES_TO_CHECK = [
    ROOT / "core" / "worker" / "cycles" / "futures_runner.py",
    ROOT / "core" / "worker" / "cycles" / "macro_ecb_runner.py",
    ROOT / "core" / "broker" / "ibkr_adapter.py",
]


def _extract_market_order_blocks(src: str) -> list[tuple[int, str]]:
    """Trouve tous les 'MarketOrder(' ou 'IbMarketOrder(' ou alias (_FailMarketOrder).
    Retourne liste de (line_num, block_content) ou block_content = 6 lignes suivantes.
    """
    results = []
    lines = src.splitlines()
    pattern = re.compile(r"\b(?:Ib|_Fail)?MarketOrder\s*\(")
    for i, line in enumerate(lines):
        if pattern.search(line) and "from " not in line and "import " not in line:
            # Grab current + next 4 lines for context
            block = "\n".join(lines[i:i + 5])
            results.append((i + 1, block))
    return results


class TestExplicitTIFOnMarketOrders:
    """P0 non-regression : every MarketOrder construction must set tif explicitly."""

    @pytest.mark.parametrize("file_path", FILES_TO_CHECK, ids=lambda p: p.name)
    def test_market_order_has_explicit_tif(self, file_path: Path):
        src = file_path.read_text(encoding="utf-8")
        blocks = _extract_market_order_blocks(src)
        assert blocks, f"No MarketOrder found in {file_path.name} (unexpected)"

        failures = []
        for line_num, block in blocks:
            # Check si dans les 5 lignes suivantes il y a un assignment .tif =
            if not re.search(r"\.tif\s*=", block):
                failures.append(f"{file_path.name}:{line_num}\n{block}\n")

        assert not failures, (
            "MarketOrder creation without explicit .tif assignment found. "
            "P0 FIX 2026-04-23: every MKT order must force tif=\"DAY\" to prevent "
            "IBKR Error 10349 (TIF rewritten by account preset, causing live "
            "order cancellation). Offenders:\n" + "\n".join(failures)
        )

    def test_futures_runner_cam_entry_uses_day_tif(self):
        """Check specific: the CAM live entry path sets tif=DAY explicitly."""
        src = (ROOT / "core" / "worker" / "cycles" / "futures_runner.py").read_text(encoding="utf-8")
        # Find the _entry_order assignment block
        m = re.search(r"_entry_order\s*=\s*IbMarketOrder\([^)]+\).*?_entry_trade", src, re.DOTALL)
        assert m, "Could not locate CAM entry block"
        block = m.group(0)
        assert '_entry_order.tif = "DAY"' in block, (
            "CAM entry order does not set tif=DAY. This is the P0 fix — "
            "required to prevent Error 10349 cancellation on U25023333 canonical."
        )

    def test_macro_ecb_entry_uses_day_tif(self):
        src = (ROOT / "core" / "worker" / "cycles" / "macro_ecb_runner.py").read_text(encoding="utf-8")
        # Pattern: entry = IbMarketOrder(...) ... entry.tif = "DAY"
        m = re.search(r"entry\s*=\s*IbMarketOrder\([^)]+\).*?entry\.tif\s*=", src, re.DOTALL)
        assert m, "macro_ecb entry does not set tif explicitly (P0 fix required)"
