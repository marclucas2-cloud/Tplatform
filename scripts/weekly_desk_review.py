"""Weekly desk review — dim 22h UTC via systemd timer.

Phase 3.4 desk productif 2026-04-22. Genere un rapport hebdo consolide:
  - Equity start/end semaine + PnL net 7j
  - Trades live 7j (count + by_strategy)
  - Capital exposure moyenne + max DD semaine
  - Incidents actifs (TTL 72h applique)
  - Runtime health (services systemd + runtime_audit)

Output:
  reports/weekly/YYYY-MM-DD.md (fichier historique)
  Telegram push critical-level avec 5 metriques cles + lien vers report

Usage:
  python scripts/weekly_desk_review.py
  python scripts/weekly_desk_review.py --week-ending 2026-04-20 (backfill)
  python scripts/weekly_desk_review.py --no-telegram (dry-run local)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

REPORT_DIR = ROOT / "reports" / "weekly"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

LIVE_PNL_CSV = ROOT / "data" / "live_pnl" / "daily_equity.csv"
LIVE_PNL_SUMMARY = ROOT / "data" / "live_pnl" / "summary.json"


def _read_equity_csv() -> list[dict]:
    if not LIVE_PNL_CSV.exists():
        return []
    import csv as _csv
    rows = []
    with open(LIVE_PNL_CSV, newline="", encoding="utf-8") as f:
        for r in _csv.DictReader(f):
            try:
                for num in ("ibkr_equity_usd", "binance_equity_usd", "total_equity_usd",
                            "daily_return_pct", "drawdown_pct", "peak_equity_usd"):
                    if r.get(num) not in (None, ""):
                        r[num] = float(r[num])
                rows.append(r)
            except (ValueError, TypeError):
                continue
    return rows


def _week_slice(rows: list[dict], week_ending: date) -> list[dict]:
    week_start = week_ending - timedelta(days=6)
    out = []
    for r in rows:
        try:
            d = datetime.strptime(r["date"], "%Y-%m-%d").date()
            if week_start <= d <= week_ending:
                out.append(r)
        except (KeyError, ValueError):
            continue
    return out


def _count_incidents_active() -> tuple[int, list[dict]]:
    """Count + return active incidents (post TTL 72h filter)."""
    try:
        from core.governance.incidents_ttl import filter_active_incidents
    except ImportError:
        return 0, []

    incidents_dir = ROOT / "data" / "incidents"
    if not incidents_dir.exists():
        return 0, []

    # Load resolutions manifest
    resolved_ts: set = set()
    res_path = incidents_dir / "resolutions.jsonl"
    if res_path.exists():
        with open(res_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ts = json.loads(line).get("resolved_incident_timestamp")
                    if ts:
                        resolved_ts.add(ts)
                except json.JSONDecodeError:
                    continue

    raw = []
    for p in incidents_dir.glob("*.jsonl"):
        if p.name == "resolutions.jsonl":
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sev = (row.get("severity") or "").upper()
                if sev not in ("P0", "P1", "CRITICAL"):
                    continue
                ts = row.get("timestamp") or ""
                if ts in resolved_ts:
                    continue
                raw.append(row)

    active = filter_active_incidents(raw)
    return len(active), active


def _services_status() -> dict:
    """Check VPS systemd services status (run on VPS or skip if local dev)."""
    import subprocess
    svcs = [
        "trading-worker", "trading-dashboard", "trading-telegram",
        "trading-watchdog", "ibgateway", "ibgateway-paper",
    ]
    status: dict[str, str] = {}
    for s in svcs:
        try:
            r = subprocess.run(
                ["systemctl", "is-active", f"{s}.service"],
                capture_output=True, text=True, timeout=5,
            )
            status[s] = r.stdout.strip() or "unknown"
        except Exception:
            status[s] = "skipped"
    return status


def _load_summary() -> dict:
    if not LIVE_PNL_SUMMARY.exists():
        return {}
    try:
        return json.loads(LIVE_PNL_SUMMARY.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _strat_catalog_count() -> dict:
    """Return count by canonical status in quant_registry (Phase 3 catalogue clean)."""
    try:
        from core.governance.quant_registry import load_registry, archived_rejected_ids
    except ImportError:
        return {}
    try:
        entries = load_registry()
    except Exception:
        return {}
    by_status: dict[str, int] = {}
    for sid, e in entries.items():
        s = e.status or "unknown"
        by_status[s] = by_status.get(s, 0) + 1
    by_status["archived"] = len(archived_rejected_ids())
    return by_status


def build_report(week_ending: date) -> dict:
    """Compute all sections of the weekly review."""
    rows = _read_equity_csv()
    week_rows = _week_slice(rows, week_ending)

    start_eq = float(week_rows[0]["total_equity_usd"]) if week_rows else 0.0
    end_eq = float(week_rows[-1]["total_equity_usd"]) if week_rows else 0.0
    pnl_week = end_eq - start_eq
    ret_week_pct = (end_eq / start_eq - 1) * 100 if start_eq > 0 else 0.0

    peak_week = max((float(r["peak_equity_usd"]) for r in week_rows
                     if r.get("peak_equity_usd")), default=end_eq)
    max_dd_week_pct = 0.0
    for r in week_rows:
        dd = float(r.get("drawdown_pct") or 0)
        max_dd_week_pct = min(max_dd_week_pct, dd)

    summary = _load_summary()
    trades = summary.get("trades_count_30d", {}) or {}
    exposure = summary.get("capital_exposure", {}) or {}

    incidents_count, incidents_list = _count_incidents_active()
    services = _services_status()
    catalog = _strat_catalog_count()

    return {
        "week_ending": week_ending.isoformat(),
        "week_start": (week_ending - timedelta(days=6)).isoformat(),
        "equity_start_usd": round(start_eq, 2),
        "equity_end_usd": round(end_eq, 2),
        "pnl_week_usd": round(pnl_week, 2),
        "return_week_pct": round(ret_week_pct, 3),
        "max_dd_week_pct": round(max_dd_week_pct, 3),
        "peak_equity_week_usd": round(peak_week, 2),
        "n_days_in_week": len(week_rows),
        "trades_30d": trades,
        "capital_exposure": exposure,
        "incidents_active": incidents_count,
        "incidents_detail": incidents_list[:5],
        "services": services,
        "catalog_counts": catalog,
        "generated_at_utc": datetime.now(UTC).isoformat(),
    }


def render_markdown(report: dict) -> str:
    lines = []
    lines.append(f"# Weekly Desk Review — semaine se terminant {report['week_ending']}")
    lines.append("")
    lines.append(f"**Periode** : {report['week_start']} → {report['week_ending']} "
                 f"({report['n_days_in_week']} snapshots)")
    lines.append(f"**Genere** : {report['generated_at_utc']}")
    lines.append("")
    lines.append("## 1. PnL & Equity")
    lines.append(f"- Equity start : ${report['equity_start_usd']:,.2f}")
    lines.append(f"- Equity end   : ${report['equity_end_usd']:,.2f}")
    lines.append(f"- PnL semaine  : **${report['pnl_week_usd']:+,.2f}** ({report['return_week_pct']:+.2f}%)")
    lines.append(f"- Max DD semaine : {report['max_dd_week_pct']:+.2f}%")
    lines.append(f"- Peak semaine : ${report['peak_equity_week_usd']:,.2f}")
    lines.append("")
    lines.append("## 2. Trades live (30j)")
    trades = report.get("trades_30d", {})
    lines.append(f"- Count total 30j : **{trades.get('count', 0)}**")
    by_strat = trades.get("by_strategy") or {}
    if by_strat:
        for sid, n in sorted(by_strat.items(), key=lambda x: -x[1]):
            lines.append(f"  - {sid} : {n}")
    else:
        lines.append("  _(aucun trade live clos dans la fenetre)_")
    lines.append("")
    lines.append("## 3. Capital exposure (snapshot)")
    exp = report.get("capital_exposure", {})
    lines.append(f"- Exposed : ${exp.get('exposed_usd', 0):,.2f} ({exp.get('exposed_pct', 0):.2f}%)")
    lines.append(f"- Idle    : {exp.get('idle_pct', 0):.2f}%")
    per_book = exp.get("per_book_usd") or {}
    if per_book:
        for book, amt in per_book.items():
            lines.append(f"  - {book} : ${amt:,.2f}")
    lines.append("")
    lines.append("## 4. Incidents actifs (TTL 72h)")
    lines.append(f"- Count : **{report['incidents_active']}**")
    for inc in report.get("incidents_detail", []):
        book = (inc.get("context") or {}).get("book", "?")
        msg = (inc.get("message") or "")[:100]
        lines.append(f"  - [{inc.get('severity', '?')}] {book}: {msg}")
    lines.append("")
    lines.append("## 5. Services VPS")
    for svc, st in report.get("services", {}).items():
        mark = "OK" if st == "active" else f"!! {st}"
        lines.append(f"- {svc} : {mark}")
    lines.append("")
    lines.append("## 6. Catalogue strategies")
    for s, n in sorted(report.get("catalog_counts", {}).items()):
        lines.append(f"- {s} : {n}")
    lines.append("")
    lines.append("---")
    lines.append("_Rapport genere par scripts/weekly_desk_review.py_")
    return "\n".join(lines)


def render_telegram(report: dict) -> str:
    """5 metriques cles max, format compact pour Telegram."""
    trades = report.get("trades_30d", {}).get("count", 0)
    exp = report.get("capital_exposure", {})
    return (
        f"DESK WEEKLY {report['week_ending']}\n"
        f"PnL 7j: ${report['pnl_week_usd']:+,.0f} ({report['return_week_pct']:+.2f}%)\n"
        f"Max DD 7j: {report['max_dd_week_pct']:+.2f}%\n"
        f"Trades 30j: {trades}\n"
        f"Exposure: {exp.get('exposed_pct', 0):.1f}% idle={exp.get('idle_pct', 0):.1f}%\n"
        f"Incidents actifs: {report['incidents_active']}\n"
        f"Report: reports/weekly/{report['week_ending']}.md"
    )


def send_telegram(msg: str) -> bool:
    try:
        from core.worker.alerts import send_alert
        send_alert(msg, level="critical")
        return True
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--week-ending", type=str, default=None,
                        help="ISO date (YYYY-MM-DD); default = today")
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument("--json", action="store_true", help="print report as JSON to stdout")
    args = parser.parse_args()

    if args.week_ending:
        try:
            week_ending = date.fromisoformat(args.week_ending)
        except ValueError:
            print(f"Invalid --week-ending: {args.week_ending}", file=sys.stderr)
            return 2
    else:
        week_ending = datetime.now(UTC).date()

    report = build_report(week_ending)

    md = render_markdown(report)
    out_path = REPORT_DIR / f"{week_ending.isoformat()}.md"
    out_path.write_text(md, encoding="utf-8")
    print(f"Report written: {out_path}")

    if args.json:
        print(json.dumps(report, indent=2, default=str))

    if not args.no_telegram:
        tg_msg = render_telegram(report)
        ok = send_telegram(tg_msg)
        print(f"Telegram push: {'ok' if ok else 'failed'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
