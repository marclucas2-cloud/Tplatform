"""Walk-forward validation for US QMJ sector-neutral V1.

Usage:
    python scripts/wf_us_qmj.py
    python scripts/wf_us_qmj.py --max-symbols 80 --refresh-fundamentals

The script uses free data only:
  - yfinance daily adjusted OHLC for prices
  - SEC EDGAR companyfacts for fundamentals when available

The verdict is intentionally mechanical and may be REJECTED if free-data
coverage is too noisy. No live wiring or registry mutation happens here.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.backtest.us_equity_event_driven import CostsConfig, EventDrivenBacktester
from strategies_v2.us.quality_minus_junk import QMJConfig, QualityMinusJunkStrategy

logger = logging.getLogger(__name__)

US_STOCKS_DIR = ROOT / "data" / "us_stocks"
US_RESEARCH_DIR = ROOT / "data" / "us_research"
QMJ_CACHE_DIR = US_RESEARCH_DIR / "qmj"
PRICE_CACHE = QMJ_CACHE_DIR / "prices_2014_2024.parquet"
COMPANY_TICKERS_CACHE = QMJ_CACHE_DIR / "company_tickers.json"
FUNDAMENTALS_DIR = QMJ_CACHE_DIR / "fundamentals"
FACTS_DIR = QMJ_CACHE_DIR / "companyfacts"
MANIFEST_PATH = ROOT / "data" / "research" / "wf_manifests" / "us_qmj_v1_2026-05-15.json"
REJECTED_PATH = ROOT / "data" / "research" / "rejected" / "us_qmj_v1_2026-05-15.json"

SEC_USER_AGENT = "trading-platform-qmj-research/1.0 contact:noreply@example.com"
START_PRICE = "2014-01-01"
END_PRICE = "2024-12-31"


METRIC_TAGS = {
    "total_revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ],
    "cost_of_revenue": [
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
        "CostOfGoodsSold",
        "CostOfRevenueExcludingDepreciationDepletionAndAmortization",
    ],
    "total_assets": ["Assets"],
    "total_stockholder_equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted", "EarningsPerShareBasic"],
}

TOTAL_DEBT_TAGS = [
    "DebtAndFinanceLeaseObligations",
    "LongTermDebtAndFinanceLeaseObligations",
    "LongTermDebt",
]
DEBT_COMPONENT_TAGS = [
    "ShortTermBorrowings",
    "LongTermDebtCurrent",
    "LongTermDebtAndFinanceLeaseObligationsCurrent",
    "LongTermDebtNoncurrent",
    "LongTermDebtAndFinanceLeaseObligationsNoncurrent",
]


def _clean_float(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if isinstance(value, pd.DataFrame):
        return [_clean_float(row) for row in value.to_dict(orient="records")]
    if isinstance(value, dict):
        return {k: _clean_float(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean_float(v) for v in value]
    return value


def load_universe(max_symbols: int | None = None) -> tuple[list[str], dict[str, str]]:
    universe_path = US_STOCKS_DIR / "_universe.json"
    metadata_path = US_STOCKS_DIR / "_metadata.csv"
    universe_data = json.loads(universe_path.read_text(encoding="utf-8"))
    tickers = universe_data.get("tickers", [])
    meta = pd.read_csv(metadata_path)
    meta = meta[(meta["ticker"].isin(tickers)) & (meta["sector"].notna())].copy()
    if "pass_all" in meta.columns:
        meta = meta[meta["pass_all"] == True].copy()  # noqa: E712
    tickers = [ticker for ticker in tickers if ticker in set(meta["ticker"])]
    if max_symbols:
        tickers = tickers[:max_symbols]
        meta = meta[meta["ticker"].isin(tickers)]
    return tickers, dict(zip(meta["ticker"], meta["sector"]))


def load_or_download_prices(tickers: list[str], refresh: bool = False) -> dict[str, pd.DataFrame]:
    QMJ_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if PRICE_CACHE.exists() and not refresh:
        panel = pd.read_parquet(PRICE_CACHE)
        cached_symbols = set(panel["symbol"].unique()) if "symbol" in panel.columns else set()
        missing = sorted(set(tickers) - cached_symbols)
        if not missing:
            return _price_panel_to_dict(panel, tickers)
        logger.info("Price cache missing %d requested symbols; refreshing full requested panel", len(missing))

    import yfinance as yf

    chunks = [tickers[i : i + 80] for i in range(0, len(tickers), 80)]
    frames = []
    for idx, chunk in enumerate(chunks, start=1):
        logger.info("Downloading yfinance prices chunk %d/%d (%d symbols)", idx, len(chunks), len(chunk))
        raw = yf.download(
            chunk,
            start=START_PRICE,
            end="2025-01-02",
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
            threads=True,
        )
        frames.append(_normalize_yfinance_prices(raw, chunk))
        time.sleep(0.25)

    panel = pd.concat(frames, ignore_index=True).drop_duplicates(["date", "symbol"])
    panel.to_parquet(PRICE_CACHE, index=False)
    return _price_panel_to_dict(panel, tickers)


def _normalize_yfinance_prices(raw: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    rows = []
    if raw.empty:
        return pd.DataFrame(columns=["date", "symbol", "open", "high", "low", "close", "dividend"])
    for symbol in tickers:
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                if symbol in raw.columns.get_level_values(0):
                    df = raw[symbol].copy()
                elif symbol in raw.columns.get_level_values(1):
                    df = raw.xs(symbol, axis=1, level=1).copy()
                else:
                    continue
            else:
                df = raw.copy()
        except Exception:
            continue
        colmap = {str(col).lower(): col for col in df.columns}
        required = ["open", "high", "low", "close"]
        if any(col not in colmap for col in required):
            continue
        out = pd.DataFrame(
            {
                "date": pd.to_datetime(df.index).tz_localize(None).normalize(),
                "symbol": symbol,
                "open": pd.to_numeric(df[colmap["open"]], errors="coerce"),
                "high": pd.to_numeric(df[colmap["high"]], errors="coerce"),
                "low": pd.to_numeric(df[colmap["low"]], errors="coerce"),
                "close": pd.to_numeric(df[colmap["close"]], errors="coerce"),
                "dividend": 0.0,
            }
        )
        rows.append(out.dropna(subset=["open", "close"]))
    if not rows:
        return pd.DataFrame(columns=["date", "symbol", "open", "high", "low", "close", "dividend"])
    return pd.concat(rows, ignore_index=True)


def _price_panel_to_dict(panel: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    panel = panel[panel["symbol"].isin(tickers)].copy()
    out = {}
    for symbol, group in panel.groupby("symbol"):
        df = group.sort_values("date").set_index(pd.to_datetime(group.sort_values("date")["date"]))
        out[symbol] = df[["open", "high", "low", "close", "dividend"]].copy()
    return out


def load_company_tickers(refresh: bool = False) -> dict[str, int]:
    QMJ_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if COMPANY_TICKERS_CACHE.exists() and not refresh:
        payload = json.loads(COMPANY_TICKERS_CACHE.read_text(encoding="utf-8"))
    else:
        resp = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers={"User-Agent": SEC_USER_AGENT},
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        COMPANY_TICKERS_CACHE.write_text(json.dumps(payload), encoding="utf-8")
    mapping = {}
    for item in payload.values():
        ticker = str(item.get("ticker", "")).upper().replace(".", "-")
        cik = int(item.get("cik_str"))
        if ticker:
            mapping[ticker] = cik
    return mapping


def load_or_fetch_fundamentals(tickers: list[str], refresh: bool = False) -> dict[str, pd.DataFrame]:
    FUNDAMENTALS_DIR.mkdir(parents=True, exist_ok=True)
    FACTS_DIR.mkdir(parents=True, exist_ok=True)
    cik_map = load_company_tickers(refresh=False)
    fundamentals = {}
    for idx, symbol in enumerate(tickers, start=1):
        cache_path = FUNDAMENTALS_DIR / f"{symbol}.parquet"
        if cache_path.exists() and not refresh:
            df = pd.read_parquet(cache_path)
            if not df.empty:
                fundamentals[symbol] = df
            continue

        cik = cik_map.get(symbol.upper().replace(".", "-"))
        if cik is None:
            logger.warning("No SEC CIK for %s", symbol)
            continue
        try:
            facts = _load_or_fetch_companyfacts(cik, refresh=refresh)
            df = companyfacts_to_qmj_frame(facts)
            if not df.empty:
                df.to_parquet(cache_path, index=False)
                fundamentals[symbol] = df
        except Exception as exc:
            logger.warning("SEC fundamentals failed for %s: %s", symbol, exc)
        if idx % 25 == 0:
            logger.info("Fundamentals progress: %d/%d symbols, usable=%d", idx, len(tickers), len(fundamentals))
        time.sleep(0.11)
    return fundamentals


def _load_or_fetch_companyfacts(cik: int, refresh: bool = False) -> dict:
    cik_padded = f"{cik:010d}"
    path = FACTS_DIR / f"CIK{cik_padded}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    resp = requests.get(
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_padded}.json",
        headers={"User-Agent": SEC_USER_AGENT},
        timeout=45,
    )
    resp.raise_for_status()
    payload = resp.json()
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def companyfacts_to_qmj_frame(facts: dict) -> pd.DataFrame:
    metric_frames = []
    for metric, tags in METRIC_TAGS.items():
        duration = metric in {"total_revenue", "cost_of_revenue", "eps"}
        frame = _extract_metric(facts, metric, tags, duration=duration)
        if not frame.empty:
            metric_frames.append(frame)
    debt = _extract_debt(facts)
    if not debt.empty:
        metric_frames.append(debt)
    if not metric_frames:
        return pd.DataFrame()

    wide = None
    for frame in metric_frames:
        keys = ["period_end", "available_date", "filed_date", "period_type"]
        if wide is None:
            wide = frame
        else:
            wide = wide.merge(frame, on=keys, how="outer")
    if wide is None or wide.empty:
        return pd.DataFrame()
    required = [
        "period_end",
        "available_date",
        "filed_date",
        "period_type",
        "total_revenue",
        "cost_of_revenue",
        "total_assets",
        "total_debt",
        "total_stockholder_equity",
        "eps",
    ]
    for col in required:
        if col not in wide.columns:
            wide[col] = np.nan
    return wide[required].sort_values(["available_date", "period_end"]).reset_index(drop=True)


def _extract_metric(facts: dict, metric: str, tags: list[str], duration: bool) -> pd.DataFrame:
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    rows = []
    for priority, tag in enumerate(tags):
        fact = us_gaap.get(tag)
        if not fact:
            continue
        units = fact.get("units", {})
        unit_key = "USD/shares" if metric == "eps" else "USD"
        if unit_key not in units:
            continue
        for item in units[unit_key]:
            if item.get("form") not in {"10-Q", "10-K", "10-Q/A", "10-K/A"}:
                continue
            if "end" not in item or "filed" not in item:
                continue
            start = pd.to_datetime(item.get("start"), errors="coerce") if item.get("start") else pd.NaT
            end = pd.to_datetime(item.get("end"), errors="coerce")
            filed = pd.to_datetime(item.get("filed"), errors="coerce")
            if pd.isna(end) or pd.isna(filed):
                continue
            if duration:
                if pd.isna(start):
                    continue
                days = (end - start).days
                if days < 40 or days > 125:
                    continue
                period_type = "Q"
            else:
                period_type = "Q" if str(item.get("fp", "")).upper().startswith("Q") else "A"
            rows.append(
                {
                    "period_end": end.normalize(),
                    "available_date": filed.normalize(),
                    "filed_date": filed.normalize(),
                    "period_type": period_type,
                    metric: float(item.get("val")),
                    "_priority": priority,
                }
            )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).sort_values(["period_end", "available_date", "_priority"])
    df = df.drop_duplicates(["period_end", "available_date"], keep="first")
    return df.drop(columns=["_priority"])


def _extract_debt(facts: dict) -> pd.DataFrame:
    total = _extract_metric(facts, "total_debt", TOTAL_DEBT_TAGS, duration=False)
    if not total.empty:
        return total
    components = []
    for tag in DEBT_COMPONENT_TAGS:
        frame = _extract_metric(facts, tag, [tag], duration=False)
        if not frame.empty:
            frame = frame.rename(columns={tag: "component"})
            components.append(frame)
    if not components:
        return pd.DataFrame()
    comp = pd.concat(components, ignore_index=True)
    keys = ["period_end", "available_date", "filed_date", "period_type"]
    out = comp.groupby(keys, as_index=False)["component"].sum()
    return out.rename(columns={"component": "total_debt"})


def run_period(
    period_start: str,
    period_end: str,
    tickers: list[str],
    price_data: dict[str, pd.DataFrame],
    strategy: QualityMinusJunkStrategy,
    seed: int = 42,
) -> dict[str, Any]:
    usable = [symbol for symbol in tickers if symbol in price_data and symbol in strategy.fundamentals]
    engine = EventDrivenBacktester(
        universe=usable,
        start=period_start,
        end=period_end,
        capital=20_000,
        costs_config=CostsConfig(),
        seed=seed,
        universe_membership_source="current_sp500_snapshot",
        copy_history=False,
    )
    output = engine.run(
        price_data,
        strategy.signal_function,
        signal_filter=QualityMinusJunkStrategy.is_rebalance_day,
    )
    return {
        "strategy": strategy,
        "output": output,
        "metrics": output.metrics,
        "usable_symbols": usable,
    }


def build_windows() -> list[dict[str, str]]:
    windows = []
    for idx, year in enumerate(range(2018, 2025), start=1):
        windows.append(
            {
                "window_idx": idx,
                "train_start": f"{year - 4}-01-01",
                "train_end": f"{year - 1}-12-31",
                "test_start": f"{year}-01-01",
                "test_end": f"{year}-12-31",
            }
        )
    return windows


def summarize_window(window: dict[str, str], is_result: dict[str, Any], oos_result: dict[str, Any]) -> dict[str, Any]:
    is_metrics = is_result["metrics"]
    oos_metrics = oos_result["metrics"]
    return {
        **window,
        "is_sharpe": is_metrics["sharpe_net"],
        "is_max_drawdown": is_metrics["max_drawdown"],
        "is_n_trades": is_metrics["n_trades"],
        "oos_sharpe": oos_metrics["sharpe_net"],
        "oos_profit_factor": oos_metrics["profit_factor"],
        "oos_max_drawdown": oos_metrics["max_drawdown"],
        "oos_n_trades": oos_metrics["n_trades"],
        "oos_hit_rate": oos_metrics["hit_rate"],
        "oos_total_net_pnl": oos_metrics["total_net_pnl"],
        "oos_profitable": oos_metrics["total_net_pnl"] > 0,
        "oos_total_cost_pct": oos_metrics["total_cost_pct"],
        "oos_borrow_to_gross": oos_metrics["ratio_borrow_cost_to_gross"],
        "usable_symbols": len(oos_result["usable_symbols"]),
    }


def period_stats(equity: pd.DataFrame, start: str, end: str) -> dict[str, float]:
    if equity.empty:
        return {"sharpe": 0.0, "max_drawdown": 0.0, "net_pnl": 0.0}
    sub = equity.loc[(equity.index >= pd.Timestamp(start)) & (equity.index <= pd.Timestamp(end))]
    if sub.empty:
        return {"sharpe": 0.0, "max_drawdown": 0.0, "net_pnl": 0.0}
    returns = sub["equity"].pct_change().dropna()
    std = returns.std(ddof=1)
    sharpe = float(np.sqrt(252) * returns.mean() / std) if std and np.isfinite(std) else 0.0
    dd = float((sub["equity"] / sub["equity"].cummax() - 1.0).min())
    pnl = float(sub["equity"].iloc[-1] - sub["equity"].iloc[0])
    return {"sharpe": sharpe, "max_drawdown": dd, "net_pnl": pnl}


def correlation_summary(qmj_equity: pd.DataFrame) -> dict[str, Any]:
    baseline_path = ROOT / "data" / "research" / "portfolio_baseline_timeseries.parquet"
    if not baseline_path.exists() or qmj_equity.empty:
        return {"available": False, "reason": "baseline file missing or qmj equity empty", "correlations": {}}
    baseline = pd.read_parquet(baseline_path)
    baseline.index = pd.to_datetime(baseline.index).normalize()
    qmj_pnl = qmj_equity["equity"].diff().rename("us_qmj_v1").dropna()
    joined = pd.concat([qmj_pnl, baseline], axis=1, join="inner").dropna(how="all")
    columns = {
        "CAM": "cross_asset_momentum",
        "GOR": "gold_oil_rotation",
        "BTC_live_proxy": "btc_trend_sma50",
    }
    out = {}
    for label, col in columns.items():
        if col not in joined.columns:
            out[label] = None
            continue
        corr = joined["us_qmj_v1"].rolling(60).corr(joined[col]).dropna()
        out[label] = float(corr.abs().max()) if not corr.empty else 0.0
    valid = [v for v in out.values() if v is not None]
    return {"available": True, "correlations": out, "max_abs_rolling_60d": max(valid) if valid else None}


def build_verdict(
    full_oos: dict[str, Any],
    windows: list[dict[str, Any]],
    correlation: dict[str, Any],
    stress: dict[str, Any],
    sideways_check: dict[str, Any] | None = None,
) -> tuple[str, list[dict[str, Any]], str]:
    metrics = full_oos["metrics"]
    profitable_windows = sum(1 for window in windows if window["oos_profitable"])
    max_is_dd = max(abs(window["is_max_drawdown"]) for window in windows if window["is_n_trades"] > 0) or 0.0
    avg_oos_dd = np.mean([abs(window["oos_max_drawdown"]) for window in windows]) if windows else 0.0
    max_corr = correlation.get("max_abs_rolling_60d")
    if max_corr is None:
        max_corr = 999.0
    stress_dd_limit = 1.5 * avg_oos_dd if avg_oos_dd else 0.0
    stress_ok = (
        abs(stress["covid_2020"]["max_drawdown"]) <= stress_dd_limit
        and abs(stress["bear_2022"]["max_drawdown"]) <= stress_dd_limit
        and abs(stress["gme_squeeze"]["max_single_name_loss_pct_gross"]) < 0.05
    )
    if sideways_check is not None:
        sideways_sharpe = float(sideways_check["sharpe_net"])
    else:
        sideways_rows = metrics["regime_breakdown"].query("window == '2015_2016'")
        sideways_sharpe = float(sideways_rows["sharpe"].iloc[0]) if not sideways_rows.empty else 0.0

    checks = [
        {"name": "Sharpe OOS net >= 0.8", "pass": metrics["sharpe_net"] >= 0.8, "value": metrics["sharpe_net"]},
        {"name": "PF OOS >= 1.2", "pass": metrics["profit_factor"] >= 1.2, "value": metrics["profit_factor"]},
        {"name": ">= 50% OOS windows profitable", "pass": profitable_windows >= 4, "value": profitable_windows},
        {"name": ">= 200 trades OOS", "pass": metrics["n_trades"] >= 200, "value": metrics["n_trades"]},
        {
            "name": "Max DD OOS <= 2x Max DD IS",
            "pass": abs(metrics["max_drawdown"]) <= 2 * max_is_dd if max_is_dd else False,
            "value": {"oos": metrics["max_drawdown"], "max_abs_is": max_is_dd},
        },
        {
            "name": "(costs + borrow) / gross <= 30%",
            "pass": metrics["total_cost_pct"] <= 0.30,
            "value": metrics["total_cost_pct"],
        },
        {"name": "Corr 60d vs live sleeves <= 0.4", "pass": max_corr <= 0.40, "value": max_corr},
        {"name": "Stress periods no catastrophe", "pass": stress_ok, "value": stress},
        {"name": "Sideways 2015-16 Sharpe >= 0", "pass": sideways_sharpe >= 0, "value": sideways_sharpe},
    ]
    failed = [check for check in checks if not check["pass"]]
    bear_bad = stress["covid_2020"]["net_pnl"] < 0 and stress["bear_2022"]["net_pnl"] < 0
    if not failed:
        verdict = "PASS"
    elif len(failed) <= 2 and not bear_bad:
        verdict = "FAIL"
    else:
        verdict = "REJECTED"
    rationale = (
        f"{verdict}: {len(failed)}/{len(checks)} criteria failed; "
        f"profitable_windows={profitable_windows}/{len(windows)}, "
        f"sharpe_oos={metrics['sharpe_net']:.3f}, pf_oos={metrics['profit_factor']:.3f}. "
        f"Sideways 2015-16 check sharpe={sideways_sharpe:.3f}."
    )
    if bear_bad:
        rationale += " Bear stress PnL is negative in both 2020 COVID and 2022, so bull-only risk is flagged."
    return verdict, checks, rationale


def build_stress(full_oos: dict[str, Any]) -> dict[str, Any]:
    output = full_oos["output"]
    trades = output.trades.copy()
    gme_start = pd.Timestamp("2021-01-25")
    gme_end = pd.Timestamp("2021-02-05")
    if trades.empty:
        max_single = 0.0
    else:
        trades["entry_date"] = pd.to_datetime(trades["entry_date"])
        trades["exit_date"] = pd.to_datetime(trades["exit_date"])
        overlap = trades[(trades["entry_date"] <= gme_end) & (trades["exit_date"] >= gme_start) & (trades["side"] == "short")]
        if overlap.empty:
            max_single = 0.0
        else:
            max_single = float((overlap["net_pnl"] / 20_000).min())
    return {
        "covid_2020": period_stats(output.equity_curve, "2020-02-15", "2020-04-30"),
        "bear_2022": period_stats(output.equity_curve, "2022-01-01", "2022-12-31"),
        "gme_squeeze": {
            **period_stats(output.equity_curve, "2021-01-25", "2021-02-05"),
            "max_single_name_loss_pct_gross": max_single,
        },
        "q4_2018": period_stats(output.equity_curve, "2018-10-01", "2018-12-31"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-symbols", type=int, default=None)
    parser.add_argument("--refresh-prices", action="store_true")
    parser.add_argument("--refresh-fundamentals", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(levelname)s:%(message)s")
    tickers, sector_map = load_universe(max_symbols=args.max_symbols)
    logger.info("Universe loaded: %d symbols", len(tickers))
    price_data = load_or_download_prices(tickers, refresh=args.refresh_prices)
    fundamentals = load_or_fetch_fundamentals(tickers, refresh=args.refresh_fundamentals)
    usable = [symbol for symbol in tickers if symbol in price_data and symbol in fundamentals]
    logger.info("Usable symbols with prices+fundamentals: %d/%d", len(usable), len(tickers))
    strategy = QualityMinusJunkStrategy(
        fundamentals={symbol: fundamentals[symbol] for symbol in usable},
        sector_map={symbol: sector_map[symbol] for symbol in usable},
        config=QMJConfig(),
        pit_method="EDGAR companyfacts filed_date when available; fallback lag proxy in strategy",
    )

    windows = []
    for window in build_windows():
        logger.info("WF window %d: IS %s-%s OOS %s-%s", window["window_idx"], window["train_start"], window["train_end"], window["test_start"], window["test_end"])
        is_result = run_period(window["train_start"], window["train_end"], usable, price_data, strategy, seed=100 + window["window_idx"])
        oos_result = run_period(window["test_start"], window["test_end"], usable, price_data, strategy, seed=200 + window["window_idx"])
        windows.append(summarize_window(window, is_result, oos_result))

    full_oos = run_period("2018-01-01", "2024-12-31", usable, price_data, strategy, seed=999)
    sideways_result = run_period("2015-01-01", "2016-12-31", usable, price_data, strategy, seed=1516)
    sideways_check = {
        "sharpe_net": sideways_result["metrics"]["sharpe_net"],
        "profit_factor": sideways_result["metrics"]["profit_factor"],
        "max_drawdown": sideways_result["metrics"]["max_drawdown"],
        "n_trades": sideways_result["metrics"]["n_trades"],
        "hit_rate": sideways_result["metrics"]["hit_rate"],
        "total_net_pnl": sideways_result["metrics"]["total_net_pnl"],
        "observations": int(len(sideways_result["output"].equity_curve)),
    }
    correlation = correlation_summary(full_oos["output"].equity_curve)
    stress = build_stress(full_oos)
    verdict, checks, rationale = build_verdict(full_oos, windows, correlation, stress, sideways_check)
    metrics = full_oos["metrics"]

    manifest = {
        "strategy_id": "us_qmj_v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "verdict": verdict,
        "rationale": rationale,
        "universe": {
            "source": "current S&P 500 snapshot from data/us_stocks/_universe.json",
            "requested_symbols": len(tickers),
            "usable_symbols": len(usable),
            "survivorship_bias_warning": True,
        },
        "data_limitations": metrics["metadata"]["data_limitations"],
        "fundamentals": {
            "source": "SEC EDGAR companyfacts cached locally",
            "pit_proxy": "EDGAR filed_date when available; strategy fallback applies 90d quarterly / 120d annual lag",
            "not_true_pit": True,
            "q4_2020_visibility_rule": "period_end 2020-12-31 with lag proxy is not visible before 2021-04-01",
        },
        "costs": {
            "slippage_pct": 0.0002,
            "borrow_rate_annual": 0.015,
            "locate_fail_rate": 0.03,
            "commission_usd": 0.0,
        },
        "wf_config": {
            "is_years": 4,
            "oos_years": 1,
            "windows": "2018-2024 annual OOS",
            "score_weights": "1/3 profitability, 1/3 safety, 1/3 stability; no optimization",
        },
        "criteria": checks,
        "oos_metrics_concat": {
            "sharpe_net": metrics["sharpe_net"],
            "profit_factor": metrics["profit_factor"],
            "max_drawdown": metrics["max_drawdown"],
            "hit_rate": metrics["hit_rate"],
            "n_trades": metrics["n_trades"],
            "avg_hold": metrics["avg_hold"],
            "total_cost_pct": metrics["total_cost_pct"],
            "ratio_borrow_cost_to_gross": metrics["ratio_borrow_cost_to_gross"],
            "total_net_pnl": metrics["total_net_pnl"],
        },
        "wf_windows": windows,
        "regime_breakdown": metrics["regime_breakdown"],
        "correlation": correlation,
        "stress_tests": stress,
        "sideways_2015_2016_check": sideways_check,
        "paper_monitoring": {
            "eligible": verdict == "PASS",
            "minimum_days": 30,
            "earliest_live_if_marc_approves": "2026-06-15",
        },
    }

    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(_clean_float(manifest), indent=2), encoding="utf-8")
    if verdict in {"FAIL", "REJECTED"}:
        REJECTED_PATH.parent.mkdir(parents=True, exist_ok=True)
        REJECTED_PATH.write_text(json.dumps(_clean_float(manifest), indent=2), encoding="utf-8")
    logger.info("Manifest written: %s", MANIFEST_PATH)
    logger.info("Verdict: %s", rationale)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
