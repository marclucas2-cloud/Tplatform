"""Atomic 6-leg crypto long/short runner — `alt_rel_strength_14_60_7` (T4-A2 VALIDATED).

Source de validation:
  - scripts/research/backtest_t4_crypto_relative_strength.py (beta-adjusted)
  - docs/research/wf_reports/T4A-02_crypto_relative_strength.md
  - docs/research/wf_reports/INT-D_crypto_batch.md
    -> Sharpe +1.11, MaxDD -7.8%, WF 3/5, MC P(DD>30%) 0.5%, bull/bear BOTH positive

Thesis:
  Altcoins beta-adjusted alpha vs BTC mean-reverts cross-sectionally. Top-3
  positive alpha LONG (spot), bottom-3 negative alpha SHORT (margin isolated).
  Weekly rebalance. Bull +$3,591 / bear +$515 sur 818 days backtest.

Logic (strict replica backtest beta_adjusted_scores):
  - alpha = cumret_14d_alt - beta_60d * cumret_14d_btc
  - beta = cov(alt, btc) / var(btc) on last 60 days
  - top_n = 3 (longs), bottom_n = 3 (shorts)
  - rebalance every 7 days
  - cost: 0.26% RT + 0.005%/day short borrow proxy

Runner approach (vs signal_fn per-symbol du pattern STRAT-XXX):
  Le pattern worker.run_crypto_cycle itere strat-par-strat + 1 signal par call.
  Une rotation atomique 6-leg (fermer N + ouvrir 6) n'est pas possible avec ce
  pattern. Ce runner prend le meme design que core/runtime/spread_paper_runner
  (MIB/ESTX50): state JSON + journal JSONL, atomic rotation, stop check.

Paper-first mode:
  - tick() avec `live=False` simule entry/exit sans broker call
  - Journal JSONL: trades simules + pnl journalier
  - State: positions avec entry_price pour check SL per position + portfolio
  - 30j observation paper avant flip live_probation

Live mode (non active cette session):
  - Idem mais avec appels BinanceBroker._create_margin_position pour shorts
  - Appels spot buy pour longs
  - SL obligatoire par position + cascade 2/6 portfolio stop
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ── Universe & params (match backtest alt_rel_strength_14_60_7) ─────────────
UNIVERSE = ["ETH", "SOL", "BNB", "XRP", "ADA", "LINK", "AVAX", "DOT", "NEAR", "SUI"]
BASE = "BTC"
ALPHA_WINDOW_DAYS = 14
BETA_WINDOW_DAYS = 60
REBALANCE_DAYS = 7
TOP_N = 3
BOTTOM_N = 3

# ── Size conservateur (vs backtest $1K per leg -> $500 ici pour paper->live) ─
DEFAULT_CAPITAL_PER_LEG_USD = 500.0  # 6 legs * $500 = $3K gross on ~$10K equity
DEFAULT_SL_PCT_PER_POSITION = 0.08    # -8% per leg
DEFAULT_SL_PCT_PORTFOLIO = 0.05       # -5% cascade portfolio
DEFAULT_MAX_STOPS_CASCADE = 2         # 2/6 stops -> close all
DEFAULT_COST_PER_SIDE = 0.0013        # 13 bps per side = 26 bps RT (backtest)
DEFAULT_SHORT_BORROW_DAILY = 0.00005  # 0.005%/day proxy (backtest)


@dataclass
class AltRelStrengthState:
    """Persisted state: open positions + last rebalance + cumulative metrics."""
    positions: dict[str, dict] = field(default_factory=dict)
    # {sym: {"direction": 1|-1, "entry_price": float, "entry_date": iso, "notional_usd": float}}
    last_rebalance_date: str | None = None  # ISO date
    cumulative_pnl_usd: float = 0.0
    cumulative_trades_closed: int = 0
    stops_hit_this_week: int = 0
    week_start_date: str | None = None  # reset stops_hit when week_start changes


def load_panel(data_dir: Path, universe: list[str]) -> pd.DataFrame:
    """Load BTC + alt closes from {SYM}USDT_1d.parquet, aligned.

    Returns DataFrame index=date (normalized, naive UTC), columns=[BASE, *universe].
    """
    series = []
    for sym in [BASE] + universe:
        path = data_dir / f"{sym}USDT_1d.parquet"
        if not path.exists():
            logger.warning(f"alt_rel_strength: missing parquet for {sym}")
            continue
        df = pd.read_parquet(path)
        if "timestamp" in df.columns:
            idx = pd.to_datetime(df["timestamp"]).dt.tz_localize(None).dt.normalize()
            close = pd.Series(df["close"].values, index=idx, name=sym).sort_index()
        else:
            idx = pd.to_datetime(df.index).tz_localize(None).normalize()
            close = pd.Series(df["close"].values, index=idx, name=sym).sort_index()
        series.append(close)
    if len(series) < len([BASE] + universe) - 2:  # allow up to 2 missing alts
        raise ValueError(f"alt_rel_strength: too many missing symbols ({len(series)}/{len(universe)+1})")
    panel = pd.concat(series, axis=1).sort_index().ffill().dropna(how="any")
    return panel


def compute_beta_adjusted_alpha(
    prices: pd.DataFrame,
    as_of: pd.Timestamp,
    alpha_window: int = ALPHA_WINDOW_DAYS,
    beta_window: int = BETA_WINDOW_DAYS,
) -> pd.Series:
    """Beta-adjusted alpha at `as_of`, using data STRICTLY <= as_of.

    alpha[alt] = cumret_alpha_window(alt) - beta_beta_window * cumret_alpha_window(btc)
    beta[alt] = cov(alt, btc) / var(btc) on last beta_window days.

    Returns Series sorted descending (alts only, no BASE). Empty if insufficient data.
    """
    as_of = pd.Timestamp(as_of).normalize()
    if as_of not in prices.index:
        return pd.Series(dtype=float)
    # Fidelity backtest: EXCLUT as_of (cf backtest_t4 line 103 "returns.iloc[:i]").
    # On decide la rotation sur data STRICTEMENT anterieure, pas sur close as_of.
    hist = prices.loc[:as_of].iloc[:-1]
    if len(hist) < beta_window + 1:
        return pd.Series(dtype=float)
    returns = hist.pct_change().fillna(0.0)
    btc_ret = returns[BASE]
    btc_window = btc_ret.tail(beta_window)
    btc_var = float(btc_window.var())
    if btc_var == 0 or pd.isna(btc_var):
        return pd.Series(dtype=float)
    btc_cum_alpha = float((1.0 + btc_ret.tail(alpha_window)).prod() - 1.0)
    alts = [c for c in prices.columns if c != BASE]
    scores = {}
    for sym in alts:
        alt_window = returns[sym].tail(beta_window)
        cov = float(alt_window.cov(btc_window))
        beta = cov / btc_var
        alt_cum = float((1.0 + returns[sym].tail(alpha_window)).prod() - 1.0)
        scores[sym] = alt_cum - beta * btc_cum_alpha
    return pd.Series(scores).dropna().sort_values(ascending=False)


def select_positions(alphas: pd.Series, top_n: int = TOP_N, bottom_n: int = BOTTOM_N) -> tuple[list[str], list[str]]:
    """Return (longs, shorts). Both lists `top_n`/`bottom_n` long. Disjoint."""
    if len(alphas) < top_n + bottom_n:
        return [], []
    longs = list(alphas.head(top_n).index)
    shorts = list(alphas.tail(bottom_n).index)
    # Ensure disjoint
    longs = [s for s in longs if s not in shorts]
    return longs, shorts


def check_stops(
    positions: dict[str, dict],
    current_prices: dict[str, float],
    sl_per_position: float,
) -> list[str]:
    """Return list of symbols that hit SL (unrealized < -sl_per_position)."""
    hit = []
    for sym, pos in positions.items():
        if sym not in current_prices:
            continue
        entry = float(pos["entry_price"])
        if entry <= 0:
            continue
        direction = int(pos["direction"])
        unrealized = (current_prices[sym] / entry - 1.0) * direction
        if unrealized <= -sl_per_position:
            hit.append(sym)
    return hit


def portfolio_unrealized_pct(
    positions: dict[str, dict],
    current_prices: dict[str, float],
) -> float:
    """Return weighted mean unrealized pct across all open positions."""
    if not positions:
        return 0.0
    total = 0.0
    n = 0
    for sym, pos in positions.items():
        if sym not in current_prices:
            continue
        entry = float(pos["entry_price"])
        if entry <= 0:
            continue
        direction = int(pos["direction"])
        unrealized = (current_prices[sym] / entry - 1.0) * direction
        total += unrealized
        n += 1
    return total / n if n > 0 else 0.0


@dataclass(frozen=True)
class TickResult:
    """Outcome of one tick (daily or rebalance)."""
    as_of_date: pd.Timestamp
    action: Literal["rebalance", "hold", "cascade_close", "portfolio_stop", "stop_loss", "init", "warmup"]
    rotation_plan: dict = field(default_factory=dict)
    # {"closes": [syms], "opens_long": [syms], "opens_short": [syms]}
    stops_triggered: list[str] = field(default_factory=list)
    daily_pnl_usd: float = 0.0
    cost_usd: float = 0.0
    net_pnl_usd: float = 0.0
    portfolio_unrealized_pct: float = 0.0
    positions_after: dict[str, dict] = field(default_factory=dict)


class AltRelStrengthRunner:
    """Atomic 6-leg long/short runner, paper or live."""

    def __init__(
        self,
        state_path: Path,
        journal_path: Path,
        paper: bool = True,
        capital_per_leg: float = DEFAULT_CAPITAL_PER_LEG_USD,
        sl_per_position: float = DEFAULT_SL_PCT_PER_POSITION,
        sl_portfolio: float = DEFAULT_SL_PCT_PORTFOLIO,
        max_stops_cascade: int = DEFAULT_MAX_STOPS_CASCADE,
        cost_per_side: float = DEFAULT_COST_PER_SIDE,
        short_borrow_daily: float = DEFAULT_SHORT_BORROW_DAILY,
    ) -> None:
        self.state_path = state_path
        self.journal_path = journal_path
        self.paper = paper
        self.capital_per_leg = capital_per_leg
        self.sl_per_position = sl_per_position
        self.sl_portfolio = sl_portfolio
        self.max_stops_cascade = max_stops_cascade
        self.cost_per_side = cost_per_side
        self.short_borrow_daily = short_borrow_daily
        self.state = self._load_state()

    def _load_state(self) -> AltRelStrengthState:
        if not self.state_path.exists():
            return AltRelStrengthState()
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            return AltRelStrengthState(
                positions=raw.get("positions", {}),
                last_rebalance_date=raw.get("last_rebalance_date"),
                cumulative_pnl_usd=float(raw.get("cumulative_pnl_usd", 0.0)),
                cumulative_trades_closed=int(raw.get("cumulative_trades_closed", 0)),
                stops_hit_this_week=int(raw.get("stops_hit_this_week", 0)),
                week_start_date=raw.get("week_start_date"),
            )
        except Exception as e:
            logger.warning(f"alt_rel_strength: state reload failed, reset: {e}")
            return AltRelStrengthState()

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps({
                "positions": self.state.positions,
                "last_rebalance_date": self.state.last_rebalance_date,
                "cumulative_pnl_usd": self.state.cumulative_pnl_usd,
                "cumulative_trades_closed": self.state.cumulative_trades_closed,
                "stops_hit_this_week": self.state.stops_hit_this_week,
                "week_start_date": self.state.week_start_date,
            }, indent=2),
            encoding="utf-8",
        )

    def _journal(self, event: dict) -> None:
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        with self.journal_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")

    def _is_already_journaled(self, as_of_iso: str) -> bool:
        """Idempotence: check journal already has an entry for this date."""
        if not self.journal_path.exists():
            return False
        for line in self.journal_path.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
                if e.get("as_of_date") == as_of_iso:
                    return True
            except Exception:
                continue
        return False

    def _close_position(self, sym: str, exit_price: float, reason: str, exit_date: pd.Timestamp | None = None) -> float:
        """Compute realized PnL on close, update state. Returns realized PnL USD.

        exit_date: Timestamp d'exit (pour days_held). Default = now UTC (non-replay).
        Fix review N2 #8: permet backfill/replay coherent au lieu de utcnow().
        """
        if sym not in self.state.positions:
            return 0.0
        pos = self.state.positions[sym]
        entry = float(pos["entry_price"])
        direction = int(pos["direction"])
        notional = float(pos["notional_usd"])
        # Gross return (percent)
        gross_ret = (exit_price / entry - 1.0) * direction
        # Short borrow cost (linear over holding days)
        days_held = 0
        try:
            entry_dt = pd.Timestamp(pos["entry_date"]).tz_localize(None).normalize() if pd.Timestamp(pos["entry_date"]).tz is not None else pd.Timestamp(pos["entry_date"]).normalize()
            exit_dt = pd.Timestamp(exit_date).normalize() if exit_date is not None else pd.Timestamp.utcnow().tz_localize(None).normalize()
            days_held = max(0, (exit_dt - entry_dt).days)
        except Exception:
            pass
        borrow_cost_pct = self.short_borrow_daily * days_held if direction == -1 else 0.0
        # Exit cost (entry cost deja paid on open)
        exit_cost_pct = self.cost_per_side
        net_ret = gross_ret - borrow_cost_pct - exit_cost_pct
        realized_usd = notional * net_ret
        self.state.cumulative_pnl_usd += realized_usd
        self.state.cumulative_trades_closed += 1
        del self.state.positions[sym]
        return realized_usd

    def _open_position(
        self,
        sym: str,
        direction: Literal[1, -1],
        entry_price: float,
        as_of_date: pd.Timestamp,
        notional_usd: float,
    ) -> None:
        """Open position: debit entry cost, record state. Paper or live."""
        self.state.positions[sym] = {
            "direction": direction,
            "entry_price": entry_price,
            "entry_date": as_of_date.isoformat(),
            "notional_usd": notional_usd,
        }
        # Entry cost debited from cumulative pnl (fair accounting)
        self.state.cumulative_pnl_usd -= notional_usd * self.cost_per_side

    def tick(
        self,
        as_of_date: pd.Timestamp,
        prices: pd.DataFrame,
    ) -> TickResult:
        """One daily tick.

        - Check SL per position (if 2+ triggered -> cascade close all)
        - Check SL portfolio (-5% mean unrealized -> close all)
        - If rebalance day + enough time since last -> atomic rotation

        Paper mode: log decisions, simulate fills at close price.
        Live mode (non-active): would call BinanceBroker._create_margin_position.
        """
        # Fix review N2 #4: normalize + strip tz pour coherence avec prices.index naive
        _ts = pd.Timestamp(as_of_date)
        if _ts.tz is not None:
            _ts = _ts.tz_localize(None)
        as_of_date = _ts.normalize()
        as_of_iso = as_of_date.isoformat()

        if self._is_already_journaled(as_of_iso):
            logger.debug(f"alt_rel_strength: already journaled {as_of_date.date()}")
            # Return no-op hold
            return TickResult(
                as_of_date=as_of_date, action="hold",
                positions_after=dict(self.state.positions),
            )

        if as_of_date not in prices.index:
            return TickResult(
                as_of_date=as_of_date, action="warmup",
                positions_after=dict(self.state.positions),
            )

        current_prices = {
            sym: float(prices.loc[as_of_date, sym])
            for sym in prices.columns
        }

        # Reset stops_hit_this_week on Monday
        current_week_start = (as_of_date - pd.Timedelta(days=as_of_date.dayofweek)).normalize()
        week_start_iso = current_week_start.isoformat()
        if self.state.week_start_date != week_start_iso:
            self.state.stops_hit_this_week = 0
            self.state.week_start_date = week_start_iso

        # ── 1. SL check per position ──────────────────────────────────────
        stops_triggered = check_stops(
            self.state.positions, current_prices, self.sl_per_position
        )

        # ── 2. Cascade: 2+ stops cumules semaine -> close all ─────────────
        total_stops_week = len(stops_triggered) + self.state.stops_hit_this_week
        if total_stops_week >= self.max_stops_cascade and self.state.positions:
            closed_pnl = 0.0
            closed_syms = list(self.state.positions.keys())
            for sym in closed_syms:
                if sym in current_prices:
                    closed_pnl += self._close_position(
                        sym, current_prices[sym], "cascade_stop", exit_date=as_of_date
                    )
            self.state.stops_hit_this_week = 0  # reset after cascade
            # Fix review N2 #2: journal AVANT save_state (crash safety)
            self._journal({
                "as_of_date": as_of_iso,
                "action": "cascade_close",
                "stops_triggered": stops_triggered,
                "pnl_usd": closed_pnl,
                "cumulative_pnl_usd": self.state.cumulative_pnl_usd,
            })
            self._save_state()
            return TickResult(
                as_of_date=as_of_date, action="cascade_close",
                stops_triggered=stops_triggered,
                rotation_plan={"closes": closed_syms, "opens_long": [], "opens_short": []},
                daily_pnl_usd=closed_pnl,
                net_pnl_usd=closed_pnl,
                positions_after={},
            )

        # ── 2-bis. Single stops (non-cascade): close individuellement ─────
        # Fix review N2 #1: ne pas laisser couler au-dela de -sl_per_position.
        # Les positions triggered sont fermees sur la bar as_of, stops_hit_this_week incr.
        if stops_triggered:
            single_stop_pnl = 0.0
            for sym in stops_triggered:
                if sym in current_prices:
                    single_stop_pnl += self._close_position(
                        sym, current_prices[sym], "stop_loss", exit_date=as_of_date
                    )
            self.state.stops_hit_this_week += len(stops_triggered)
            self._journal({
                "as_of_date": as_of_iso,
                "action": "stop_loss",
                "stops_triggered": stops_triggered,
                "pnl_usd": single_stop_pnl,
                "stops_hit_this_week": self.state.stops_hit_this_week,
                "cumulative_pnl_usd": self.state.cumulative_pnl_usd,
            })
            self._save_state()
            # Continue tick: le check portfolio_stop + rebalance peuvent encore s'appliquer
            # sur les positions restantes. Pour paper log-only on arrete ici et return.
            return TickResult(
                as_of_date=as_of_date, action="stop_loss",
                stops_triggered=stops_triggered,
                rotation_plan={"closes": stops_triggered, "opens_long": [], "opens_short": []},
                daily_pnl_usd=single_stop_pnl,
                net_pnl_usd=single_stop_pnl,
                positions_after=dict(self.state.positions),
            )

        # ── 3. Portfolio stop: mean unrealized < -5% ──────────────────────
        p_unrealized = portfolio_unrealized_pct(self.state.positions, current_prices)
        if p_unrealized <= -self.sl_portfolio and self.state.positions:
            closed_pnl = 0.0
            closed_syms = list(self.state.positions.keys())
            for sym in closed_syms:
                if sym in current_prices:
                    closed_pnl += self._close_position(
                        sym, current_prices[sym], "portfolio_stop", exit_date=as_of_date
                    )
            self._journal({
                "as_of_date": as_of_iso,
                "action": "portfolio_stop",
                "portfolio_unrealized_pct": p_unrealized,
                "pnl_usd": closed_pnl,
                "cumulative_pnl_usd": self.state.cumulative_pnl_usd,
            })
            self._save_state()
            return TickResult(
                as_of_date=as_of_date, action="portfolio_stop",
                rotation_plan={"closes": closed_syms, "opens_long": [], "opens_short": []},
                daily_pnl_usd=closed_pnl,
                net_pnl_usd=closed_pnl,
                portfolio_unrealized_pct=p_unrealized,
                positions_after={},
            )

        # ── 4. Rebalance check: Sunday + enough days since last ───────────
        is_sunday = as_of_date.dayofweek == 6
        days_since = 9999
        if self.state.last_rebalance_date:
            try:
                last_rb = pd.Timestamp(self.state.last_rebalance_date).normalize()
                days_since = (as_of_date - last_rb).days
            except Exception:
                pass
        do_rebalance = is_sunday and days_since >= REBALANCE_DAYS

        if do_rebalance:
            alphas = compute_beta_adjusted_alpha(
                prices.drop(columns=[c for c in prices.columns if c not in UNIVERSE + [BASE]]),
                as_of_date,
            )
            if alphas.empty:
                # Not enough history, warmup
                return TickResult(
                    as_of_date=as_of_date, action="warmup",
                    positions_after=dict(self.state.positions),
                )
            longs, shorts = select_positions(alphas)
            if not longs or not shorts:
                return TickResult(
                    as_of_date=as_of_date, action="warmup",
                    positions_after=dict(self.state.positions),
                )

            # Rotation atomic: close old that are not in new targets, open missing
            to_close = [
                sym for sym, pos in self.state.positions.items()
                if (pos["direction"] == 1 and sym not in longs)
                or (pos["direction"] == -1 and sym not in shorts)
            ]
            realized_pnl = 0.0
            for sym in to_close:
                if sym in current_prices:
                    realized_pnl += self._close_position(
                        sym, current_prices[sym], "rotation_out", exit_date=as_of_date
                    )

            to_open_long = [s for s in longs if s not in self.state.positions]
            to_open_short = [s for s in shorts if s not in self.state.positions]
            for sym in to_open_long:
                if sym in current_prices:
                    self._open_position(sym, 1, current_prices[sym], as_of_date, self.capital_per_leg)
            for sym in to_open_short:
                if sym in current_prices:
                    self._open_position(sym, -1, current_prices[sym], as_of_date, self.capital_per_leg)

            self.state.last_rebalance_date = as_of_iso
            action: Literal["rebalance", "init"] = (
                "init" if self.state.cumulative_trades_closed == 0 and len(to_close) == 0 else "rebalance"
            )
            # Fix review N2 #2: journal AVANT save_state
            self._journal({
                "as_of_date": as_of_iso,
                "action": action,
                "longs_target": longs,
                "shorts_target": shorts,
                "closes": to_close,
                "opens_long": to_open_long,
                "opens_short": to_open_short,
                "realized_pnl_usd": realized_pnl,
                "cumulative_pnl_usd": self.state.cumulative_pnl_usd,
            })
            self._save_state()
            return TickResult(
                as_of_date=as_of_date, action=action,
                rotation_plan={
                    "closes": to_close,
                    "opens_long": to_open_long,
                    "opens_short": to_open_short,
                },
                daily_pnl_usd=realized_pnl,
                net_pnl_usd=realized_pnl,
                positions_after=dict(self.state.positions),
            )

        # ── 5. Hold: compute daily mark-to-market pnl for journal ────────
        self._save_state()
        result = TickResult(
            as_of_date=as_of_date, action="hold",
            portfolio_unrealized_pct=p_unrealized,
            positions_after=dict(self.state.positions),
        )
        self._journal({
            "as_of_date": as_of_iso,
            "action": "hold",
            "portfolio_unrealized_pct": p_unrealized,
            "n_positions": len(self.state.positions),
            "cumulative_pnl_usd": self.state.cumulative_pnl_usd,
        })
        return result


def for_paper(state_dir: Path | None = None) -> AltRelStrengthRunner:
    """Factory: paper mode runner with default state/journal paths."""
    if state_dir is None:
        state_dir = Path("data/state/alt_rel_strength")
    return AltRelStrengthRunner(
        state_path=state_dir / "state.json",
        journal_path=state_dir / "paper_journal.jsonl",
        paper=True,
    )
