"""Source-level guards for futures SL CHECK live/paper routing.

The regression we want to prevent:
- reading paper futures state
- checking only the live IBKR port 4002
- declaring the paper position "gone" and deleting it from state
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_sl_check_routes_paper_positions_to_paper_port():
    src = (ROOT / "worker.py").read_text(encoding="utf-8")
    assert 'for _mode_key, _fut_pos in _state_by_mode.items()' in src
    assert 'os.getenv("IBKR_PAPER_PORT", "4003")' in src
    assert 'if _mode_key == "paper"' in src


def test_sl_check_uses_per_mode_state_files():
    src = (ROOT / "worker.py").read_text(encoding="utf-8")
    assert 'f"futures_positions_{_sfx}.json"' in src
    assert 'f"futures_positions_{_mode_key}.json"' in src
    assert 'FUTURES SL CHECK ({_mode_key.upper()})' in src


def test_sl_check_uses_canonical_future_contract_factory():
    src = (ROOT / "worker.py").read_text(encoding="utf-8")
    assert "from core.worker.cycles.futures_runner import _make_future_contract as _sl_make_future_contract" in src
    assert "_contract = _sl_make_future_contract(_ps)" in src


def test_sl_check_reads_all_open_orders_not_only_current_client():
    src = (ROOT / "worker.py").read_text(encoding="utf-8")
    assert "_sl_ib.reqAllOpenOrders()" in src
