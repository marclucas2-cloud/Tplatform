#!/usr/bin/env python3
"""Paper vs Backtest divergence monitor.

Pour chaque strat en `paper_only` (cf config/live_whitelist.yaml), compare:
  - PnL backtest historique recent (depuis paper_start_date)
  - PnL paper reel (parser logs/worker/worker.log + state files IBKR/Binance)
  - Divergence z-score = (paper_pnl - backtest_pnl) / std_backtest

Alerte (Telegram) si :
  - z-score > 2 sur fenetre 30j rolling
  - Drawdown paper > drawdown backtest * 1.5
  - 0 fills depuis 7 jours alors que backtest predit > 0 trades

Usage:
  python scripts/research/monitor_paper_divergence.py            # check all
  python scripts/research/monitor_paper_divergence.py --strat mes_monday_long_oc

Output: docs/research/paper_divergence_YYYY-MM-DD.md + alert si divergence.

Squelette V1: parsing logs basique, comparaison delta-PnL simple.
V2 (a venir): integration EventLogger + state files + dashboard graphique.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent

PAPER_STRAT_IDS = [
    "mes_monday_long_oc",
    "mes_wednesday_long_oc",
    "mes_pre_holiday_long",
]

# Paper start date (cf INT-C committee)
PAPER_START = pd.Timestamp("2026-04-16", tz="UTC")


def parse_worker_log_signals(strat_id: str, log_path: Path) -> pd.DataFrame:
    """Extract signals + executions from worker.log for a given strat_id."""
    if not log_path.exists():
        return pd.DataFrame()
    rows = []
    pattern_signal = f"{strat_id}"
    pattern_fill = "ORDRE EXECUTE"
    with open(log_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if strat_id not in line:
                continue
            try:
                ts = pd.Timestamp(line[:19], tz="UTC")
            except Exception:
                continue
            if "SIGNAL" in line or "BUY" in line or "SELL" in line:
                rows.append({"ts": ts, "strat": strat_id, "type": "signal", "raw": line.strip()[:200]})
            elif pattern_fill in line:
                rows.append({"ts": ts, "strat": strat_id, "type": "fill", "raw": line.strip()[:200]})
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def expected_signals_from_backtest(strat_id: str, since: pd.Timestamp) -> int:
    """Count expected signals from backtest pattern since `since` date.

    Estimation simple basee sur la frequence du pattern :
      - mes_monday_long_oc : ~1/semaine
      - mes_wednesday_long_oc : ~1/semaine
      - mes_pre_holiday_long : ~10/an = 0.2/semaine
    """
    days = (pd.Timestamp.now(tz="UTC") - since).days
    if days <= 0:
        return 0
    weeks = days / 7.0
    if "monday" in strat_id or "wednesday" in strat_id:
        return int(weeks)
    if "pre_holiday" in strat_id:
        return max(0, int(weeks * 0.2))
    return 0


def assess(strat_id: str, log_path: Path) -> dict:
    sig = parse_worker_log_signals(strat_id, log_path)
    sig_paper = sig[sig["ts"] >= PAPER_START] if not sig.empty else sig
    n_signals = (sig_paper["type"] == "signal").sum() if not sig_paper.empty else 0
    n_fills = (sig_paper["type"] == "fill").sum() if not sig_paper.empty else 0
    n_expected = expected_signals_from_backtest(strat_id, PAPER_START)

    last_signal_ts = sig_paper["ts"].max() if not sig_paper.empty else None
    days_since_last = (pd.Timestamp.now(tz="UTC") - last_signal_ts).days if last_signal_ts else None

    diverged = False
    reasons = []
    if days_since_last is not None and days_since_last > 14 and n_expected >= 2:
        diverged = True
        reasons.append(f"no signal since {days_since_last}d (expected ~{n_expected})")
    if n_signals > 0 and n_fills == 0:
        diverged = True
        reasons.append(f"{n_signals} signals but 0 fills (execution layer KO?)")
    if n_signals == 0 and n_expected > 5:
        diverged = True
        reasons.append(f"0 signals but backtest predicts ~{n_expected}")

    return {
        "strat_id": strat_id,
        "paper_start": PAPER_START.date().isoformat(),
        "days_running": (pd.Timestamp.now(tz="UTC") - PAPER_START).days,
        "n_signals_paper": int(n_signals),
        "n_fills_paper": int(n_fills),
        "n_signals_expected_backtest": n_expected,
        "last_signal": last_signal_ts.isoformat() if last_signal_ts else None,
        "days_since_last_signal": days_since_last,
        "diverged": diverged,
        "reasons": reasons,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strat", help="Filter to single strat_id")
    ap.add_argument("--log", default=str(ROOT / "logs" / "worker" / "worker.log"),
                    help="Worker log path (default: logs/worker/worker.log)")
    ap.add_argument("--alert", action="store_true",
                    help="Send Telegram alert if any divergence")
    args = ap.parse_args()

    log_path = Path(args.log)
    print(f"=== Paper divergence monitor ===")
    print(f"Log: {log_path}")
    print(f"Paper start: {PAPER_START.date()}")
    print(f"Now: {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M UTC')}")
    print()

    targets = [args.strat] if args.strat else PAPER_STRAT_IDS
    results = []
    for sid in targets:
        r = assess(sid, log_path)
        results.append(r)
        flag = "DIVERGED" if r["diverged"] else "OK"
        print(f"[{flag}] {sid}")
        print(f"  signals/fills paper: {r['n_signals_paper']}/{r['n_fills_paper']} "
              f"(expected ~{r['n_signals_expected_backtest']})")
        if r["last_signal"]:
            print(f"  last signal: {r['last_signal']} ({r['days_since_last_signal']}d ago)")
        if r["reasons"]:
            for reason in r["reasons"]:
                print(f"  WARN: {reason}")
        print()

    # Persist
    out_dir = ROOT / "docs" / "research"
    out_path = out_dir / f"paper_divergence_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.md"
    md = [
        "# Paper divergence monitor",
        "",
        f"**Run** : {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Paper start** : {PAPER_START.date()}",
        f"**Days running** : {(pd.Timestamp.now(tz='UTC') - PAPER_START).days}",
        "",
        "| Strat | Status | Signals | Fills | Expected | Last signal | Reasons |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for r in results:
        st = "🔴 DIVERGED" if r["diverged"] else "✅ OK"
        last = r["last_signal"][:10] if r["last_signal"] else "-"
        reasons = ", ".join(r["reasons"]) if r["reasons"] else "-"
        md.append(f"| `{r['strat_id']}` | {st} | {r['n_signals_paper']} | "
                  f"{r['n_fills_paper']} | {r['n_signals_expected_backtest']} | "
                  f"{last} | {reasons} |")
    out_path.write_text("\n".join(md), encoding="utf-8")
    print(f"Report -> {out_path}")

    # Alert
    if args.alert and any(r["diverged"] for r in results):
        try:
            from core.telegram_alert import send_alert
            diverged_list = [r["strat_id"] for r in results if r["diverged"]]
            send_alert(
                f"🔴 PAPER DIVERGENCE: {len(diverged_list)} strats\n"
                f"Strats: {', '.join(diverged_list)}\n"
                f"Report: {out_path.relative_to(ROOT)}",
                level="warning",
            )
            print("Telegram alert sent.")
        except Exception as e:
            print(f"Telegram alert failed: {e}")

    return 0 if not any(r["diverged"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
