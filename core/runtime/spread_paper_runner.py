"""Paper runner pour spread strategies (multi-leg atomiques).

Isolation totale: pas de broker, pas de Signal, pas de StrategyBase.
Logge les trades simules dans data/state/<strategy>/paper_trades.jsonl
pour analyse 30j et promotion eventuelle.

Usage worker:
    runner = SpreadPaperRunner.for_mib_estx50()
    runner.tick(today_bar_a, today_bar_b)  # 1x/jour apres close EU

Etat persistant entre runs dans data/state/<strategy>/spread_state.json.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger("spread_paper_runner")

ROOT = Path(__file__).resolve().parent.parent.parent
STATE_ROOT = ROOT / "data" / "state"


@dataclass
class SpreadPosition:
    """Etat d'un spread ouvert (2 legs synchronisees)."""

    entry_date: str
    direction: str  # "LONG_SPREAD" ou "SHORT_SPREAD"
    sym_a: str
    sym_b: str
    n_a: int
    n_b: int
    entry_a: float
    entry_b: float
    entry_z: float
    hold_days: int = 0


@dataclass
class SpreadConfig:
    """Configuration immuable d'un spread strategy."""

    strategy_id: str
    sym_a: str
    sym_b: str
    mult_a: float
    mult_b: float
    comm_a: float
    comm_b: float
    tick_a: float
    tick_b: float
    slip_ticks: float = 0.5  # par leg, par cote
    lookback: int = 60
    z_entry: float = 2.0
    z_exit: float = 0.0
    z_stop: float = 3.5
    max_hold: int = 60


def _hedge_ratio(price_a: float, price_b: float, cfg: SpreadConfig) -> tuple[int, int]:
    """Notional-based dollar-neutral hedge ratio. Anchor n_a=1, scale n_b."""
    notional_a = price_a * cfg.mult_a
    notional_b = price_b * cfg.mult_b
    n_b_raw = notional_a / notional_b if notional_b > 0 else 1.0
    return 1, max(1, round(n_b_raw))


def _trade_costs(n_a: int, n_b: int, cfg: SpreadConfig) -> float:
    """Round-trip cost: commissions 2 legs * 2 sides + slippage 4 crossings."""
    comm = 2 * (n_a * cfg.comm_a + n_b * cfg.comm_b)
    slip_a = 2 * n_a * cfg.slip_ticks * cfg.tick_a * cfg.mult_a
    slip_b = 2 * n_b * cfg.slip_ticks * cfg.tick_b * cfg.mult_b
    return comm + slip_a + slip_b


def _zscore(history: pd.Series, lookback: int) -> float | None:
    """Z-score du log-ratio sur les `lookback` derniers points (incluant aujourd'hui)."""
    if len(history) < lookback:
        return None
    window = history.tail(lookback)
    mu = window.mean()
    sigma = window.std()
    if sigma == 0 or pd.isna(sigma):
        return None
    return float((history.iloc[-1] - mu) / sigma)


class SpreadPaperRunner:
    """Runner paper isole pour une spread strategy."""

    def __init__(self, cfg: SpreadConfig):
        self.cfg = cfg
        self.state_dir = STATE_ROOT / cfg.strategy_id
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.state_dir / "spread_state.json"
        self.trades_path = self.state_dir / "paper_trades.jsonl"
        self.history_a_path = self.state_dir / "history_a.parquet"
        self.history_b_path = self.state_dir / "history_b.parquet"
        self._load_state()

    @classmethod
    def for_mib_estx50(cls) -> "SpreadPaperRunner":
        """Factory pour MIB/ESTX50 spread (params validés WF corrigé 2026-04-18)."""
        cfg = SpreadConfig(
            strategy_id="mib_estx50_spread",
            sym_a="MIB",
            sym_b="ESTX50",
            mult_a=5.0,
            mult_b=10.0,
            comm_a=2.5,
            comm_b=2.0,
            tick_a=5.0,
            tick_b=1.0,
        )
        return cls(cfg)

    def _load_state(self) -> None:
        if self.state_path.exists():
            data = json.loads(self.state_path.read_text())
            self.position: SpreadPosition | None = (
                SpreadPosition(**data["position"]) if data.get("position") else None
            )
        else:
            self.position = None

    def _save_state(self) -> None:
        data = {
            "position": asdict(self.position) if self.position else None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.state_path.write_text(json.dumps(data, indent=2))

    def _append_trade(self, trade: dict[str, Any]) -> None:
        with self.trades_path.open("a") as f:
            f.write(json.dumps(trade) + "\n")

    def _log_ratio_history(
        self, bars_a: pd.Series, bars_b: pd.Series
    ) -> pd.Series:
        """Aligne les 2 series sur dates communes et retourne log(A/B)."""
        df = pd.DataFrame({"a": bars_a, "b": bars_b}).dropna()
        return np.log(df["a"] / df["b"])

    def tick(
        self,
        today_a: float,
        today_b: float,
        history_a: pd.Series,
        history_b: pd.Series,
        as_of: str | None = None,
    ) -> dict[str, Any]:
        """Process 1 daily tick.

        Args:
            today_a, today_b: closes du jour pour leg A et leg B
            history_a, history_b: historique pandas Series (>=lookback bars)
            as_of: date du tick (default: aujourd'hui UTC)

        Returns:
            dict status: {action, z, position_state, ...}
        """
        as_of = as_of or datetime.now(timezone.utc).date().isoformat()
        cfg = self.cfg

        log_ratio = self._log_ratio_history(history_a, history_b)
        z = _zscore(log_ratio, cfg.lookback)
        if z is None:
            return {"action": "skip", "reason": "insufficient_data", "as_of": as_of}

        # === Position ouverte: check exit ===
        if self.position is not None:
            self.position.hold_days += 1
            pos = self.position

            exit_now = False
            reason = ""
            if pos.direction == "LONG_SPREAD" and z >= cfg.z_exit:
                exit_now, reason = True, "tp_z_revert"
            elif pos.direction == "SHORT_SPREAD" and z <= -cfg.z_exit:
                exit_now, reason = True, "tp_z_revert"
            elif pos.direction == "LONG_SPREAD" and z < -cfg.z_stop:
                exit_now, reason = True, "sl_z_blow"
            elif pos.direction == "SHORT_SPREAD" and z > cfg.z_stop:
                exit_now, reason = True, "sl_z_blow"
            elif pos.hold_days >= cfg.max_hold:
                exit_now, reason = True, "max_hold"

            if exit_now:
                sign_a = 1 if pos.direction == "LONG_SPREAD" else -1
                sign_b = -sign_a
                pnl_gross = (
                    (today_a - pos.entry_a) * cfg.mult_a * pos.n_a * sign_a
                    + (today_b - pos.entry_b) * cfg.mult_b * pos.n_b * sign_b
                )
                cost = _trade_costs(pos.n_a, pos.n_b, cfg)
                pnl_net = pnl_gross - cost

                trade_record = {
                    "type": "exit",
                    "as_of": as_of,
                    "entry_date": pos.entry_date,
                    "direction": pos.direction,
                    "n_a": pos.n_a,
                    "n_b": pos.n_b,
                    "entry_a": pos.entry_a,
                    "entry_b": pos.entry_b,
                    "exit_a": today_a,
                    "exit_b": today_b,
                    "entry_z": pos.entry_z,
                    "exit_z": z,
                    "hold_days": pos.hold_days,
                    "pnl_gross": pnl_gross,
                    "pnl_net": pnl_net,
                    "cost": cost,
                    "exit_reason": reason,
                }
                self._append_trade(trade_record)
                logger.info(
                    "SPREAD EXIT %s @ %s: PnL net EUR%+.0f (%s, hold %dj, z=%.2f)",
                    pos.direction, as_of, pnl_net, reason, pos.hold_days, z,
                )
                self.position = None
                self._save_state()
                return {
                    "action": "exit", "z": z, "reason": reason,
                    "pnl_net": pnl_net, "as_of": as_of,
                }

            # Hold: persist incremented hold_days
            self._save_state()
            return {
                "action": "hold", "z": z, "hold_days": pos.hold_days,
                "direction": pos.direction, "as_of": as_of,
            }

        # === Pas de position: check entry ===
        if z < -cfg.z_entry:
            direction = "LONG_SPREAD"
        elif z > cfg.z_entry:
            direction = "SHORT_SPREAD"
        else:
            return {"action": "no_signal", "z": z, "as_of": as_of}

        n_a, n_b = _hedge_ratio(today_a, today_b, cfg)
        self.position = SpreadPosition(
            entry_date=as_of,
            direction=direction,
            sym_a=cfg.sym_a, sym_b=cfg.sym_b,
            n_a=n_a, n_b=n_b,
            entry_a=today_a, entry_b=today_b,
            entry_z=z, hold_days=0,
        )
        notional_a = today_a * cfg.mult_a * n_a
        notional_b = today_b * cfg.mult_b * n_b
        trade_record = {
            "type": "entry",
            "as_of": as_of,
            "direction": direction,
            "n_a": n_a, "n_b": n_b,
            "entry_a": today_a, "entry_b": today_b,
            "entry_z": z,
            "notional_a_eur": notional_a,
            "notional_b_eur": notional_b,
        }
        self._append_trade(trade_record)
        self._save_state()
        logger.info(
            "SPREAD ENTRY %s @ %s: %d %s + %d %s, z=%.2f, notional EUR%.0fK + %.0fK",
            direction, as_of, n_a, cfg.sym_a, n_b, cfg.sym_b, z,
            notional_a / 1000, notional_b / 1000,
        )
        return {
            "action": "entry", "direction": direction, "z": z,
            "n_a": n_a, "n_b": n_b, "as_of": as_of,
        }
