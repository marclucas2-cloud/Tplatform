"""
Strategie OPT-005 : Protective Puts Overlay (tail risk hedge)

PAS une strategie de profit — c'est une assurance pour le portefeuille.

Logique :
- Quand VIX < 15 (protection pas chere) : acheter des puts SPY OTM a delta -0.10
- Taille : proteger 50% de l'exposition equity longue
- Roll mensuel (acheter le put du mois suivant quand l'actuel expire)
- Budget de cout : < 1% annualise du portefeuille

Signaux :
- VIX < 15 ET pas de position put active → generer BUY_PUT
- VIX > 25 → considerer la prise de profit (les puts ont apprecie)
- Expiration < 5 jours → roll vers le mois suivant

Genere des objets Signal avec action="LONG" et metadata incluant
delta, strike, expiry. Inclut le suivi des couts (prime payee vs
protection fournie).
"""
import pandas as pd
import numpy as np
from abc import ABC, abstractmethod
from datetime import time as dt_time, timedelta
from typing import Optional


# ── Signal & BaseStrategy (local definitions for standalone use) ─────────

class Signal:
    """Represente un signal de trading."""
    def __init__(
        self,
        action: str,
        ticker: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        timestamp: pd.Timestamp,
        metadata: dict = None,
    ):
        self.action = action
        self.ticker = ticker
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.timestamp = timestamp
        self.metadata = metadata or {}


class BaseStrategy(ABC):
    """Classe abstraite — chaque strategie implemente generate_signals()."""

    name: str = "BaseStrategy"

    @abstractmethod
    def generate_signals(self, data: dict, date) -> list:
        pass

    def get_required_tickers(self) -> list[str]:
        return []


# ── Strategy parameters ─────────────────────────────────────────────────

VIX_BUY_THRESHOLD = 15.0       # Buy puts when VIX < 15 (cheap)
VIX_PROFIT_THRESHOLD = 25.0    # Consider profit when VIX > 25
PUT_DELTA_TARGET = -0.10       # OTM delta target
PROTECTION_RATIO = 0.50        # Protect 50% of long equity exposure
ANNUAL_COST_BUDGET_PCT = 0.01  # Max 1% annualized cost
ROLL_DAYS_BEFORE_EXPIRY = 5    # Roll when < 5 days to expiry
DAYS_TO_EXPIRY_TARGET = 30     # Buy ~30 DTE puts
CAPITAL = 100_000.0

# Signal evaluation window
SIGNAL_WINDOW_START = dt_time(10, 0)
SIGNAL_WINDOW_END = dt_time(15, 30)

# Approximate put pricing (simplified Black-Scholes proxy)
# Used for cost estimation — real execution uses broker quotes
BASE_PUT_COST_PCT = 0.005      # ~0.5% of underlying for 30-DTE, delta -0.10


class ProtectivePutsOverlayStrategy(BaseStrategy):
    """
    Protective Puts Overlay — Tail Risk Hedge.

    NOT a profit strategy. This is portfolio insurance.
    Buys SPY puts OTM at delta -0.10 when VIX is cheap (< 15).
    Rolls monthly, budget < 1% annualized.

    In a crash scenario (VIX > 25), puts appreciate significantly,
    providing a hedge against the rest of the portfolio.
    """

    name = "Protective Puts Overlay"

    def __init__(
        self,
        vix_buy_threshold: float = VIX_BUY_THRESHOLD,
        vix_profit_threshold: float = VIX_PROFIT_THRESHOLD,
        delta_target: float = PUT_DELTA_TARGET,
        protection_ratio: float = PROTECTION_RATIO,
        annual_cost_budget_pct: float = ANNUAL_COST_BUDGET_PCT,
        roll_days: int = ROLL_DAYS_BEFORE_EXPIRY,
        dte_target: int = DAYS_TO_EXPIRY_TARGET,
        capital: float = CAPITAL,
    ):
        self.vix_buy_threshold = vix_buy_threshold
        self.vix_profit_threshold = vix_profit_threshold
        self.delta_target = delta_target
        self.protection_ratio = protection_ratio
        self.annual_cost_budget_pct = annual_cost_budget_pct
        self.roll_days = roll_days
        self.dte_target = dte_target
        self.capital = capital

        # State tracking
        self._active_put = None         # Current put position info
        self._ytd_premium_paid = 0.0    # Total premium paid this year
        self._ytd_premium_budget = capital * annual_cost_budget_pct

    def get_required_tickers(self) -> list[str]:
        """SPY for put underlying, VIX for timing."""
        return ["SPY", "VIX"]

    def _get_vix_level(self, data: dict[str, pd.DataFrame]) -> Optional[float]:
        """Extract latest VIX level."""
        if "VIX" not in data:
            return None
        df_vix = data["VIX"]
        if df_vix.empty:
            return None
        return float(df_vix["close"].iloc[-1])

    def _estimate_put_premium(
        self,
        spy_price: float,
        vix_level: float,
        dte: int,
    ) -> dict:
        """Estimate put premium using simplified model.

        Approximation based on VIX level and DTE.
        Real execution would use broker option chain quotes.

        Returns:
            {strike, premium_per_contract, delta, dte, contracts_needed, total_premium}
        """
        # Strike at delta -0.10 is roughly 10-15% OTM depending on VIX
        otm_pct = 0.10 + (vix_level / 100) * 0.05  # Higher VIX → farther OTM
        strike = round(spy_price * (1 - otm_pct), 0)

        # Premium estimation (simplified)
        # Base cost scales with VIX and DTE
        vix_factor = vix_level / 15.0  # Normalized to VIX=15
        dte_factor = (dte / 30.0) ** 0.5  # sqrt scaling with time
        premium_pct = BASE_PUT_COST_PCT * vix_factor * dte_factor
        premium_per_contract = spy_price * premium_pct  # Per 100 shares

        # Number of contracts needed to protect portfolio
        equity_exposure = self.capital * self.protection_ratio
        contracts_needed = max(1, int(equity_exposure / (spy_price * 100)))

        total_premium = premium_per_contract * contracts_needed * 100

        return {
            "strike": strike,
            "premium_per_contract": round(premium_per_contract, 2),
            "delta": self.delta_target,
            "dte": dte,
            "contracts_needed": contracts_needed,
            "total_premium": round(total_premium, 2),
            "cost_pct_of_portfolio": round(total_premium / self.capital * 100, 4),
        }

    def _check_cost_budget(self, total_premium: float) -> bool:
        """Check if buying puts would exceed annual cost budget.

        Budget: < 1% annualized of portfolio value.
        """
        projected_cost = self._ytd_premium_paid + total_premium
        return projected_cost <= self._ytd_premium_budget

    def _needs_roll(self, date) -> bool:
        """Check if current put position needs to be rolled."""
        if self._active_put is None:
            return False

        expiry = self._active_put.get("expiry")
        if expiry is None:
            return False

        if isinstance(date, pd.Timestamp):
            date = date.date()

        days_to_expiry = (expiry - date).days if hasattr(expiry, '__sub__') else self.roll_days + 1
        return days_to_expiry <= self.roll_days

    def _should_take_profit(self, vix_level: float) -> bool:
        """Check if VIX is high enough to take profit on puts."""
        return vix_level > self.vix_profit_threshold and self._active_put is not None

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        """
        Generate protective put signals.

        Logic:
        1. VIX < 15 AND no active put → BUY PUT
        2. VIX > 25 AND active put → TAKE PROFIT (sell put)
        3. Expiry < 5 days → ROLL (sell current, buy next month)

        data: {ticker: DataFrame with intraday OHLCV bars}
        date: trading date

        Returns list of Signal objects.
        """
        signals = []

        if "SPY" not in data:
            return signals

        spy_df = data["SPY"]
        if len(spy_df) < 10:
            return signals

        vix_level = self._get_vix_level(data)
        if vix_level is None:
            return signals

        # ── Find signal bars in evaluation window ──
        signal_bars = spy_df[
            (spy_df.index.time >= SIGNAL_WINDOW_START)
            & (spy_df.index.time <= SIGNAL_WINDOW_END)
        ]

        if signal_bars.empty:
            return signals

        latest_bar = signal_bars.iloc[-1]
        latest_ts = signal_bars.index[-1]
        spy_price = float(latest_bar["close"])

        # ── Signal 1: Take profit on existing puts when VIX spikes ──
        if self._should_take_profit(vix_level):
            # Estimate profit — puts bought at low VIX are now worth more
            buy_vix = self._active_put.get("buy_vix", 14.0)
            appreciation = (vix_level / buy_vix - 1.0)  # Rough proxy

            if appreciation > 0.5:  # Puts appreciated > 50%
                signals.append(Signal(
                    action="SHORT",  # Sell the put
                    ticker="SPY",
                    entry_price=spy_price,
                    stop_loss=spy_price * 1.05,   # Nominal (not directional)
                    take_profit=spy_price * 0.95,
                    timestamp=latest_ts,
                    metadata={
                        "strategy": self.name,
                        "signal_type": "SELL_PUT_PROFIT",
                        "vix_level": round(vix_level, 2),
                        "buy_vix": buy_vix,
                        "appreciation_pct": round(appreciation * 100, 1),
                        "instrument": "SPY_PUT",
                    },
                ))
                self._active_put = None
                return signals

        # ── Signal 2: Roll existing puts near expiry ──
        if self._needs_roll(date):
            put_info = self._estimate_put_premium(spy_price, vix_level, self.dte_target)

            if self._check_cost_budget(put_info["total_premium"]):
                # Compute expiry date for the new put
                if isinstance(date, pd.Timestamp):
                    current_date = date.date()
                else:
                    current_date = date
                new_expiry = current_date + timedelta(days=self.dte_target)

                signals.append(Signal(
                    action="LONG",  # Buy new put (roll)
                    ticker="SPY",
                    entry_price=spy_price,
                    stop_loss=0.0,  # No stop on insurance
                    take_profit=0.0,  # No TP — it's a hedge
                    timestamp=latest_ts,
                    metadata={
                        "strategy": self.name,
                        "signal_type": "ROLL_PUT",
                        "instrument": "SPY_PUT",
                        "delta": self.delta_target,
                        "strike": put_info["strike"],
                        "dte": self.dte_target,
                        "expiry": str(new_expiry),
                        "premium_per_contract": put_info["premium_per_contract"],
                        "contracts": put_info["contracts_needed"],
                        "total_premium": put_info["total_premium"],
                        "cost_pct": put_info["cost_pct_of_portfolio"],
                        "vix_level": round(vix_level, 2),
                        "ytd_premium_paid": round(self._ytd_premium_paid, 2),
                    },
                ))

                self._ytd_premium_paid += put_info["total_premium"]
                self._active_put = {
                    "strike": put_info["strike"],
                    "expiry": new_expiry,
                    "buy_vix": vix_level,
                    "premium": put_info["total_premium"],
                }
            return signals

        # ── Signal 3: Buy new puts when VIX is cheap ──
        if vix_level < self.vix_buy_threshold and self._active_put is None:
            put_info = self._estimate_put_premium(spy_price, vix_level, self.dte_target)

            if not self._check_cost_budget(put_info["total_premium"]):
                return signals  # Over budget

            if isinstance(date, pd.Timestamp):
                current_date = date.date()
            else:
                current_date = date
            new_expiry = current_date + timedelta(days=self.dte_target)

            signals.append(Signal(
                action="LONG",
                ticker="SPY",
                entry_price=spy_price,
                stop_loss=0.0,   # No stop on insurance
                take_profit=0.0,  # No TP — it's a hedge
                timestamp=latest_ts,
                metadata={
                    "strategy": self.name,
                    "signal_type": "BUY_PUT",
                    "instrument": "SPY_PUT",
                    "delta": self.delta_target,
                    "strike": put_info["strike"],
                    "dte": self.dte_target,
                    "expiry": str(new_expiry),
                    "premium_per_contract": put_info["premium_per_contract"],
                    "contracts": put_info["contracts_needed"],
                    "total_premium": put_info["total_premium"],
                    "cost_pct": put_info["cost_pct_of_portfolio"],
                    "vix_level": round(vix_level, 2),
                    "ytd_premium_paid": round(self._ytd_premium_paid, 2),
                    "budget_remaining": round(
                        self._ytd_premium_budget - self._ytd_premium_paid - put_info["total_premium"], 2
                    ),
                },
            ))

            self._ytd_premium_paid += put_info["total_premium"]
            self._active_put = {
                "strike": put_info["strike"],
                "expiry": new_expiry,
                "buy_vix": vix_level,
                "premium": put_info["total_premium"],
            }

        return signals

    def get_cost_summary(self) -> dict:
        """Return a summary of premium costs for monitoring."""
        return {
            "ytd_premium_paid": round(self._ytd_premium_paid, 2),
            "ytd_budget": round(self._ytd_premium_budget, 2),
            "budget_used_pct": round(
                (self._ytd_premium_paid / self._ytd_premium_budget * 100)
                if self._ytd_premium_budget > 0
                else 0.0,
                2,
            ),
            "active_put": self._active_put is not None,
            "active_put_details": self._active_put,
        }

    def reset_annual_budget(self):
        """Reset annual premium budget (call at start of new year)."""
        self._ytd_premium_paid = 0.0
        self._ytd_premium_budget = self.capital * self.annual_cost_budget_pct
