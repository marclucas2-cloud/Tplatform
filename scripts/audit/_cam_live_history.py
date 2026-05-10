"""Extract CAM live executions history from worker.log."""
from __future__ import annotations
import re
import gzip
from pathlib import Path
from collections import Counter

ROOT = Path("/opt/trading-platform")
LOGS = ROOT / "logs" / "worker"

# Account live = U25023333
LIVE_ACCT = "U25023333"

# Pattern for execDetails Execution lines
PATTERN = re.compile(
    r"execDetails Execution\(execId='(\S+?)', time=datetime\.datetime\(([\d, ]+)[^)]*\), "
    r"acctNumber='([^']+)', exchange='([^']+)', side='([^']+)', shares=([\d.]+), "
    r"price=([\d.]+).*?cumQty=([\d.]+).*?avgPrice=([\d.]+)"
)

# Also extract symbol from contract ref earlier in same record. Easier: grep the contract symbol from nearby line.
# Given the log format, execDetails comes after a Trade(...) record with contract=Future(symbol='MNQ', ...).
# We'll do a 2-pass: find all execDetails lines + find symbol by execId in surrounding context.

trades = []
seen_ids = set()

for log_file in sorted(LOGS.glob("worker.log*")):
    if log_file.suffix == ".gz":
        opener = lambda p: gzip.open(p, "rt", encoding="utf-8", errors="replace")
    else:
        opener = lambda p: open(p, "r", encoding="utf-8", errors="replace")
    try:
        with opener(log_file) as f:
            for line in f:
                if "execDetails Execution" not in line or LIVE_ACCT not in line:
                    continue
                m = PATTERN.search(line)
                if not m:
                    continue
                exec_id = m.group(1)
                if exec_id in seen_ids:
                    continue
                seen_ids.add(exec_id)
                time_parts = m.group(2)
                # parse "2026, 5, 8, 14, 0, 36"
                nums = [int(x.strip()) for x in time_parts.split(",")[:6]]
                from datetime import datetime
                ts = datetime(*nums) if len(nums) >= 3 else None
                # Symbol via line content : look for "symbol='XXX'" occurring just before in the record
                sym_match = re.search(r"symbol='([A-Z0-9]+)'", line)
                sym = sym_match.group(1) if sym_match else "?"
                trades.append({
                    "exec_id": exec_id,
                    "time": ts.isoformat() if ts else "?",
                    "side": m.group(5),
                    "shares": float(m.group(6)),
                    "price": float(m.group(7)),
                    "avg_price": float(m.group(9)),
                    "symbol": sym,
                })
    except Exception as e:
        print(f"  [warn] {log_file.name}: {e}")

trades.sort(key=lambda t: t["time"])

print(f"=== {len(trades)} executions LIVE U25023333 (CAM/GOR) ===\n")
by_sym = Counter(t["symbol"] for t in trades)
by_side = Counter(t["side"] for t in trades)
print("Par symbole:", dict(by_sym))
print("Par side:  ", dict(by_side))
print()
print("Tous les fills:")
for t in trades:
    print(f"  {t['time'][:19]} | {t['symbol']:>4} {t['side']:>3} {t['shares']:>4.0f} @ ${t['avg_price']:>9.2f}")
