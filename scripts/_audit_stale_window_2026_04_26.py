#!/usr/bin/env python3
"""Audit retroactif fenetre stale 2026-03-27 -> 2026-04-24.

Bug: 4 parquets _1D corrompus (MES/MNQ/MGC/MCL) + VIX hors cron, depuis
~2026-03-27 (MGC/MCL) et 2026-04-08 (MES/MNQ).

Question audit: pour chaque jour ouvre dans la fenetre, qu auraient decide
les sleeves desk SI elles avaient vu data fresh vs ce qu elles ont decide
sur data stale (logs reels) ?

Sleeves auditees:
  - cross_asset_momentum (CAM, live_core)  : top-1 sur 20d momentum
  - gold_oil_rotation    (GOR, live_core)  : rotation MGC/MCL sur 20d momentum
  - gold_trend_mgc       (paper)            : long MGC si close > EMA20
  - mcl_overnight_mon_trend10 (paper)       : monday long MCL si trend
  - mes_monday/wednesday_long_oc (paper)    : day-of-week effects

Sortie:
  reports/audit/stale_window_2026_04_26_findings.md
  reports/audit/stale_window_2026_04_26_divergences.json
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# Add project root for imports
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.worker.cycles.futures_runner import _load_futures_daily_frame  # noqa: E402

REPORT_DIR = ROOT / "reports" / "audit"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# Fenetre stale: depuis le bug le plus ancien (MGC/MCL 27/03) jusqu'au fix (24/04)
WINDOW_START = pd.Timestamp("2026-03-27")
WINDOW_END = pd.Timestamp("2026-04-24")

# Quelle data les sleeves ont VRAIMENT vue chaque jour de cycle ?
# Reproduction: l'ancien loader truncated MES/MNQ a 2026-04-08 et MGC/MCL a 2026-03-27.
# A partir du moment ou ces files etaient corrompus, la sleeve voyait au plus la
# derniere date du fichier corrompu.
STALE_LAST_DATES = {
    "MES": pd.Timestamp("2026-04-08"),
    "MNQ": pd.Timestamp("2026-04-08"),
    "MGC": pd.Timestamp("2026-03-27"),
    "MCL": pd.Timestamp("2026-03-27"),
    "M2K": None,  # pas de corruption (pas de col datetime)
    "VIX": pd.Timestamp("2026-04-09"),  # hors cron, last refresh 09/04
}


def load_fresh_data() -> dict[str, pd.DataFrame]:
    """Load fresh data post-fix (MES/MNQ/M2K/MGC/MCL/VIX)."""
    data_dir = ROOT / "data" / "futures"
    out = {}
    for sym in ["MES", "MNQ", "M2K", "MGC", "MCL", "VIX"]:
        fpath = data_dir / f"{sym}_1D.parquet"
        if fpath.exists():
            out[sym] = _load_futures_daily_frame(fpath)
    return out


def view_at_date(
    df: pd.DataFrame, decision_date: pd.Timestamp, stale_last: pd.Timestamp | None
) -> pd.DataFrame:
    """Retourne le DataFrame tel que la sleeve l a vu a `decision_date`.

    Si stale_last est None: pas de corruption, voit jusqu'a decision_date - 1.
    Si stale_last est defini: voit jusqu'a min(decision_date - 1, stale_last).
    """
    if stale_last is None:
        cap = decision_date - pd.Timedelta(days=1)
    else:
        cap = min(decision_date - pd.Timedelta(days=1), stale_last)
    return df[df.index <= cap]


def view_fresh_at_date(df: pd.DataFrame, decision_date: pd.Timestamp) -> pd.DataFrame:
    """Retourne le DataFrame tel qu'il aurait du etre vu (data fresh)."""
    return df[df.index <= decision_date - pd.Timedelta(days=1)]


# ============================================================================
# CAM logic recomputed
# ============================================================================
def cam_top_pick(prices: dict[str, pd.Series], lookback: int = 20, min_mom: float = 0.02) -> str | None:
    """CAM: pick symbole with best 20d cumulative return >= min_mom.
    prices: dict symbol -> Series of close (last value = decision view)
    """
    if not prices:
        return None
    moms = {}
    for sym, ser in prices.items():
        if len(ser) < lookback + 1:
            continue
        cum = (1 + ser.pct_change()).rolling(lookback).apply(np.prod, raw=True).iloc[-1] - 1
        if pd.notna(cum):
            moms[sym] = cum
    if not moms:
        return None
    top_sym, top_mom = max(moms.items(), key=lambda kv: kv[1])
    if top_mom < min_mom:
        return None
    return top_sym


def gor_pick(mgc: pd.Series, mcl: pd.Series, lookback: int = 20) -> str | None:
    """GOR: pick MGC ou MCL whichever has higher 20d cumulative return."""
    if len(mgc) < lookback + 1 or len(mcl) < lookback + 1:
        return None
    mgc_mom = (1 + mgc.pct_change()).rolling(lookback).apply(np.prod, raw=True).iloc[-1] - 1
    mcl_mom = (1 + mcl.pct_change()).rolling(lookback).apply(np.prod, raw=True).iloc[-1] - 1
    if pd.isna(mgc_mom) or pd.isna(mcl_mom):
        return None
    return "MGC" if mgc_mom > mcl_mom else "MCL"


def gold_trend_signal(mgc: pd.Series, ema_period: int = 20) -> bool:
    """gold_trend_mgc: return True if close > EMA20."""
    if len(mgc) < ema_period + 1:
        return False
    ema = mgc.ewm(span=ema_period, adjust=False).mean()
    return bool(mgc.iloc[-1] > ema.iloc[-1])


# ============================================================================
# Audit driver
# ============================================================================
def run_audit() -> dict:
    fresh = load_fresh_data()
    if not fresh or "MES" not in fresh:
        return {"error": "fresh data unavailable"}

    # Generate business days in window
    bdays = pd.bdate_range(WINDOW_START, WINDOW_END)
    print(f"Auditing {len(bdays)} business days in {WINDOW_START.date()} -> {WINDOW_END.date()}")

    cam_records = []
    gor_records = []
    gold_records = []

    for d in bdays:
        # CAM
        cam_universe = ["MES", "MNQ", "M2K", "MGC", "MCL"]
        stale_views = {}
        fresh_views = {}
        for sym in cam_universe:
            stale_views[sym] = view_at_date(fresh[sym], d, STALE_LAST_DATES.get(sym))["close"]
            fresh_views[sym] = view_fresh_at_date(fresh[sym], d)["close"]

        cam_stale = cam_top_pick(stale_views)
        cam_fresh = cam_top_pick(fresh_views)
        cam_records.append({
            "date": str(d.date()),
            "stale_pick": cam_stale,
            "fresh_pick": cam_fresh,
            "divergent": cam_stale != cam_fresh,
        })

        # GOR
        mgc_stale = view_at_date(fresh["MGC"], d, STALE_LAST_DATES["MGC"])["close"]
        mcl_stale = view_at_date(fresh["MCL"], d, STALE_LAST_DATES["MCL"])["close"]
        mgc_fresh = view_fresh_at_date(fresh["MGC"], d)["close"]
        mcl_fresh = view_fresh_at_date(fresh["MCL"], d)["close"]

        gor_stale = gor_pick(mgc_stale, mcl_stale)
        gor_fresh = gor_pick(mgc_fresh, mcl_fresh)
        gor_records.append({
            "date": str(d.date()),
            "stale_pick": gor_stale,
            "fresh_pick": gor_fresh,
            "divergent": gor_stale != gor_fresh,
        })

        # gold_trend_mgc
        gold_stale_signal = gold_trend_signal(mgc_stale)
        gold_fresh_signal = gold_trend_signal(mgc_fresh)
        gold_records.append({
            "date": str(d.date()),
            "stale_long": gold_stale_signal,
            "fresh_long": gold_fresh_signal,
            "divergent": gold_stale_signal != gold_fresh_signal,
        })

    # Summary
    cam_divs = sum(1 for r in cam_records if r["divergent"])
    gor_divs = sum(1 for r in gor_records if r["divergent"])
    gold_divs = sum(1 for r in gold_records if r["divergent"])

    summary = {
        "window": f"{WINDOW_START.date()} -> {WINDOW_END.date()}",
        "n_business_days": len(bdays),
        "stale_data_picture": {sym: str(d) if d else "fresh" for sym, d in STALE_LAST_DATES.items()},
        "CAM": {
            "n_divergent_days": cam_divs,
            "divergence_pct": round(cam_divs / len(bdays) * 100, 1),
            "records": cam_records,
        },
        "GOR": {
            "n_divergent_days": gor_divs,
            "divergence_pct": round(gor_divs / len(bdays) * 100, 1),
            "records": gor_records,
        },
        "gold_trend_mgc": {
            "n_divergent_days": gold_divs,
            "divergence_pct": round(gold_divs / len(bdays) * 100, 1),
            "records": gold_records,
        },
    }
    return summary


def main():
    audit = run_audit()
    if "error" in audit:
        print(f"AUDIT FAILED: {audit['error']}")
        return 1

    out_json = REPORT_DIR / "stale_window_2026_04_26_divergences.json"
    out_json.write_text(json.dumps(audit, indent=2, default=str), encoding="utf-8")

    # Print summary
    print(f"\n=== AUDIT SUMMARY ({audit['window']}, {audit['n_business_days']} business days) ===")
    for sleeve in ["CAM", "GOR", "gold_trend_mgc"]:
        r = audit[sleeve]
        print(f"\n{sleeve}: {r['n_divergent_days']} jours divergents ({r['divergence_pct']}%)")
        # Print divergent days only
        divs = [rec for rec in r["records"] if rec["divergent"]]
        for rec in divs[:10]:
            print(f"  {rec['date']}: stale={rec.get('stale_pick', rec.get('stale_long'))} vs fresh={rec.get('fresh_pick', rec.get('fresh_long'))}")
        if len(divs) > 10:
            print(f"  ... and {len(divs) - 10} more")

    print(f"\nFull JSON: {out_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
