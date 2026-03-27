"""
ALLOC-002 : Kelly Criterion Calculator

Calcule la fraction de Kelly optimale pour chaque strategie,
puis applique un quart-Kelly (fraction=0.25) pour le sizing prudent
en debut de live trading.

Formule de Kelly :
  f* = (p * b - q) / b
  avec p = win_rate, q = 1-p, b = avg_win / avg_loss

Usage :
  from core.kelly_calculator import KellyCalculator
  kc = KellyCalculator()
  kelly = kc.calculate_kelly(win_rate=0.55, avg_win=1.5, avg_loss=1.0)
  frac = kc.calculate_fractional_kelly(0.55, 1.5, 1.0, fraction=0.25)
"""
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class KellyCalculator:
    """Kelly Criterion pour le sizing des strategies."""

    def calculate_kelly(self, win_rate: float, avg_win: float, avg_loss: float) -> float:
        """
        Kelly criterion classique : f* = (p*b - q) / b
        avec b = avg_win / avg_loss.

        Args:
            win_rate: taux de gain (0 a 1)
            avg_win:  gain moyen absolu ($)
            avg_loss: perte moyenne absolue ($, positif)

        Returns:
            Fraction optimale du capital a risquer (peut etre negatif = pas de trade).
        """
        if avg_loss <= 0 or avg_win <= 0 or not (0 <= win_rate <= 1):
            logger.warning("Parametres invalides pour Kelly: wr=%.2f, w=%.2f, l=%.2f",
                           win_rate, avg_win, avg_loss)
            return 0.0

        b = avg_win / avg_loss
        p = win_rate
        q = 1.0 - p

        kelly = (p * b - q) / b

        logger.debug("Kelly: wr=%.2f, b=%.2f -> f*=%.4f", win_rate, b, kelly)
        return round(kelly, 6)

    def calculate_fractional_kelly(
        self,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
        fraction: float = 0.25,
    ) -> float:
        """
        Quart-Kelly (par defaut) pour le live initial.
        Plus conservateur, reduit le drawdown attendu significativement.

        Args:
            win_rate: taux de gain (0 a 1)
            avg_win:  gain moyen absolu ($)
            avg_loss: perte moyenne absolue ($, positif)
            fraction: fraction de Kelly a appliquer (defaut 0.25)

        Returns:
            Fraction du capital a risquer (toujours >= 0).
        """
        full_kelly = self.calculate_kelly(win_rate, avg_win, avg_loss)

        # Ne jamais retourner une fraction negative
        frac_kelly = max(0.0, full_kelly * fraction)

        logger.debug("Fractional Kelly (%.0f%%): f*=%.4f -> frac=%.4f",
                      fraction * 100, full_kelly, frac_kelly)
        return round(frac_kelly, 6)

    def calculate_for_portfolio(
        self,
        strategies: Dict[str, dict],
        total_capital: float = 25_000,
        fraction: float = 0.25,
    ) -> Dict[str, dict]:
        """
        Calcule le Kelly et le sizing recommande pour un portefeuille de strategies.

        Args:
            strategies: {
                name: {
                    "win_rate": float,
                    "avg_win": float,   # gain moyen absolu
                    "avg_loss": float,  # perte moyenne absolue
                    "sharpe": float,    # optionnel, pour info
                    "n_trades": int,    # optionnel, pour info
                }
            }
            total_capital: capital total disponible ($)
            fraction: fraction de Kelly (defaut 0.25)

        Returns:
            {
                name: {
                    "full_kelly": float,
                    "fractional_kelly": float,
                    "recommended_pct": float,  # en %
                    "recommended_capital": float,  # en $
                    "max_position_size": float,  # en $
                }
            }
        """
        results = {}
        total_alloc = 0.0

        for name, s in strategies.items():
            wr = s.get("win_rate", 0)
            aw = s.get("avg_win", 0)
            al = s.get("avg_loss", 0)

            full_k = self.calculate_kelly(wr, aw, al)
            frac_k = self.calculate_fractional_kelly(wr, aw, al, fraction)

            # Cap a 25% max par strategie (meme si Kelly dit plus)
            capped = min(frac_k, 0.25)

            results[name] = {
                "full_kelly": round(full_k * 100, 2),            # en %
                "fractional_kelly": round(frac_k * 100, 2),      # en %
                "recommended_pct": round(capped * 100, 2),       # en %
                "recommended_capital": round(capped * total_capital, 2),
                "max_position_size": round(capped * total_capital, 2),
                "win_rate": round(wr * 100, 1),
                "avg_win": round(aw, 2),
                "avg_loss": round(al, 2),
                "n_trades": s.get("n_trades", 0),
                "sharpe": s.get("sharpe", 0),
            }
            total_alloc += capped

        # Normaliser si total > 100%
        if total_alloc > 1.0:
            scale = 1.0 / total_alloc
            for name in results:
                results[name]["recommended_pct"] = round(results[name]["recommended_pct"] * scale, 2)
                results[name]["recommended_capital"] = round(results[name]["recommended_capital"] * scale, 2)
                results[name]["max_position_size"] = round(results[name]["max_position_size"] * scale, 2)

        logger.info("Kelly portfolio: %d strategies, total_alloc=%.1f%%, capital=$%,.0f",
                     len(results), min(total_alloc, 1.0) * 100, total_capital)
        return results
