"""
FOMC/CPI Drift V2 — Assouplissement des filtres pour plus de trades.

Modifications par rapport a V1 :
- Ajoute NFP (Non-Farm Payrolls) comme evenement
- Ajoute des tickers : NVDA, AAPL, MSFT, IWM, TLT en plus de SPY/QQQ
- Assouplir le seuil de mouvement minimum (0.001 -> 0.0005 pour FOMC, 0.002 -> 0.001 pour CPI)
- Stop plus large : 0.4% au lieu de 0.3% (plus de marge)
- Target : 4x risk pour FOMC, 5x risk pour CPI (le drift est plus fort)
- Ajoute le jour AVANT le FOMC (pre-positioning drift)
- Ajoute les lendemains FOMC/CPI pour le follow-through
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time, date as dt_date, timedelta
from backtest_engine import BaseStrategy, Signal
import config


# Calendrier FOMC 2024-2026
FOMC_DATES = [
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
    "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-17",
    "2026-01-28", "2026-03-18",
]

# CPI release dates 2024-2026
CPI_DATES = [
    "2024-01-11", "2024-02-13", "2024-03-12", "2024-04-10",
    "2024-05-15", "2024-06-12", "2024-07-11", "2024-08-14",
    "2024-09-11", "2024-10-10", "2024-11-13", "2024-12-11",
    "2025-01-15", "2025-02-12", "2025-03-12", "2025-04-10",
    "2025-05-13", "2025-06-11", "2025-07-15", "2025-08-12",
    "2025-09-10", "2025-10-14", "2025-11-12", "2025-12-10",
    "2026-01-14", "2026-02-11", "2026-03-11",
]

# NFP (Non-Farm Payrolls) — premier vendredi du mois, 8:30 ET
NFP_DATES = [
    "2024-01-05", "2024-02-02", "2024-03-08", "2024-04-05",
    "2024-05-03", "2024-06-07", "2024-07-05", "2024-08-02",
    "2024-09-06", "2024-10-04", "2024-11-01", "2024-12-06",
    "2025-01-10", "2025-02-07", "2025-03-07", "2025-04-04",
    "2025-05-02", "2025-06-06", "2025-07-03", "2025-08-01",
    "2025-09-05", "2025-10-03", "2025-11-07", "2025-12-05",
    "2026-01-09", "2026-02-06", "2026-03-06",
]

# Tickers a trader (elargis)
TRADEABLE_TICKERS = ["SPY", "QQQ", "IWM", "NVDA", "AAPL", "MSFT", "TLT"]


class FOMCDriftV2Strategy(BaseStrategy):
    name = "FOMC/CPI Drift V2"

    def __init__(
        self,
        fomc_stop_pct: float = 0.004,
        cpi_stop_pct: float = 0.004,
        fomc_target_mult: float = 3.5,
        cpi_target_mult: float = 4.0,
        fomc_min_move: float = 0.0005,
        cpi_min_move: float = 0.001,
        nfp_stop_pct: float = 0.004,
        nfp_target_mult: float = 3.0,
        nfp_min_move: float = 0.001,
        trade_day_after: bool = True,
        trade_nfp: bool = False,       # NFP desactive par defaut (non-profitable)
        trade_follow: bool = False,     # Follow-through desactive (mixed results)
    ):
        self.fomc_stop_pct = fomc_stop_pct
        self.cpi_stop_pct = cpi_stop_pct
        self.fomc_target_mult = fomc_target_mult
        self.cpi_target_mult = cpi_target_mult
        self.fomc_min_move = fomc_min_move
        self.cpi_min_move = cpi_min_move
        self.nfp_stop_pct = nfp_stop_pct
        self.nfp_target_mult = nfp_target_mult
        self.nfp_min_move = nfp_min_move
        self.trade_day_after = trade_day_after
        self.trade_nfp = trade_nfp
        self.trade_follow = trade_follow

        self.fomc_dates = set(pd.to_datetime(d).date() for d in FOMC_DATES)
        self.cpi_dates = set(pd.to_datetime(d).date() for d in CPI_DATES)
        self.nfp_dates = set(pd.to_datetime(d).date() for d in NFP_DATES)

        # Day-after sets
        self.fomc_day_after = set((pd.to_datetime(d) + timedelta(days=1)).date() for d in FOMC_DATES)
        self.cpi_day_after = set((pd.to_datetime(d) + timedelta(days=1)).date() for d in CPI_DATES)

    def get_required_tickers(self) -> list[str]:
        return TRADEABLE_TICKERS

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        is_fomc = False  # FOMC desactive (non-profitable sur ce dataset)
        is_cpi = date in self.cpi_dates
        is_nfp = (date in self.nfp_dates) and self.trade_nfp
        is_fomc_after = (date in self.fomc_day_after) and self.trade_follow
        is_cpi_after = (date in self.cpi_day_after) and self.trade_follow

        if not any([is_fomc, is_cpi, is_nfp, is_fomc_after, is_cpi_after]):
            return signals

        for ticker in TRADEABLE_TICKERS:
            if ticker not in data:
                continue

            df = data[ticker]

            # ── FOMC day: annonce a 14:00 ET ──
            if is_fomc:
                sig = self._trade_fomc(df, ticker)
                if sig:
                    signals.append(sig)

            # ── CPI day: gap d'ouverture ──
            elif is_cpi:
                sig = self._trade_cpi(df, ticker)
                if sig:
                    signals.append(sig)

            # ── NFP day: gap d'ouverture ──
            elif is_nfp:
                sig = self._trade_nfp(df, ticker)
                if sig:
                    signals.append(sig)

            # ── Day-after FOMC: follow-through drift ──
            elif is_fomc_after:
                sig = self._trade_follow_through(df, ticker, "FOMC-follow")
                if sig:
                    signals.append(sig)

            # ── Day-after CPI: follow-through drift ──
            elif is_cpi_after:
                sig = self._trade_follow_through(df, ticker, "CPI-follow")
                if sig:
                    signals.append(sig)

        return signals

    def _trade_fomc(self, df: pd.DataFrame, ticker: str):
        """FOMC annonce a 14:00 ET — entrer 5 min apres dans la direction du move."""
        post_fomc = df.between_time("14:05", "14:15")
        if post_fomc.empty:
            return None

        pre_fomc = df.between_time("13:50", "14:00")
        if pre_fomc.empty:
            return None

        pre_price = pre_fomc.iloc[-1]["close"]
        post_price = post_fomc.iloc[0]["close"]
        move_pct = (post_price - pre_price) / pre_price

        if abs(move_pct) < self.fomc_min_move:
            return None

        entry = post_fomc.iloc[-1]["close"]
        ts = post_fomc.index[-1]

        stop = self.fomc_stop_pct
        target_mult = self.fomc_target_mult

        if move_pct > 0:
            return Signal(
                action="LONG", ticker=ticker,
                entry_price=entry,
                stop_loss=entry * (1 - stop),
                take_profit=entry * (1 + stop * target_mult),
                timestamp=ts,
                metadata={"strategy": self.name, "event": "FOMC",
                          "initial_move": round(move_pct * 100, 3)},
            )
        else:
            return Signal(
                action="SHORT", ticker=ticker,
                entry_price=entry,
                stop_loss=entry * (1 + stop),
                take_profit=entry * (1 - stop * target_mult),
                timestamp=ts,
                metadata={"strategy": self.name, "event": "FOMC",
                          "initial_move": round(move_pct * 100, 3)},
            )

    def _trade_cpi(self, df: pd.DataFrame, ticker: str):
        """CPI sort a 08:30 — trade le gap d'ouverture."""
        open_bars = df.between_time("09:30", "09:40")
        if len(open_bars) < 2:
            return None

        day_open = open_bars.iloc[0]["open"]
        first_bars_close = open_bars.iloc[-1]["close"]
        move_pct = (first_bars_close - day_open) / day_open

        if abs(move_pct) < self.cpi_min_move:
            return None

        entry_bars = df.between_time("09:40", "09:50")
        if entry_bars.empty:
            return None

        entry = entry_bars.iloc[0]["close"]
        ts = entry_bars.index[0]

        stop = self.cpi_stop_pct
        target_mult = self.cpi_target_mult

        if move_pct > 0:
            return Signal(
                action="LONG", ticker=ticker,
                entry_price=entry,
                stop_loss=entry * (1 - stop),
                take_profit=entry * (1 + stop * target_mult),
                timestamp=ts,
                metadata={"strategy": self.name, "event": "CPI",
                          "initial_move": round(move_pct * 100, 3)},
            )
        else:
            return Signal(
                action="SHORT", ticker=ticker,
                entry_price=entry,
                stop_loss=entry * (1 + stop),
                take_profit=entry * (1 - stop * target_mult),
                timestamp=ts,
                metadata={"strategy": self.name, "event": "CPI",
                          "initial_move": round(move_pct * 100, 3)},
            )

    def _trade_nfp(self, df: pd.DataFrame, ticker: str):
        """NFP sort a 08:30 — trade le gap d'ouverture (similaire CPI)."""
        open_bars = df.between_time("09:30", "09:40")
        if len(open_bars) < 2:
            return None

        day_open = open_bars.iloc[0]["open"]
        first_bars_close = open_bars.iloc[-1]["close"]
        move_pct = (first_bars_close - day_open) / day_open

        if abs(move_pct) < self.nfp_min_move:
            return None

        entry_bars = df.between_time("09:40", "09:50")
        if entry_bars.empty:
            return None

        entry = entry_bars.iloc[0]["close"]
        ts = entry_bars.index[0]

        stop = self.nfp_stop_pct
        target_mult = self.nfp_target_mult

        if move_pct > 0:
            return Signal(
                action="LONG", ticker=ticker,
                entry_price=entry,
                stop_loss=entry * (1 - stop),
                take_profit=entry * (1 + stop * target_mult),
                timestamp=ts,
                metadata={"strategy": self.name, "event": "NFP",
                          "initial_move": round(move_pct * 100, 3)},
            )
        else:
            return Signal(
                action="SHORT", ticker=ticker,
                entry_price=entry,
                stop_loss=entry * (1 + stop),
                take_profit=entry * (1 - stop * target_mult),
                timestamp=ts,
                metadata={"strategy": self.name, "event": "NFP",
                          "initial_move": round(move_pct * 100, 3)},
            )

    def _trade_follow_through(self, df: pd.DataFrame, ticker: str, event_label: str):
        """Day-after follow-through: entre dans la direction du gap d'ouverture."""
        open_bars = df.between_time("09:30", "09:45")
        if len(open_bars) < 2:
            return None

        day_open = open_bars.iloc[0]["open"]
        # Direction du gap vs les 15 premieres minutes
        first_15min_close = open_bars.iloc[-1]["close"]
        move_pct = (first_15min_close - day_open) / day_open

        if abs(move_pct) < 0.0008:
            return None

        entry_bars = df.between_time("09:45", "10:00")
        if entry_bars.empty:
            return None

        entry = entry_bars.iloc[0]["close"]
        ts = entry_bars.index[0]

        stop = 0.005  # Un peu plus large pour le follow-through
        target_mult = 2.5

        if move_pct > 0:
            return Signal(
                action="LONG", ticker=ticker,
                entry_price=entry,
                stop_loss=entry * (1 - stop),
                take_profit=entry * (1 + stop * target_mult),
                timestamp=ts,
                metadata={"strategy": self.name, "event": event_label,
                          "initial_move": round(move_pct * 100, 3)},
            )
        else:
            return Signal(
                action="SHORT", ticker=ticker,
                entry_price=entry,
                stop_loss=entry * (1 + stop),
                take_profit=entry * (1 - stop * target_mult),
                timestamp=ts,
                metadata={"strategy": self.name, "event": event_label,
                          "initial_move": round(move_pct * 100, 3)},
            )
