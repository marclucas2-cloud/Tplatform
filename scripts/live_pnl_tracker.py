"""Live P&L tracker — daily snapshot of live broker equity.

Produces a rolling CSV + JSONL log to PROVE profitability over time. This is
the single source of truth for "is the live strategy making money?" — more
important than any backtest or paper P&L, because it reflects real slippage,
fees, latency, and execution quality.

Outputs:
  data/live_pnl/daily_equity.csv      # one row per day (append-only)
  data/live_pnl/daily_pnl.jsonl       # same data as JSONL with extras
  data/live_pnl/summary.json          # running stats (CAGR, Sharpe, MaxDD)

Usage:
  python scripts/live_pnl_tracker.py                      # snapshot today
  python scripts/live_pnl_tracker.py --date 2026-04-19    # backfill date
  python scripts/live_pnl_tracker.py --summary            # print summary only
  python scripts/live_pnl_tracker.py --force              # override same-day

Cron suggestion (VPS Hetzner):
  # Daily snapshot at 22h00 UTC (00h Paris, after close all sessions)
  0 22 * * * cd /opt/trading-platform && python3 scripts/live_pnl_tracker.py
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("live_pnl_tracker")

OUT_DIR = ROOT / "data" / "live_pnl"
OUT_DIR.mkdir(parents=True, exist_ok=True)
CSV_PATH = OUT_DIR / "daily_equity.csv"
JSONL_PATH = OUT_DIR / "daily_pnl.jsonl"
SUMMARY_PATH = OUT_DIR / "summary.json"

CSV_HEADERS = [
    "date", "ibkr_equity_usd", "binance_equity_usd", "total_equity_usd",
    "daily_return_pct", "cum_return_pct", "peak_equity_usd", "drawdown_pct",
    "source",
]


def _fetch_ibkr_live_equity() -> float | None:
    """Fetch IBKR live account equity in USD.

    Returns None on failure (fetch impossible) to distinguish from 0.0 (vrai zero).
    Caller doit fail-closed sur None pour eviter d'ecrire un snapshot partiel
    (sinon daily_return=-52% fantome quand IB Gateway offline nocturne).

    Uses a dedicated clientId (200) to avoid conflict with the running worker
    (which uses 1, 3, 10, 77, 80-89, 310-329).
    """
    if os.getenv("IBKR_PAPER", "true").lower() == "true":
        logger.info("IBKR_PAPER=true - skipping IBKR live fetch")
        return None
    try:
        from core.broker.ibkr_adapter import IBKRBroker
        broker = IBKRBroker(client_id=200)
        info = broker.get_account_info()
        for key in ("equity", "net_liquidation_usd", "nav", "net_liquidation"):
            v = info.get(key) if isinstance(info, dict) else None
            if v:
                try:
                    broker.disconnect() if hasattr(broker, "disconnect") else None
                except Exception:
                    pass
                return float(v)
    except Exception as e:
        logger.warning(f"IBKR live equity fetch failed: {e}")
    return None


def _fetch_binance_live_equity() -> float | None:
    """Fetch Binance live equity in USD (spot USDC + margin + earn).

    Returns None on failure (caller fail-closed, cf. _fetch_ibkr_live_equity).
    """
    if os.getenv("BINANCE_TESTNET", "false").lower() == "true":
        logger.info("BINANCE_TESTNET=true - skipping Binance live fetch")
        return None
    if not os.getenv("BINANCE_API_KEY") or not os.getenv("BINANCE_API_SECRET"):
        logger.info("BINANCE creds missing - skipping")
        return None
    try:
        from core.broker.binance_broker import BinanceBroker
        bnb = BinanceBroker()
        info = bnb.get_account_info()
        # binance_broker returns {"equity": X, "spot_total_usd": Y, "earn_total_usd": Z}
        for key in ("equity", "total_equity_usd", "equity_usd", "nav"):
            v = info.get(key) if isinstance(info, dict) else None
            if v:
                return float(v)
    except Exception as e:
        logger.warning(f"Binance live equity fetch failed: {e}")
    return None


def _read_history() -> list[dict]:
    """Read existing CSV history, parsing numeric columns."""
    if not CSV_PATH.exists():
        return []
    rows = []
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                for num_col in ("ibkr_equity_usd", "binance_equity_usd", "total_equity_usd",
                                "daily_return_pct", "cum_return_pct", "peak_equity_usd", "drawdown_pct"):
                    if r.get(num_col) not in (None, ""):
                        r[num_col] = float(r[num_col])
                rows.append(r)
            except (ValueError, TypeError):
                continue
    return rows


def _atomic_write_csv(rows: list[dict]) -> None:
    """Atomic CSV write: tempfile + os.replace."""
    tmp_path = CSV_PATH.with_suffix(".csv.tmp")
    with open(tmp_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in CSV_HEADERS})
    os.replace(tmp_path, CSV_PATH)


def _append_jsonl(entry: dict) -> None:
    with open(JSONL_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _compute_running_stats(rows: list[dict]) -> dict:
    """Compute CAGR, annualized Sharpe, MaxDD over the full history."""
    if len(rows) < 2:
        return {"n_days": len(rows), "insufficient": True}

    equities = [float(r["total_equity_usd"]) for r in rows if r.get("total_equity_usd")]
    if len(equities) < 2:
        return {"n_days": len(rows), "insufficient": True}

    daily_returns = [float(r["daily_return_pct"]) / 100.0 for r in rows[1:]
                     if r.get("daily_return_pct") not in (None, "")]
    if not daily_returns:
        return {"n_days": len(rows), "insufficient": True}

    try:
        start_date = datetime.strptime(rows[0]["date"], "%Y-%m-%d").date()
        end_date = datetime.strptime(rows[-1]["date"], "%Y-%m-%d").date()
    except ValueError:
        return {"n_days": len(rows), "error": "bad_dates"}

    days = max((end_date - start_date).days, 1)
    years = days / 365.25
    cagr = (equities[-1] / equities[0]) ** (1 / years) - 1 if years > 0 and equities[0] > 0 else 0.0

    mean_r = sum(daily_returns) / len(daily_returns)
    var = sum((r - mean_r) ** 2 for r in daily_returns) / max(len(daily_returns) - 1, 1)
    std = math.sqrt(var)
    sharpe = (mean_r * 252) / (std * math.sqrt(252)) if std > 0 else 0.0

    peak = equities[0]
    max_dd = 0.0
    for eq in equities:
        peak = max(peak, eq)
        dd = (eq - peak) / peak if peak > 0 else 0.0
        max_dd = min(max_dd, dd)

    return {
        "n_days": len(rows),
        "start_date": rows[0]["date"],
        "end_date": rows[-1]["date"],
        "start_equity_usd": equities[0],
        "end_equity_usd": equities[-1],
        "pnl_usd": equities[-1] - equities[0],
        "cum_return_pct": (equities[-1] / equities[0] - 1) * 100 if equities[0] else 0.0,
        "cagr_pct": cagr * 100,
        "sharpe_annual": sharpe,
        "max_dd_pct": max_dd * 100,
        "last_updated": datetime.now(UTC).isoformat(),
    }


def take_snapshot(target_date: date | None = None, force: bool = False) -> dict:
    """Take a snapshot of today's equity and append to CSV/JSONL."""
    target_date = target_date or datetime.now(UTC).date()
    date_str = target_date.isoformat()

    rows = _read_history()
    if not force and any(r.get("date") == date_str for r in rows):
        logger.warning(f"Snapshot for {date_str} already exists — use --force to override")
        return {"skipped": True, "date": date_str}

    ibkr_eq = _fetch_ibkr_live_equity()
    binance_eq = _fetch_binance_live_equity()

    # Fail-closed : si l'un des 2 brokers ne repond pas (None), REFUS snapshot.
    # Sinon on ecrit un snapshot partiel (ex: ibkr=0 nocturne IB Gateway) qui
    # genere un daily_return faux (-52% fantome vs veille somme-complete).
    if ibkr_eq is None or binance_eq is None:
        missing = []
        if ibkr_eq is None:
            missing.append("IBKR")
        if binance_eq is None:
            missing.append("Binance")
        logger.error(
            f"Broker fetch incomplete: {', '.join(missing)} returned None - "
            f"snapshot refused (fail-closed, evite daily_return fantome)"
        )
        return {"error": "partial_fetch", "date": date_str,
                "ibkr": ibkr_eq, "binance": binance_eq, "missing": missing}

    total_eq = ibkr_eq + binance_eq
    if total_eq <= 0:
        logger.error("Both brokers returned 0.0 equity — snapshot refused (fail-closed)")
        return {"error": "no_equity", "date": date_str, "ibkr": ibkr_eq, "binance": binance_eq}

    prev_eq = float(rows[-1]["total_equity_usd"]) if rows else total_eq
    peak = max((float(r["peak_equity_usd"]) for r in rows if r.get("peak_equity_usd")),
               default=total_eq)
    peak = max(peak, total_eq)

    daily_ret = (total_eq / prev_eq - 1) * 100 if prev_eq > 0 else 0.0
    start_eq = float(rows[0]["total_equity_usd"]) if rows else total_eq
    cum_ret = (total_eq / start_eq - 1) * 100 if start_eq > 0 else 0.0
    dd = (total_eq - peak) / peak * 100 if peak > 0 else 0.0

    row = {
        "date": date_str,
        "ibkr_equity_usd": round(ibkr_eq, 2),
        "binance_equity_usd": round(binance_eq, 2),
        "total_equity_usd": round(total_eq, 2),
        "daily_return_pct": round(daily_ret, 4),
        "cum_return_pct": round(cum_ret, 4),
        "peak_equity_usd": round(peak, 2),
        "drawdown_pct": round(dd, 4),
        "source": "live_brokers",
    }

    # Replace same-date row if force, else append
    rows = [r for r in rows if r.get("date") != date_str]
    rows.append(row)
    rows.sort(key=lambda r: r["date"])

    _atomic_write_csv(rows)
    _append_jsonl({**row, "timestamp_utc": datetime.now(UTC).isoformat()})

    summary = _compute_running_stats(rows)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    logger.info(
        f"Snapshot OK {date_str}: IBKR=${ibkr_eq:,.0f} Binance=${binance_eq:,.0f} "
        f"Total=${total_eq:,.0f} daily={daily_ret:+.2f}% dd={dd:+.2f}%"
    )
    return {"row": row, "summary": summary}


def print_summary() -> None:
    if not SUMMARY_PATH.exists():
        print("No summary yet — run a snapshot first.")
        return
    s = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    print("=" * 60)
    print(f"Live P&L Summary ({s.get('n_days', 0)} days)")
    print("=" * 60)
    if s.get("insufficient"):
        print("  Insufficient history (need ≥2 days)")
        return
    print(f"  Period       : {s['start_date']} → {s['end_date']}")
    print(f"  Start equity : ${s['start_equity_usd']:,.2f}")
    print(f"  End equity   : ${s['end_equity_usd']:,.2f}")
    print(f"  P&L          : ${s['pnl_usd']:+,.2f}")
    print(f"  Cum return   : {s['cum_return_pct']:+.2f}%")
    print(f"  CAGR         : {s['cagr_pct']:+.2f}%")
    print(f"  Sharpe ann.  : {s['sharpe_annual']:+.2f}")
    print(f"  Max DD       : {s['max_dd_pct']:+.2f}%")
    print(f"  Updated      : {s['last_updated']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Live P&L daily tracker")
    parser.add_argument("--date", type=str, default=None, help="Override date (YYYY-MM-DD)")
    parser.add_argument("--force", action="store_true", help="Override same-day row")
    parser.add_argument("--summary", action="store_true", help="Print summary only")
    args = parser.parse_args()

    if args.summary:
        print_summary()
        return 0

    target = None
    if args.date:
        try:
            target = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            logger.error(f"Invalid date: {args.date}")
            return 2

    result = take_snapshot(target, force=args.force)
    if result.get("error"):
        return 3
    print_summary()
    return 0


if __name__ == "__main__":
    sys.exit(main())
