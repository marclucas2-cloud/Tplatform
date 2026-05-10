"""Audit signaux paper vs backtest pour mes_mr_vix_spike.

Usage:
    python scripts/audit/audit_mes_mr_vix_spike_paper.py [--start YYYY-MM-DD] [--end YYYY-MM-DD]

Genere un rapport comparatif entre les signaux observes en paper (journal
JSONL) et les signaux qu'aurait emis la strategie sur la meme fenetre.

Decision rule pour promotion live_probation :
  - Match >= 90% des signaux (date + side) -> GO
  - Match < 90% mais > 70% -> revue manuelle (regarder pourquoi divergent)
  - Match < 70% -> NO GO (divergence trop forte, retravailler)

Window par defaut : paper_start_at -> today (J+30 cible 2026-05-23).

REFACTOR 2026-05-10 v2 : le replay implemente EXACTEMENT la logique de la
classe MESMeanReversionVIXSpike.on_bar() :
  1. 3 bougies consecutives "rouges" (close < open) sur MES
  2. VIX close > 15 (vix_min)
Pas de pct_change ni autre proxy. Match strict avec le runtime.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

PAPER_START_DEFAULT = "2026-04-23"
JOURNAL_PATH = ROOT / "data" / "state" / "mes_mr_vix_spike" / "journal.jsonl"

UTC = timezone.utc


def _load_paper_journal() -> list[dict]:
    """Read paper journal entries, oldest first."""
    if not JOURNAL_PATH.exists():
        print(f"[ERROR] Journal not found: {JOURNAL_PATH}", file=sys.stderr)
        return []
    out: list[dict] = []
    with JOURNAL_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _replay_backtest(start: date, end: date) -> list[dict]:
    """Replay strategy en utilisant EXACTEMENT la logique de
    MESMeanReversionVIXSpike.on_bar() :
      1. consec_days bougies consecutives "rouges" (close < open) sur MES
      2. VIX > vix_min sur le bar courant

    Retourne la liste des dates ou un BUY aurait ete emis.
    """
    # Note: WF manifest mentionne MES_LONG.parquet mais le runtime utilise
    # MES_1D.parquet (yfinance daily). Meme source effective.
    mes_path = ROOT / "data" / "futures" / "MES_1D.parquet"
    vix_path = ROOT / "data" / "futures" / "VIX_1D.parquet"
    if not mes_path.exists() or not vix_path.exists():
        print(f"[ERROR] MES_1D.parquet or VIX_1D.parquet missing in data/futures/",
              file=sys.stderr)
        return []

    # Strat config : consec=3, hold=4, vix_min=15 (cf manifest WF backfill)
    consec_days = 3
    vix_min = 15.0

    def _normalize(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"]).dt.date
        elif "Date" in df.columns:
            df["date"] = pd.to_datetime(df["Date"]).dt.date
        else:
            idx = pd.to_datetime(df.index, errors="coerce")
            df["date"] = idx.date
        return df

    mes = _normalize(pd.read_parquet(mes_path))
    vix = _normalize(pd.read_parquet(vix_path))

    # On a besoin de regarder les `consec_days` bougies precedant chaque jour
    # du window, donc on prend un buffer de quelques jours avant `start`.
    buffer_start = start - timedelta(days=consec_days + 5)
    mes = mes[(mes["date"] >= buffer_start) & (mes["date"] <= end)].sort_values("date").reset_index(drop=True)
    vix = vix[(vix["date"] >= buffer_start) & (vix["date"] <= end)].sort_values("date").reset_index(drop=True)

    if len(mes) == 0:
        print(f"[WARN] No MES bars in window {start}..{end}", file=sys.stderr)
        return []

    vix_indexed = vix.set_index("date")["close"]

    signals: list[dict] = []
    # Pour chaque bar dans la fenetre, verifier la condition (consec rouges + VIX>15)
    for i in range(consec_days, len(mes)):
        row = mes.iloc[i]
        d = row["date"]
        # Skip les bars hors window
        if d < start or d > end:
            continue
        # Bougies rouges : les `consec_days` PRECEDENTES + la bougie courante ?
        # La classe utilise data_feed.get_bars(consec_days+1).tail(consec_days),
        # donc c'est les 3 dernieres bougies INCLUANT le bar courant.
        recent = mes.iloc[i - consec_days + 1: i + 1]
        all_red = bool((recent["close"] < recent["open"]).all())
        if not all_red:
            continue
        vix_close = vix_indexed.get(d, None)
        if vix_close is None or float(vix_close) <= vix_min:
            continue
        signals.append({
            "date": d.isoformat(),
            "side": "BUY",
            "mes_close": float(row["close"]),
            "vix_close": float(vix_close),
            "consec_days_red": consec_days,
        })
    return signals


def _extract_paper_signals(journal: list[dict], start: date, end: date) -> list[dict]:
    """Extract signal_emit events from paper journal in window."""
    out: list[dict] = []
    for ev in journal:
        if ev.get("event") != "signal_emit":
            continue
        bar_ts_raw = ev.get("bar_ts", "")
        try:
            d = pd.to_datetime(bar_ts_raw).date()
        except Exception:
            continue
        if not (start <= d <= end):
            continue
        out.append({
            "date": d.isoformat(),
            "side": ev.get("side", "?"),
            "bar_close": ev.get("bar_close"),
            "ts_utc": ev.get("ts_utc"),
        })
    return out


def _compute_match_pct(backtest: list[dict], paper: list[dict]) -> tuple[float, dict]:
    """Match by (date, side). Returns (match_pct, details)."""
    bt_set = {(s["date"], s["side"]) for s in backtest}
    pp_set = {(s["date"], s["side"]) for s in paper}
    if not bt_set and not pp_set:
        return 1.0, {"no_signals_either_side": True}
    intersection = bt_set & pp_set
    union = bt_set | pp_set
    match = len(intersection) / max(len(union), 1)
    return match, {
        "backtest_only": sorted(bt_set - pp_set),
        "paper_only": sorted(pp_set - bt_set),
        "common": sorted(intersection),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=PAPER_START_DEFAULT,
                        help=f"Paper window start (default {PAPER_START_DEFAULT})")
    parser.add_argument("--end", default=None,
                        help="Paper window end (default today)")
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else datetime.now(UTC).date()

    print(f"=== AUDIT mes_mr_vix_spike paper vs backtest ===")
    print(f"Window: {start} -> {end} ({(end - start).days} days)")
    print()

    journal = _load_paper_journal()
    paper_sigs = _extract_paper_signals(journal, start, end)
    bt_sigs = _replay_backtest(start, end)

    print(f"Paper journal entries in window : {len([e for e in journal if 'bar_ts' in e and start <= pd.to_datetime(e['bar_ts']).date() <= end])}")
    print(f"Paper signals (signal_emit)     : {len(paper_sigs)}")
    print(f"Backtest signals (replay)       : {len(bt_sigs)}")
    print()

    match_pct, detail = _compute_match_pct(bt_sigs, paper_sigs)
    print(f"Match percentage: {match_pct:.1%}")
    if detail.get("no_signals_either_side"):
        print("  -> Both backtest and paper produced 0 signals (cadence basse OK)")
    else:
        print(f"  Common signals       : {len(detail['common'])}")
        for s in detail["common"]:
            print(f"    {s[0]} {s[1]}")
        if detail["backtest_only"]:
            print(f"  Backtest-only (PAPER MISSED) : {len(detail['backtest_only'])}")
            for s in detail["backtest_only"][:10]:
                print(f"    {s[0]} {s[1]} [WARN: paper did not emit]")
        if detail["paper_only"]:
            print(f"  Paper-only (BACKTEST MISSED) : {len(detail['paper_only'])}")
            for s in detail["paper_only"][:10]:
                print(f"    {s[0]} {s[1]} [INFO: paper emitted but backtest did not]")

    print()
    if (end - start).days < 30:
        print(f"[WARN] Window only {(end - start).days}j < 30j minimum. Audit incomplet pour decision live.")
    elif match_pct >= 0.90:
        print("VERDICT: GO -> match >= 90%, promote a live_probation possible.")
    elif match_pct >= 0.70:
        print("VERDICT: REVIEW -> match 70-90%, investiguer divergences avant promotion.")
    else:
        print("VERDICT: NO GO -> match < 70%, divergence trop forte. Audit du runtime requis.")


if __name__ == "__main__":
    main()
