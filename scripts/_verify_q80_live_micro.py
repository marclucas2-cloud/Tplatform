"""One-shot verification script for btc_asia_q80 live_micro deployment."""
from core.governance.quant_registry import get_entry
from core.governance.live_whitelist import (
    is_strategy_live_allowed,
    get_strategy_entry,
)

sid = "btc_asia_mes_leadlag_q80_v80_long_only"
book = "binance_crypto"

e = get_entry(sid)
print(f"registry: status={e.status}, grade={e.grade}, live_start_at={e.live_start_at}")
print(f"whitelist.is_live_allowed = {is_strategy_live_allowed(sid, book)}")
wh = get_strategy_entry(sid, book)
if wh:
    print(f"whitelist.status = {wh.get('status')}")
    kc = wh.get("kill_criteria") or {}
    print(f"kill.drawdown_absolute_usd = {kc.get('drawdown_absolute_usd')}")
    print(f"kill.max_hold_hours = {kc.get('max_hold_hours')}")
    print(f"kill.no_pyramid_before_j = {kc.get('no_pyramid_before_j')}")
    print(f"max_notional_usd = {wh.get('max_notional_usd')}")
    print(f"max_risk_usd = {wh.get('max_risk_usd')}")
else:
    print("whitelist entry NOT FOUND")
