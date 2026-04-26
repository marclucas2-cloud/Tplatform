#!/usr/bin/env python3
"""Mission 2: chiffrer l'impact economique de la fenetre stale 27/03 -> 24/04.

Sources:
  - data/futures/*_1D.parquet (fresh post-fix, contient l'historique complet)
  - reports/audit/stale_window_2026_04_26_divergences.json (qui devait picker quoi)
  - State files / journal (si disponibles)

Calcul: pour chaque jour divergent, simuler le PnL hypothetique fresh vs
ce qui s'est probablement passe stale, sur 1 contract.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.data.parquet_safe_loader import load_daily_parquet_safe  # noqa: E402

REPORT_DIR = ROOT / "reports" / "checkup"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# Multipliers (futures USD per point per contract)
MULTIPLIERS = {
    "MES": 5,    # S&P 500 micro
    "MNQ": 2,    # Nasdaq micro
    "M2K": 5,    # Russell micro
    "MGC": 10,   # Gold micro
    "MCL": 100,  # Crude oil micro (10 barrels * $0.10/tick * 10 ticks/$)
}

WINDOW_START = pd.Timestamp("2026-03-27")
WINDOW_END = pd.Timestamp("2026-04-24")


def load_fresh() -> dict[str, pd.DataFrame]:
    out = {}
    for sym in ["MES", "MNQ", "M2K", "MGC", "MCL", "VIX"]:
        path = ROOT / "data" / "futures" / f"{sym}_1D.parquet"
        if path.exists():
            out[sym] = load_daily_parquet_safe(path)
    return out


def cam_pnl_simulation(fresh: dict, divergences_json: dict) -> dict:
    """Simule PnL CAM si l'on avait pris le pick fresh chaque jour vs stale.

    Approche: pour chaque jour de la fenetre, regarder quelle position CAM
    aurait detenu (stale_pick vs fresh_pick), accumulate daily PnL sur
    1 contract du symbol pické.
    """
    cam_records = divergences_json["CAM"]["records"]
    daily_pnls = []
    for rec in cam_records:
        d = pd.Timestamp(rec["date"])
        stale = rec["stale_pick"]
        fresh = rec["fresh_pick"]
        if stale == fresh:
            continue
        # Lendemain ouvre, on accumule le pnl de la journee = (close[d+1] - close[d]) * mult * 1 contract
        # Pour la simu on prend (close[d] - close[d-1]) si position detenue le jour d
        for sym in [stale, fresh]:
            if sym is None or sym not in MULTIPLIERS:
                continue
            df = (load_daily_parquet_safe(ROOT / "data" / "futures" / f"{sym}_1D.parquet")
                  if sym not in ["MGC", "MCL", "MES", "MNQ", "M2K"] else None)
        # Compute fresh-pick pnl
        fresh_pnl = 0.0
        stale_pnl = 0.0
        if fresh and fresh in MULTIPLIERS:
            df = load_daily_parquet_safe(ROOT / "data" / "futures" / f"{fresh}_1D.parquet")
            if d in df.index:
                idx_pos = df.index.get_loc(d)
                if idx_pos > 0 and idx_pos < len(df) - 1:
                    # day d held: pnl = close[d+1] - close[d] (overnight)
                    fresh_pnl = (df.iloc[idx_pos + 1]["close"] - df.iloc[idx_pos]["close"]) * MULTIPLIERS[fresh]
        if stale and stale in MULTIPLIERS:
            df = load_daily_parquet_safe(ROOT / "data" / "futures" / f"{stale}_1D.parquet")
            if d in df.index:
                idx_pos = df.index.get_loc(d)
                if idx_pos > 0 and idx_pos < len(df) - 1:
                    stale_pnl = (df.iloc[idx_pos + 1]["close"] - df.iloc[idx_pos]["close"]) * MULTIPLIERS[stale]
        daily_pnls.append({
            "date": rec["date"],
            "stale_pick": stale,
            "fresh_pick": fresh,
            "stale_overnight_pnl": round(stale_pnl, 2),
            "fresh_overnight_pnl": round(fresh_pnl, 2),
            "missed_or_gained_by_stale": round(stale_pnl - fresh_pnl, 2),
        })

    total_missed = sum(r["missed_or_gained_by_stale"] for r in daily_pnls)
    return {
        "n_divergent_days_simulated": len(daily_pnls),
        "total_pnl_diff_stale_minus_fresh_usd": round(total_missed, 2),
        "interpretation": (
            "Negatif = stale CAM aurait perdu vs fresh; Positif = stale CAM aurait gagne plus vs fresh."
        ),
        "daily": daily_pnls,
    }


def gor_pnl_simulation(fresh: dict, divergences_json: dict) -> dict:
    """GOR: rotation MGC/MCL. Calcul du PnL si l'on tient le stale_pick vs fresh_pick.

    GOR signal etait dormant (pas de fill reel) - donc le manque a gagner est
    'l'ecart entre la position que GOR aurait dû tenir si signal vivant et la
    position figee'.
    """
    gor_records = divergences_json["GOR"]["records"]
    daily = []
    mgc = load_daily_parquet_safe(ROOT / "data" / "futures" / "MGC_1D.parquet")
    mcl = load_daily_parquet_safe(ROOT / "data" / "futures" / "MCL_1D.parquet")
    for rec in gor_records:
        if not rec["divergent"]:
            continue
        d = pd.Timestamp(rec["date"])
        stale = rec["stale_pick"]
        fresh = rec["fresh_pick"]
        if stale not in (None, "MGC", "MCL"):
            continue
        if fresh not in (None, "MGC", "MCL"):
            continue
        stale_pnl = 0.0
        fresh_pnl = 0.0
        if stale == "MGC" and d in mgc.index:
            ip = mgc.index.get_loc(d)
            if 0 < ip < len(mgc) - 1:
                stale_pnl = (mgc.iloc[ip + 1]["close"] - mgc.iloc[ip]["close"]) * MULTIPLIERS["MGC"]
        if stale == "MCL" and d in mcl.index:
            ip = mcl.index.get_loc(d)
            if 0 < ip < len(mcl) - 1:
                stale_pnl = (mcl.iloc[ip + 1]["close"] - mcl.iloc[ip]["close"]) * MULTIPLIERS["MCL"]
        if fresh == "MGC" and d in mgc.index:
            ip = mgc.index.get_loc(d)
            if 0 < ip < len(mgc) - 1:
                fresh_pnl = (mgc.iloc[ip + 1]["close"] - mgc.iloc[ip]["close"]) * MULTIPLIERS["MGC"]
        if fresh == "MCL" and d in mcl.index:
            ip = mcl.index.get_loc(d)
            if 0 < ip < len(mcl) - 1:
                fresh_pnl = (mcl.iloc[ip + 1]["close"] - mcl.iloc[ip]["close"]) * MULTIPLIERS["MCL"]
        daily.append({
            "date": rec["date"],
            "stale": stale,
            "fresh": fresh,
            "stale_pnl_usd": round(stale_pnl, 2),
            "fresh_pnl_usd": round(fresh_pnl, 2),
            "missed_by_stale_usd": round(fresh_pnl - stale_pnl, 2),
        })
    total = sum(r["missed_by_stale_usd"] for r in daily)
    return {
        "n_divergent_days": len(daily),
        "total_missed_usd": round(total, 2),
        "interpretation": (
            "Manque a gagner si GOR avait pu switch (signal vivant) vers fresh_pick. "
            "GOR signal dormant -> en pratique, le worker n'a pas tenu de position GOR pendant la fenetre. "
            "Borne supérieure du manque a gagner."
        ),
        "daily": daily,
    }


def gold_trend_pnl_simulation(fresh: dict, divergences_json: dict) -> dict:
    """gold_trend_mgc: long MGC si > EMA20. Sur fenetre stale = 10 jours longs manques.
    Calcul: si on avait tenu MGC long 10 jours fresh vs aucune position, gain estime.
    """
    gold = divergences_json["gold_trend_mgc"]["records"]
    mgc = load_daily_parquet_safe(ROOT / "data" / "futures" / "MGC_1D.parquet")
    daily = []
    for rec in gold:
        if not rec["divergent"]:
            continue
        d = pd.Timestamp(rec["date"])
        # stale = False (no long), fresh = True (long)
        # Manque a gagner = pnl overnight long MGC ce jour-la
        if d in mgc.index:
            ip = mgc.index.get_loc(d)
            if 0 < ip < len(mgc) - 1:
                pnl = (mgc.iloc[ip + 1]["close"] - mgc.iloc[ip]["close"]) * MULTIPLIERS["MGC"]
                daily.append({
                    "date": rec["date"],
                    "missed_long_pnl_usd": round(pnl, 2),
                })
    total = sum(r["missed_long_pnl_usd"] for r in daily)
    return {
        "n_missed_long_days": len(daily),
        "total_missed_paper_pnl_usd": round(total, 2),
        "interpretation": (
            "PnL paper hypothetique manque par gold_trend_mgc (long-only, 1 contract MGC). "
            "Pas de PnL reel: sleeve paper_only."
        ),
        "daily": daily,
    }


def main() -> int:
    div_path = ROOT / "reports" / "audit" / "stale_window_2026_04_26_divergences.json"
    if not div_path.exists():
        print(f"FAIL: {div_path} not found - run _audit_stale_window_2026_04_26.py first")
        return 1
    div = json.loads(div_path.read_text(encoding="utf-8"))

    fresh = load_fresh()
    print(f"Fresh data loaded: {list(fresh.keys())}")
    print(f"Window: {WINDOW_START.date()} -> {WINDOW_END.date()}")

    cam = cam_pnl_simulation(fresh, div)
    gor = gor_pnl_simulation(fresh, div)
    gold = gold_trend_pnl_simulation(fresh, div)

    print(f"\n=== CAM ECONOMIC IMPACT ===")
    print(f"Days divergent: {cam['n_divergent_days_simulated']}")
    print(f"Sum (stale-fresh) PnL: ${cam['total_pnl_diff_stale_minus_fresh_usd']}")
    if cam['total_pnl_diff_stale_minus_fresh_usd'] < 0:
        print(f"  -> Stale CAM aurait perdu ~${-cam['total_pnl_diff_stale_minus_fresh_usd']} de plus que fresh")

    print(f"\n=== GOR ECONOMIC IMPACT ===")
    print(f"Days divergent: {gor['n_divergent_days']}")
    print(f"Total missed by stale: ${gor['total_missed_usd']}")
    print(f"  -> {gor['interpretation']}")

    print(f"\n=== gold_trend_mgc PAPER IMPACT ===")
    print(f"Missed long days: {gold['n_missed_long_days']}")
    print(f"Total paper missed: ${gold['total_missed_paper_pnl_usd']}")

    out = REPORT_DIR / "stale_window_economic_impact_2026_04_26.json"
    out.write_text(
        json.dumps({"CAM": cam, "GOR": gor, "gold_trend_mgc": gold}, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\nSaved JSON: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
