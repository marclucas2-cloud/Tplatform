"""Alpaca Go / No-Go 25K gate — machine-readable decision.

Iter3 plan (2026-04-19). Evalue si un depot $25K Alpaca est justifie
pour passer `us_sector_ls_40_5` (ou autre strat Alpaca) de paper a
live_probation, en utilisant le PDT waiver.

Regles definies dans docs/audit/alpaca_go_25k_rule.md.

Usage:
    python scripts/alpaca_go_25k_gate.py --strategy us_sector_ls_40_5
    python scripts/alpaca_go_25k_gate.py --strategy us_sector_ls_40_5 --min-days 45

Exit codes:
    0 = GO_25K (depot recommande)
    1 = WATCH_* (continuer paper)
    2 = NO_GO_* (blocage)
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MIN_DAYS = 30
DEFAULT_MIN_TRADES = 12
DEFAULT_MAX_DD_PCT = 6.0
DEFAULT_MAX_DIV_SIGMA_GO = 1.5
DEFAULT_MAX_DIV_SIGMA_NOGO = 2.5


@dataclass
class GateMetrics:
    strategy_id: str
    paper_start_at: str | None
    paper_days: int
    trade_count: int
    paper_pnl_net: float
    paper_max_dd_pct: float
    paper_sharpe: float | None
    paper_wr: float | None
    div_sigmas: dict
    incidents_open_p0p1: int
    verdict: str
    reasons: list
    recommendation: str
    checked_at: str


def _load_yaml(path: Path) -> dict:
    import yaml

    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _get_quant_entry(strategy_id: str) -> dict | None:
    qr = _load_yaml(ROOT / "config" / "quant_registry.yaml")
    for entry in qr.get("strategies", []) or []:
        if entry.get("strategy_id") == strategy_id:
            return entry
    return None


def _paper_start_at(entry: dict | None) -> str | None:
    if entry is None:
        return None
    return entry.get("paper_start_at")


def _find_paper_journal(strategy_id: str) -> Path | None:
    candidates = [
        ROOT / "data" / "state" / strategy_id / "paper_journal.jsonl",
        ROOT / "data" / "state" / "us_sector_ls" / "paper_journal.jsonl",
        ROOT / "data" / "state" / "alpaca_us" / f"{strategy_id}_journal.jsonl",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _find_backtest_manifest(strategy_id: str) -> dict | None:
    wf_dir = ROOT / "data" / "research" / "wf_manifests"
    if not wf_dir.exists():
        return None
    matches = sorted(wf_dir.glob(f"{strategy_id}_*.json"), reverse=True)
    if not matches:
        return None
    try:
        with open(matches[0], encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _load_resolved_incidents() -> set[str]:
    """Load timestamps of resolved incidents from data/incidents/resolutions.jsonl.

    Resolution file is append-only, each line = {resolved_incident_timestamp, resolution_commit, ...}.
    Returns set of resolved timestamps (for efficient membership check).
    """
    resolutions_path = ROOT / "data" / "incidents" / "resolutions.jsonl"
    resolved: set[str] = set()
    if not resolutions_path.exists():
        return resolved
    with open(resolutions_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                ts = row.get("resolved_incident_timestamp")
                if ts:
                    resolved.add(ts)
            except json.JSONDecodeError:
                continue
    return resolved


def _count_incidents_open_p0p1(
    *, since_iso: str | None = None, book_filter: str | None = None
) -> int:
    """Count open P0/P1/critical incidents, optionally scoped by time + book.

    Filters (dans l'ordre):
      1. Exclus les incidents listes dans data/incidents/resolutions.jsonl
         (incidents explicitement fermes par resolution manifest).
      2. Phase 3.2 2026-04-22: TTL 72h (core.governance.incidents_ttl).
         Un incident > 72h SANS re-trigger dans la fenetre est auto-exclu
         (considere resolu par epuisement naturel).
      3. since_iso: si fourni, exclus les incidents avant cette date.
      4. book_filter: si fourni, exclus les incidents d'un autre book.
    """
    incidents_dir = ROOT / "data" / "incidents"
    if not incidents_dir.exists():
        return 0
    resolved_ts = _load_resolved_incidents()
    raw = []
    for p in incidents_dir.glob("*.jsonl"):
        if p.name == "resolutions.jsonl":
            continue
        for entry in _load_jsonl(p):
            sev = (entry.get("severity") or "").upper()
            status = (entry.get("status") or "").lower()
            if sev not in ("P0", "P1", "CRITICAL"):
                continue
            if status not in ("open", ""):
                continue
            ts = entry.get("timestamp") or ""
            if ts in resolved_ts:
                continue
            raw.append(entry)

    # Phase 3.2: TTL 72h auto-exclusion
    try:
        from core.governance.incidents_ttl import filter_active_incidents
        active = filter_active_incidents(raw)
    except ImportError:
        # fallback si module absent (env minimal)
        active = [dict(e, _ts_parsed=None) for e in raw]

    count = 0
    for entry in active:
        ts = entry.get("timestamp") or ""
        if since_iso and ts and ts < since_iso:
            continue
        if book_filter:
            ctx_book = (entry.get("context") or {}).get("book")
            if ctx_book and ctx_book != book_filter:
                continue
        count += 1
    return count


def _compute_trade_stats(journal: list[dict]) -> tuple[int, float, float]:
    closed = [t for t in journal if t.get("exit_price") is not None or t.get("action") == "close"]
    trade_count = len(closed)
    pnl_net = sum(float(t.get("pnl_after_cost", t.get("realized_pnl_usd", 0.0)) or 0.0) for t in closed)
    wr = None
    if trade_count > 0:
        wins = [t for t in closed if float(t.get("pnl_after_cost", t.get("realized_pnl_usd", 0.0)) or 0.0) > 0]
        wr = len(wins) / trade_count
    return trade_count, pnl_net, wr if wr is not None else 0.0


def _compute_max_dd_pct(equity_curve: list[float]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak * 100
            if dd > max_dd:
                max_dd = dd
    return max_dd


def _build_equity_curve(journal: list[dict]) -> list[float]:
    curve: list[float] = []
    cumulative = 0.0
    for entry in journal:
        cum = entry.get("cumulative_pnl_usd")
        if cum is not None:
            curve.append(float(cum))
            cumulative = float(cum)
        else:
            pnl = float(entry.get("pnl_after_cost", entry.get("realized_pnl_usd", 0.0)) or 0.0)
            cumulative += pnl
            curve.append(cumulative)
    return curve


def _divergence_sigmas(paper: float, backtest_mean: float, backtest_std: float) -> float:
    if backtest_std is None or backtest_std <= 0:
        return 0.0
    return abs(paper - backtest_mean) / backtest_std


def compute_metrics(
    strategy_id: str,
    min_days: int = DEFAULT_MIN_DAYS,
) -> GateMetrics:
    entry = _get_quant_entry(strategy_id)
    paper_start = _paper_start_at(entry)
    paper_days = 0
    if paper_start:
        try:
            start_dt = datetime.fromisoformat(paper_start)
            paper_days = (datetime.now(timezone.utc).date() - start_dt.date()).days
        except ValueError:
            paper_days = 0

    journal_path = _find_paper_journal(strategy_id)
    journal = _load_jsonl(journal_path) if journal_path else []

    trade_count, paper_pnl_net, paper_wr = _compute_trade_stats(journal)
    equity_curve = _build_equity_curve(journal)
    paper_max_dd_pct = _compute_max_dd_pct(equity_curve)

    backtest = _find_backtest_manifest(strategy_id)
    div_sigmas: dict[str, float] = {}
    paper_sharpe: float | None = None
    if backtest:
        metrics_bt = backtest.get("metrics", {}) or backtest.get("baseline", {})
        bt_sharpe = metrics_bt.get("sharpe")
        bt_sharpe_std = metrics_bt.get("sharpe_std") or 0.5
        bt_pnl_mean = metrics_bt.get("pnl_expected_for_period") or metrics_bt.get("pnl_mean")
        bt_pnl_std = metrics_bt.get("pnl_std") or max(1.0, abs(bt_pnl_mean or 0.0) * 0.3)
        bt_wr = metrics_bt.get("wr")
        bt_wr_std = metrics_bt.get("wr_std") or 0.10

        if bt_sharpe is not None and trade_count >= 5:
            paper_sharpe = _compute_paper_sharpe(journal)
            div_sigmas["sharpe"] = _divergence_sigmas(paper_sharpe, bt_sharpe, bt_sharpe_std)
        if bt_pnl_mean is not None:
            div_sigmas["pnl"] = _divergence_sigmas(paper_pnl_net, bt_pnl_mean, bt_pnl_std)
        if bt_wr is not None and trade_count >= 5:
            div_sigmas["wr"] = _divergence_sigmas(paper_wr, bt_wr, bt_wr_std)

    incidents_open = _count_incidents_open_p0p1(
        since_iso=paper_start,
        book_filter=(entry or {}).get("book") if entry else None,
    )

    verdict, reasons, recommendation = _evaluate(
        paper_days=paper_days,
        trade_count=trade_count,
        paper_pnl_net=paper_pnl_net,
        paper_max_dd_pct=paper_max_dd_pct,
        div_sigmas=div_sigmas,
        incidents_open=incidents_open,
        journal_found=journal_path is not None,
        min_days=min_days,
    )

    return GateMetrics(
        strategy_id=strategy_id,
        paper_start_at=paper_start,
        paper_days=paper_days,
        trade_count=trade_count,
        paper_pnl_net=round(paper_pnl_net, 2),
        paper_max_dd_pct=round(paper_max_dd_pct, 2),
        paper_sharpe=(round(paper_sharpe, 3) if paper_sharpe is not None else None),
        paper_wr=round(paper_wr, 3) if paper_wr is not None else None,
        div_sigmas={k: round(v, 2) for k, v in div_sigmas.items()},
        incidents_open_p0p1=incidents_open,
        verdict=verdict,
        reasons=reasons,
        recommendation=recommendation,
        checked_at=datetime.now(timezone.utc).isoformat() + "Z",
    )


def _compute_paper_sharpe(journal: list[dict]) -> float | None:
    returns = []
    for entry in journal:
        r = entry.get("daily_return") or entry.get("realized_pnl_usd")
        if r is not None:
            try:
                returns.append(float(r))
            except (TypeError, ValueError):
                continue
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    var = sum((x - mean) ** 2 for x in returns) / (len(returns) - 1)
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    return mean / std * math.sqrt(252)


def _evaluate(
    *,
    paper_days: int,
    trade_count: int,
    paper_pnl_net: float,
    paper_max_dd_pct: float,
    div_sigmas: dict,
    incidents_open: int,
    journal_found: bool,
    min_days: int,
) -> tuple[str, list, str]:
    reasons: list[str] = []
    max_div = max(div_sigmas.values()) if div_sigmas else 0.0

    if not journal_found:
        reasons.append("paper_journal_missing")
        return (
            "NO_GO_paper_journal_missing",
            reasons,
            "Paper journal absent. Verifier que le paper runner ecrit bien sur VPS.",
        )
    if paper_days < min_days:
        reasons.append(f"paper_days={paper_days} < {min_days}")
        return (
            "NO_GO_paper_too_short",
            reasons,
            f"Continuer paper {min_days - paper_days}j supplementaires.",
        )
    if paper_max_dd_pct > 8.0:
        reasons.append(f"max_dd={paper_max_dd_pct:.2f}% > 8%")
        return ("NO_GO_drawdown_exceeded", reasons, "Pas de deposit. Revue strat + re-WF.")
    if incidents_open > 0:
        reasons.append(f"incidents_open_p0p1={incidents_open}")
        return ("NO_GO_incident_open", reasons, "Fermer incidents P0/P1 avant re-evaluation.")
    if max_div > DEFAULT_MAX_DIV_SIGMA_NOGO:
        reasons.append(f"divergence_max={max_div:.2f}sigma > {DEFAULT_MAX_DIV_SIGMA_NOGO}")
        return ("NO_GO_divergence_critical", reasons, "Paper diverge du backtest. Revue strat.")

    if trade_count < DEFAULT_MIN_TRADES:
        reasons.append(f"trade_count={trade_count} < {DEFAULT_MIN_TRADES}")
        return (
            "WATCH_trade_count_low",
            reasons,
            f"Continuer paper, attendre {DEFAULT_MIN_TRADES - trade_count} trades fermes supplementaires.",
        )
    if max_div > DEFAULT_MAX_DIV_SIGMA_GO:
        reasons.append(f"divergence_max={max_div:.2f}sigma > {DEFAULT_MAX_DIV_SIGMA_GO}")
        return (
            "WATCH_divergence_elevated",
            reasons,
            "Divergence paper/backtest elevee. Continuer 15j supplementaires.",
        )
    if paper_pnl_net < 0:
        reasons.append(f"paper_pnl_net={paper_pnl_net:.2f} < 0")
        return (
            "WATCH_pnl_negative",
            reasons,
            "PnL paper negatif. Continuer 15j, revue edge si persiste.",
        )
    if paper_max_dd_pct > DEFAULT_MAX_DD_PCT:
        reasons.append(f"max_dd={paper_max_dd_pct:.2f}% > {DEFAULT_MAX_DD_PCT}%")
        return (
            "WATCH_drawdown_elevated",
            reasons,
            "Drawdown paper superieur a cible. Surveillance accrue.",
        )

    reasons.append("all_conditions_met")
    return (
        "GO_25K",
        reasons,
        "Depot $25K Alpaca recommande. Promotion us_sector_ls_40_5 vers live_probation autorisee.",
    )


def _print_report(m: GateMetrics) -> None:
    print("=" * 72)
    print(f"  Alpaca Go / No-Go 25K Gate   strategy={m.strategy_id}")
    print("=" * 72)
    print(f"  Paper start    : {m.paper_start_at or 'n/a'}")
    print(f"  Paper days     : {m.paper_days}")
    print(f"  Trades ferme   : {m.trade_count}")
    print(f"  PnL net paper  : ${m.paper_pnl_net:,.2f}")
    print(f"  Max DD paper   : {m.paper_max_dd_pct:.2f}%")
    if m.paper_sharpe is not None:
        print(f"  Sharpe paper   : {m.paper_sharpe:.3f}")
    if m.paper_wr is not None:
        print(f"  WR paper       : {m.paper_wr:.1%}")
    if m.div_sigmas:
        print(f"  Divergences    : {m.div_sigmas}")
    print(f"  Incidents P0/P1: {m.incidents_open_p0p1}")
    print("-" * 72)
    print(f"  Verdict        : {m.verdict}")
    print(f"  Reasons        : {', '.join(m.reasons)}")
    print(f"  Recommendation : {m.recommendation}")
    print("=" * 72)


def main() -> int:
    parser = argparse.ArgumentParser(description="Alpaca Go/No-Go 25K gate")
    parser.add_argument("--strategy", default="us_sector_ls_40_5", help="strategy_id canonique")
    parser.add_argument("--min-days", type=int, default=DEFAULT_MIN_DAYS)
    parser.add_argument("--json", action="store_true", help="JSON output (machine-readable)")
    args = parser.parse_args()

    metrics = compute_metrics(args.strategy, min_days=args.min_days)

    if args.json:
        print(json.dumps(asdict(metrics), indent=2))
    else:
        _print_report(metrics)

    if metrics.verdict == "GO_25K":
        return 0
    if metrics.verdict.startswith("WATCH"):
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
