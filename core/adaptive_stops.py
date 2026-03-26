"""
OPT-B2 : Stops ATR adaptatifs.

Remplace les stops en % fixe par des stops bases sur l'ATR (Average True Range),
adaptes au regime de marche et a la strategie.

Principe :
  - Bull  → multiplicateurs serres (capture les gains, coupe vite)
  - Bear  → multiplicateurs larges (evite les stop-hunts en haute vol)
"""

import logging

logger = logging.getLogger(__name__)


class AdaptiveStopCalculator:
    """Calcule les stops bases sur l'ATR au lieu de % fixe."""

    # Multiplicateurs ATR par strategie et regime
    MULTIPLIERS = {
        "opex_gamma": {"bull": 1.0, "bear": 1.5},
        "gap_continuation": {"bull": 1.2, "bear": 1.8},
        "vwap_micro": {"bull": 1.5, "bear": 2.0},
        "crypto_proxy_v2": {"bull": 2.0, "bear": 2.5},
        "dow_seasonal": {"bull": 1.0, "bear": 1.5},
        "gold_fear": {"bull": 1.5, "bear": 2.0},
        "orb_v2": {"bull": 1.2, "bear": 1.8},
        "meanrev_v2": {"bull": 1.5, "bear": 2.0},
        "corr_hedge": {"bull": 1.5, "bear": 2.0},
        "triple_ema": {"bull": 1.0, "bear": 1.5},
        "lateday_meanrev": {"bull": 1.2, "bear": 1.8},
        "default": {"bull": 1.5, "bear": 2.0},
    }

    def calculate_stop(
        self,
        entry_price: float,
        direction: str,
        atr: float,
        multiplier: float = 1.5,
    ) -> float:
        """Calcule le prix du stop-loss base sur l'ATR.

        Args:
            entry_price: prix d'entree de la position
            direction:   'BUY' (long) ou 'SELL' (short)
            atr:         Average True Range courant
            multiplier:  multiplicateur ATR (defaut 1.5)

        Returns:
            prix du stop-loss
        """
        if atr <= 0:
            logger.warning("ATR <= 0 (%.4f), using 1%% of entry as fallback", atr)
            atr = entry_price * 0.01

        if direction == "BUY":
            stop = entry_price - atr * multiplier
        else:
            stop = entry_price + atr * multiplier

        logger.debug(
            "Stop %s @ %.2f: ATR=%.4f x%.1f → stop=%.2f",
            direction,
            entry_price,
            atr,
            multiplier,
            stop,
        )
        return round(stop, 2)

    def calculate_take_profit(
        self,
        entry_price: float,
        direction: str,
        atr: float,
        risk_reward: float = 2.0,
        stop_multiplier: float = 1.5,
    ) -> float:
        """Calcule le take-profit base sur un ratio risk/reward.

        Args:
            entry_price:     prix d'entree
            direction:       'BUY' ou 'SELL'
            atr:             Average True Range
            risk_reward:     ratio TP/SL (defaut 2.0)
            stop_multiplier: multiplicateur ATR du stop

        Returns:
            prix du take-profit
        """
        risk = atr * stop_multiplier
        reward = risk * risk_reward

        if direction == "BUY":
            tp = entry_price + reward
        else:
            tp = entry_price - reward

        return round(tp, 2)

    def get_multiplier(self, strategy_name: str, regime: str) -> float:
        """Retourne le multiplicateur ATR par strategie et regime.

        Args:
            strategy_name: nom de la strategie (ex: 'opex_gamma')
            regime:        regime de marche (contenant 'BEAR' ou non)

        Returns:
            multiplicateur ATR (float)
        """
        m = self.MULTIPLIERS.get(strategy_name, self.MULTIPLIERS["default"])
        regime_key = "bear" if "BEAR" in regime else "bull"
        return m[regime_key]

    def get_bracket_params(
        self,
        entry_price: float,
        direction: str,
        atr: float,
        strategy_name: str,
        regime: str,
        risk_reward: float = 2.0,
    ) -> dict:
        """Retourne les parametres complets pour un bracket order.

        Args:
            entry_price:   prix d'entree
            direction:     'BUY' ou 'SELL'
            atr:           Average True Range
            strategy_name: nom de la strategie
            regime:        regime de marche
            risk_reward:   ratio TP/SL

        Returns:
            {stop_loss, take_profit, multiplier, atr}
        """
        multiplier = self.get_multiplier(strategy_name, regime)
        stop_loss = self.calculate_stop(entry_price, direction, atr, multiplier)
        take_profit = self.calculate_take_profit(
            entry_price, direction, atr, risk_reward, multiplier
        )

        return {
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "multiplier": multiplier,
            "atr": atr,
        }
