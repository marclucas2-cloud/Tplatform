"""Quick alpha audit: PnL cumule par sleeve (Alpaca paper + crypto)."""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

# === ALPACA PAPER ===
state = json.load(open(ROOT / "data" / "state" / "paper_portfolio_state.json"))
log = state.get("strategy_pnl_log", {})
hist = state.get("history", [])

print("=== ALPACA PAPER — PnL cumule par strat ===")
total = 0
for sid, trades in sorted(log.items()):
    if not isinstance(trades, list) or not trades:
        continue
    pnls = [t.get("pnl", 0) for t in trades]
    n = len(pnls)
    cumsum = sum(pnls)
    wr = sum(1 for p in pnls if p > 0) / n
    avg = cumsum / n
    best = max(pnls)
    worst = min(pnls)
    total += cumsum
    print(f"  {sid:25s} n={n:>3} pnl=${cumsum:>+8.2f} wr={wr:>4.0%} avg=${avg:>+6.2f} best=${best:>+6.2f} worst=${worst:>+6.2f}")

print()
print(f"TOTAL Alpaca paper PnL cumule : ${total:+,.2f}")

if hist:
    first_cap = hist[0].get("capital", 0)
    last_cap = hist[-1].get("capital", 0)
    delta = last_cap - first_cap
    pct = (delta / first_cap) * 100 if first_cap else 0
    days = len(hist)
    first_date = hist[0].get("date", "?")
    last_date = hist[-1].get("date", "?")
    print(f"Capital: ${first_cap:,.0f} ({first_date}) -> ${last_cap:,.0f} ({last_date})")
    print(f"  Delta: ${delta:+,.2f} ({pct:+.2f}%) sur {days} jours ouvres")

# === CRYPTO ===
print()
print("=== CRYPTO STATE ===")

# btc_asia live_micro
p = ROOT / "data" / "state" / "btc_asia_mes_leadlag_q80_live_micro" / "journal.jsonl"
if p.exists():
    events = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    by_event = {}
    for e in events:
        ev = e.get("event", "?")
        by_event[ev] = by_event.get(ev, 0) + 1
    print(f"  btc_asia_q80_live_micro: {len(events)} events  -> {by_event}")
else:
    print("  btc_asia_q80_live_micro: (journal absent)")

# btc_asia paper
p = ROOT / "data" / "state" / "btc_asia_mes_leadlag_q80_long_only" / "paper_journal.jsonl"
if p.exists():
    trades = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    total_pnl = sum(t.get("pnl_usd", 0) for t in trades)
    n_buys = sum(1 for t in trades if t.get("side") == "BUY")
    n_short = sum(1 for t in trades if t.get("side") == "SHORT")
    print(f"  btc_asia_q80_paper:      {len(trades)} bars logged ({n_buys} BUY {n_short} SHORT) cumul=${total_pnl:+.2f}")

# alt_rel_strength
p = ROOT / "data" / "state" / "alt_rel_strength" / "paper_journal.jsonl"
if p.exists():
    events = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    last_cum = 0
    for e in events[::-1]:
        if "cumulative_pnl_usd" in e:
            last_cum = e["cumulative_pnl_usd"]
            break
    print(f"  alt_rel_strength:        {len(events)} events, cumul PnL=${last_cum:+.2f}")

# === IBKR FUTURES LIVE ===
print()
print("=== IBKR FUTURES LIVE ===")
p = ROOT / "data" / "state" / "futures_positions_live.json"
print(f"  positions live actuelles: {json.load(open(p))}")

# === STRATS PAR SOURCE ===
print()
print("=== STRATS DEPLOYABLES ===")
crypto_dir = ROOT / "strategies" / "crypto"
crypto_active = sorted(f.stem for f in crypto_dir.glob("*.py") if f.name != "__init__.py")
crypto_arch = sorted(f.stem for f in (ROOT / "strategies" / "_archive" / "crypto").glob("*.py"))
print(f"  Crypto actives sur disque: {len(crypto_active)} : {crypto_active}")
print(f"  Crypto archivees: {len(crypto_arch)}")
print()
fut_dir = ROOT / "strategies_v2" / "futures"
fut_active = sorted(f.stem for f in fut_dir.glob("*.py") if f.name != "__init__.py")
print(f"  Futures actives sur disque: {len(fut_active)} : {fut_active}")
