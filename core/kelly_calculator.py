"""
ALLOC-002 : Kelly Criterion Calculator

Calcule la fraction de Kelly optimale pour chaque strategie,
puis applique un quart-Kelly (fraction=0.25) pour le sizing prudent
en debut de live trading.

Formule de Kelly :
  f* = (p * b - q) / b
  avec p = win_rate, q = 1-p, b = avg_win / avg_loss

FX-001 : Ajout du calcul Kelly specifique FX
  - Couts FX ~0.01% RT (vs 0.15% equities)
  - Sizing en pips avec pip_value configurable
  - Distribution Sharpe-weighted de l'allocation FX

Usage :
  from core.kelly_calculator import KellyCalculator
  kc = KellyCalculator()
  kelly = kc.calculate_kelly(win_rate=0.55, avg_win=1.5, avg_loss=1.0)
  frac = kc.calculate_fractional_kelly(0.55, 1.5, 1.0, fraction=0.25)
  fx_kelly = kc.calculate_fx_kelly(0.52, 35.0, 25.0, pip_value=10.0)
"""
import logging
from typing import Dict

logger = logging.getLogger(__name__)


# Default FX Sharpe ratios for Sharpe-weighted distribution
FX_SHARPE_WEIGHTS = {
    "fx_eurusd_trend": 4.62,
    "fx_eurgbp_mr": 3.65,
    "fx_eurjpy_carry": 2.50,
    "fx_audjpy_carry": 1.58,
    "fx_gbpusd_trend": 2.00,
    "fx_usdchf_mr": 1.50,
    "fx_nzdusd_carry": 1.20,
}

# FX cost is ~0.01% round-trip (vs ~0.15% for equities)
FX_COST_RT_PCT = 0.0001
EQUITY_COST_RT_PCT = 0.0015


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

    # -----------------------------------------------------------------
    # FX-001 : Kelly specifique FX (couts reduits, sizing en pips)
    # -----------------------------------------------------------------

    def calculate_fx_kelly(
        self,
        win_rate: float,
        avg_win_pips: float,
        avg_loss_pips: float,
        pip_value: float = 10.0,
        fraction: float = 0.25,
        cost_rt_pct: float = FX_COST_RT_PCT,
    ) -> dict:
        """
        Kelly specifique FX avec couts reduits et sizing en pips.

        FX a des couts ~15x inferieurs aux equities (~0.01% vs ~0.15%).
        Le Kelly brut est donc plus genereux — on applique toujours un
        fractional Kelly pour rester prudent.

        Args:
            win_rate:       taux de gain (0 a 1)
            avg_win_pips:   gain moyen en pips (ex: 35.0)
            avg_loss_pips:  perte moyenne en pips (ex: 25.0, positif)
            pip_value:      valeur d'un pip en $ (defaut 10.0 pour mini lot)
            fraction:       fraction de Kelly (defaut 0.25)
            cost_rt_pct:    cout round-trip en pourcentage (defaut 0.01%)

        Returns:
            {
                "full_kelly": float,         # Kelly complet (fraction du capital)
                "fractional_kelly": float,   # Fraction appliquee
                "avg_win_net_pips": float,   # Gain moyen net de couts
                "avg_loss_net_pips": float,  # Perte moyenne nette de couts
                "cost_impact_pct": float,    # Impact des couts sur le Kelly
            }
        """
        if avg_loss_pips <= 0 or avg_win_pips <= 0 or not (0 <= win_rate <= 1):
            logger.warning(
                "Parametres FX invalides: wr=%.2f, w_pips=%.1f, l_pips=%.1f",
                win_rate, avg_win_pips, avg_loss_pips,
            )
            return {
                "full_kelly": 0.0,
                "fractional_kelly": 0.0,
                "avg_win_net_pips": 0.0,
                "avg_loss_net_pips": 0.0,
                "cost_impact_pct": 0.0,
            }

        # Convert pips to dollars for cost calculation
        avg_win_usd = avg_win_pips * pip_value
        avg_loss_usd = avg_loss_pips * pip_value

        # Apply FX costs (both wins and losses are reduced by costs)
        avg_trade_value = (avg_win_usd + avg_loss_usd) / 2.0
        cost_per_trade = avg_trade_value * cost_rt_pct

        avg_win_net = avg_win_usd - cost_per_trade
        avg_loss_net = avg_loss_usd + cost_per_trade

        # Convert back to pips for reporting
        avg_win_net_pips = avg_win_net / pip_value if pip_value > 0 else 0.0
        avg_loss_net_pips = avg_loss_net / pip_value if pip_value > 0 else 0.0

        # Kelly with net values
        full_kelly = self.calculate_kelly(win_rate, avg_win_net, avg_loss_net)
        frac_kelly = max(0.0, full_kelly * fraction)

        # Kelly without costs for comparison
        kelly_gross = self.calculate_kelly(win_rate, avg_win_usd, avg_loss_usd)
        cost_impact = 0.0
        if kelly_gross > 0:
            cost_impact = (kelly_gross - full_kelly) / kelly_gross

        result = {
            "full_kelly": round(full_kelly, 6),
            "fractional_kelly": round(frac_kelly, 6),
            "avg_win_net_pips": round(avg_win_net_pips, 2),
            "avg_loss_net_pips": round(avg_loss_net_pips, 2),
            "cost_impact_pct": round(cost_impact, 6),
        }

        logger.info(
            "FX Kelly: wr=%.2f, win=%.1f pips, loss=%.1f pips -> "
            "full=%.4f, frac=%.4f, cost_impact=%.4f%%",
            win_rate, avg_win_pips, avg_loss_pips,
            full_kelly, frac_kelly, cost_impact * 100,
        )
        return result

    def distribute_fx_allocation(
        self,
        total_fx_pct: float = 0.18,
        sharpe_weights: Dict[str, float] | None = None,
        total_capital: float = 25_000,
    ) -> Dict[str, dict]:
        """
        Distribue l'allocation FX totale entre les paires, ponderee par Sharpe.

        Args:
            total_fx_pct: allocation totale FX en fraction (defaut 0.18 = 18%)
            sharpe_weights: {pair_name: sharpe} (defaut FX_SHARPE_WEIGHTS)
            total_capital: capital total ($, defaut 25000)

        Returns:
            {
                pair_name: {
                    "sharpe": float,
                    "weight_pct": float,    # % du portefeuille total
                    "capital": float,       # $ alloues
                }
            }
        """
        weights = sharpe_weights or FX_SHARPE_WEIGHTS

        if not weights:
            return {}

        # Sharpe-weighted distribution (only positive Sharpe)
        positive = {k: max(v, 0) for k, v in weights.items()}
        total_sharpe = sum(positive.values())

        if total_sharpe <= 0:
            return {}

        result = {}
        for pair, sharpe in weights.items():
            pair_weight = max(sharpe, 0) / total_sharpe * total_fx_pct
            result[pair] = {
                "sharpe": round(sharpe, 2),
                "weight_pct": round(pair_weight, 6),
                "capital": round(pair_weight * total_capital, 2),
            }

        logger.info(
            "FX distribution: %d pairs, total=%.1f%%, capital=$%,.0f",
            len(result), total_fx_pct * 100, total_fx_pct * total_capital,
        )
        return result

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
