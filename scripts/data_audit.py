"""
DATA-001 : Verification qualite donnees

Pour les 7 strategies retenues (4 VALIDATED + 3 BORDERLINE),
charge les donnees Parquet et compare avec yfinance.

Checks :
1. Pas de NaN dans les colonnes OHLCV
2. Pas de splits non ajustes (variation > 30% entre 2 barres)
3. Pas de gaps > 5% entre 2 jours consecutifs (close-to-open)
4. Comparaison close prix avec yfinance sur 10 jours echantillon

Rapport sauvegarde dans output/data_audit_report.md
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CACHE_DIR = ROOT / "archive" / "intraday-backtesterV2" / "data_cache"
OUTPUT_DIR = ROOT / "output"

# Strategies retenues du walk-forward (4 VALIDATED + 3 BORDERLINE)
RETAINED_STRATEGIES = {
    "Day-of-Week Seasonal": {
        "tickers": ["SPY", "QQQ"],
        "verdict": "VALIDATED",
    },
    "Correlation Regime Hedge": {
        "tickers": ["SPY", "TLT", "GLD", "USO"],
        "verdict": "VALIDATED",
    },
    "VIX Expansion Short": {
        "tickers": ["SPY", "QQQ"],
        "verdict": "VALIDATED",
    },
    "High-Beta Underperformance Short": {
        "tickers": ["ARKK", "TSLA", "SPY"],
        "verdict": "VALIDATED",
    },
    "Late Day Mean Reversion": {
        "tickers": ["SPY", "QQQ", "AAPL", "MSFT"],
        "verdict": "BORDERLINE",
    },
    "Failed Rally Short": {
        "tickers": ["SPY", "QQQ"],
        "verdict": "BORDERLINE",
    },
    "EOD Sell Pressure V2": {
        "tickers": ["SPY", "QQQ", "AAPL"],
        "verdict": "BORDERLINE",
    },
}


def find_parquet(ticker: str) -> Path | None:
    """Trouve le fichier parquet le plus recent pour un ticker."""
    candidates = sorted(CACHE_DIR.glob(f"{ticker}_5Min_*.parquet"), reverse=True)
    return candidates[0] if candidates else None


def check_nan(df: pd.DataFrame, ticker: str) -> dict:
    """Verifie les NaN dans les colonnes OHLCV."""
    ohlcv = ["open", "high", "low", "close", "volume"]
    cols = [c for c in ohlcv if c in df.columns]
    nan_counts = {c: int(df[c].isna().sum()) for c in cols}
    total_nan = sum(nan_counts.values())
    return {
        "ticker": ticker,
        "check": "NaN",
        "pass": total_nan == 0,
        "detail": nan_counts,
        "total_rows": len(df),
    }


def check_splits(df: pd.DataFrame, ticker: str) -> dict:
    """Detecte les splits non ajustes (variation > 30% entre 2 barres)."""
    if "close" not in df.columns or len(df) < 2:
        return {"ticker": ticker, "check": "splits", "pass": True, "detail": "Pas assez de donnees"}

    # Calculer les returns barre-a-barre
    daily_close = df.groupby(df.index.date)["close"].last()
    returns = daily_close.pct_change().dropna()
    suspicious = returns[returns.abs() > 0.30]

    return {
        "ticker": ticker,
        "check": "splits",
        "pass": len(suspicious) == 0,
        "detail": {str(d): round(float(r), 4) for d, r in suspicious.items()} if len(suspicious) > 0 else "OK",
        "n_suspicious": len(suspicious),
    }


def check_gaps(df: pd.DataFrame, ticker: str) -> dict:
    """Verifie les gaps > 5% entre close J et open J+1."""
    if "close" not in df.columns or "open" not in df.columns or len(df) < 2:
        return {"ticker": ticker, "check": "gaps", "pass": True, "detail": "Pas assez de donnees"}

    daily_close = df.groupby(df.index.date)["close"].last()
    daily_open = df.groupby(df.index.date)["open"].first()

    # Aligner dates
    dates = sorted(set(daily_close.index) & set(daily_open.index))
    gaps = []
    for i in range(1, len(dates)):
        prev_close = daily_close.loc[dates[i - 1]]
        curr_open = daily_open.loc[dates[i]]
        gap_pct = (curr_open - prev_close) / prev_close
        if abs(gap_pct) > 0.05:
            gaps.append({"date": str(dates[i]), "gap_pct": round(float(gap_pct) * 100, 2)})

    return {
        "ticker": ticker,
        "check": "gaps_5pct",
        "pass": len(gaps) == 0,
        "detail": gaps if gaps else "OK",
        "n_gaps": len(gaps),
    }


def compare_with_yfinance(df: pd.DataFrame, ticker: str, n_days: int = 10) -> dict:
    """Compare les close prix avec yfinance sur n_days echantillon."""
    try:
        import yfinance as yf
    except ImportError:
        return {"ticker": ticker, "check": "yfinance_compare", "pass": None, "detail": "yfinance non installe"}

    # Prendre les 10 derniers jours de trading dans le parquet
    daily_close = df.groupby(df.index.date)["close"].last()
    sample_dates = sorted(daily_close.index)[-n_days:]

    if not sample_dates:
        return {"ticker": ticker, "check": "yfinance_compare", "pass": None, "detail": "Pas de dates"}

    start = str(sample_dates[0])
    end_dt = pd.Timestamp(sample_dates[-1]) + timedelta(days=3)
    end = str(end_dt.date())

    try:
        yf_data = yf.download(ticker, start=start, end=end, interval="1d", progress=False)
        if isinstance(yf_data.columns, pd.MultiIndex):
            yf_data.columns = yf_data.columns.get_level_values(0)
    except Exception as e:
        return {"ticker": ticker, "check": "yfinance_compare", "pass": None, "detail": f"Erreur yfinance: {e}"}

    if yf_data.empty:
        return {"ticker": ticker, "check": "yfinance_compare", "pass": None, "detail": "yfinance vide"}

    yf_close = yf_data["Close"]
    yf_by_date = {d.date() if hasattr(d, "date") else d: float(v)
                  for d, v in yf_close.items()}

    comparisons = []
    max_diff_pct = 0
    for d in sample_dates:
        local = float(daily_close.loc[d])
        yf_val = yf_by_date.get(d)
        if yf_val is not None and yf_val > 0:
            diff_pct = abs(local - yf_val) / yf_val * 100
            max_diff_pct = max(max_diff_pct, diff_pct)
            comparisons.append({
                "date": str(d),
                "local": round(local, 4),
                "yfinance": round(yf_val, 4),
                "diff_pct": round(diff_pct, 3),
            })

    # Pass si ecart < 2% (tolerance pour timezone/split adjustments)
    passed = max_diff_pct < 2.0

    return {
        "ticker": ticker,
        "check": "yfinance_compare",
        "pass": passed,
        "max_diff_pct": round(max_diff_pct, 3),
        "n_compared": len(comparisons),
        "sample": comparisons[:5],  # 5 premiers pour le rapport
    }


def run_audit():
    """Execute l'audit complet et genere le rapport."""
    print("=" * 60)
    print("[DATA AUDIT] Verification qualite donnees")
    print(f"  Strategies auditees: {len(RETAINED_STRATEGIES)}")
    print("=" * 60)

    all_results = {}
    all_tickers = set()
    for strat_name, info in RETAINED_STRATEGIES.items():
        all_tickers.update(info["tickers"])

    ticker_results = {}
    for ticker in sorted(all_tickers):
        print(f"\n  [{ticker}] Recherche parquet...")
        parquet_path = find_parquet(ticker)
        if parquet_path is None:
            print(f"    [SKIP] Pas de fichier parquet pour {ticker}")
            ticker_results[ticker] = {
                "file": None,
                "checks": [{"check": "file_exists", "pass": False, "detail": "Parquet non trouve"}],
            }
            continue

        print(f"    Fichier: {parquet_path.name}")
        df = pd.read_parquet(parquet_path)
        df.columns = [c.lower() for c in df.columns]
        if not isinstance(df.index, pd.DatetimeIndex):
            if "timestamp" in df.columns:
                df.index = pd.to_datetime(df["timestamp"])
            else:
                df.index = pd.to_datetime(df.index)

        n_days = len(set(df.index.date))
        print(f"    Lignes: {len(df)}, Jours: {n_days}")

        checks = []

        # Check NaN
        r = check_nan(df, ticker)
        checks.append(r)
        status = "PASS" if r["pass"] else "FAIL"
        print(f"    NaN: {status}")

        # Check splits
        r = check_splits(df, ticker)
        checks.append(r)
        status = "PASS" if r["pass"] else f"FAIL ({r.get('n_suspicious', 0)} suspects)"
        print(f"    Splits: {status}")

        # Check gaps
        r = check_gaps(df, ticker)
        checks.append(r)
        status = "PASS" if r["pass"] else f"FAIL ({r.get('n_gaps', 0)} gaps >5%)"
        print(f"    Gaps >5%: {status}")

        # Compare yfinance
        r = compare_with_yfinance(df, ticker)
        checks.append(r)
        if r["pass"] is None:
            status = "SKIP"
        elif r["pass"]:
            status = f"PASS (max diff {r.get('max_diff_pct', 0):.3f}%)"
        else:
            status = f"FAIL (max diff {r.get('max_diff_pct', 0):.3f}%)"
        print(f"    yfinance: {status}")

        ticker_results[ticker] = {
            "file": str(parquet_path.name),
            "rows": len(df),
            "days": n_days,
            "checks": checks,
        }

    # ── Generer le rapport Markdown ──
    report = []
    report.append("# Data Audit Report")
    report.append(f"\nDate: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    report.append(f"Strategies auditees: {len(RETAINED_STRATEGIES)}")
    report.append(f"Tickers uniques: {len(all_tickers)}")
    report.append("")

    report.append("## Strategies retenues")
    report.append("")
    report.append("| Strategie | Verdict | Tickers |")
    report.append("|-----------|---------|---------|")
    for name, info in RETAINED_STRATEGIES.items():
        report.append(f"| {name} | {info['verdict']} | {', '.join(info['tickers'])} |")
    report.append("")

    report.append("## Resultats par ticker")
    report.append("")

    total_pass = 0
    total_fail = 0
    total_skip = 0

    for ticker, res in sorted(ticker_results.items()):
        report.append(f"### {ticker}")
        if res.get("file") is None:
            report.append("- **Fichier**: Non trouve")
            total_fail += 1
            report.append("")
            continue

        report.append(f"- **Fichier**: `{res['file']}`")
        report.append(f"- **Lignes**: {res['rows']:,} | **Jours**: {res['days']}")
        report.append("")

        report.append("| Check | Status | Detail |")
        report.append("|-------|--------|--------|")
        for chk in res["checks"]:
            if chk["pass"] is None:
                status = "SKIP"
                total_skip += 1
            elif chk["pass"]:
                status = "PASS"
                total_pass += 1
            else:
                status = "FAIL"
                total_fail += 1

            detail = str(chk.get("detail", ""))
            if len(detail) > 80:
                detail = detail[:77] + "..."
            report.append(f"| {chk['check']} | {status} | {detail} |")
        report.append("")

    report.append("## Resume")
    report.append("")
    report.append(f"- **PASS**: {total_pass}")
    report.append(f"- **FAIL**: {total_fail}")
    report.append(f"- **SKIP**: {total_skip}")
    report.append(f"- **Score**: {total_pass}/{total_pass + total_fail} ({total_pass / max(1, total_pass + total_fail) * 100:.0f}%)")
    report.append("")

    if total_fail == 0:
        report.append("**Conclusion: Toutes les donnees sont propres. Aucune action requise.**")
    else:
        report.append("**Conclusion: Des anomalies ont ete detectees. Verifier les tickers en FAIL.**")

    # Sauvegarder
    report_path = OUTPUT_DIR / "data_audit_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(report), encoding="utf-8")

    print(f"\n{'=' * 60}")
    print(f"[DONE] Rapport sauvegarde: {report_path}")
    print(f"  PASS: {total_pass} | FAIL: {total_fail} | SKIP: {total_skip}")
    print(f"{'=' * 60}")

    return ticker_results


if __name__ == "__main__":
    run_audit()
