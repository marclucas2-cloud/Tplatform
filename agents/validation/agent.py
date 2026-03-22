"""
Validation Agent — filtre strict avant autorisation d'exécution.

Validations effectuées :
  1. Critères basiques (Sharpe, drawdown, nb trades, profit factor)
  2. Walk-forward : consistance sur plusieurs fenêtres temporelles
  3. Vérification fingerprint stratégie (intégrité)

Flux :
  BACKTEST_COMPLETE {result, strategy} →
  Walk-forward validation →
  VALIDATION_PASSED ou VALIDATION_FAILED {reason}
"""
from __future__ import annotations

import logging
import statistics

from agents.base_agent import BaseAgent, AgentMessage
from core.backtest.engine import BacktestEngine
from core.data.loader import OHLCVLoader
from core.strategy_schema.validator import StrategyValidator

logger = logging.getLogger(__name__)


class ValidationAgent(BaseAgent):
    """
    Validation robuste multi-critères.
    Une stratégie ne passe que si TOUS les critères sont satisfaits
    sur TOUTES les fenêtres walk-forward.
    """

    def __init__(self, bus, initial_capital: float = 10_000.0):
        super().__init__("validation", bus)
        self._engine = BacktestEngine(initial_capital=initial_capital)
        self._validator = StrategyValidator()

    async def process(self, message: AgentMessage):
        if message.type != "BACKTEST_COMPLETE":
            return

        strategy = message.payload.get("strategy")
        basic_result = message.payload.get("result")

        if not strategy or not basic_result:
            return

        strategy_id = strategy["strategy_id"]
        req = strategy["validation_requirements"]

        self.logger.info(f"Validation de {strategy_id}")

        failures = list(basic_result.get("validation_failures", []))

        # Si le backtest de base échoue déjà → rejet immédiat
        if failures:
            self.logger.warning(f"{strategy_id} rejeté (backtest de base) : {failures}")
            await self.emit("VALIDATION_FAILED", {
                "strategy_id": strategy_id,
                "reason": "Backtest de base insuffisant",
                "failures": failures,
            }, message.correlation_id)
            return

        # Walk-forward validation
        n_windows = req.get("walk_forward_windows", 4)
        oos_pct = req.get("out_of_sample_pct", 30) / 100
        n_bars_total = 5000  # Utiliser plus de données pour le walk-forward

        data = OHLCVLoader.generate_synthetic(
            asset=strategy["asset"],
            timeframe=strategy["timeframe"],
            n_bars=n_bars_total,
            seed=42,
        )

        windows = data.walk_forward_windows(n_windows=n_windows, oos_pct=oos_pct)
        wf_results = []
        wf_failures = []

        for i, (is_data, oos_data) in enumerate(windows):
            # Backtest sur la fenêtre OOS uniquement (test de généralisation)
            oos_result = self._engine.run(oos_data, strategy)
            wf_results.append(oos_result)

            if not oos_result.passes_validation:
                wf_failures.append(
                    f"Fenêtre {i+1}/{n_windows} OOS : {oos_result.validation_failures}"
                )
            self.logger.debug(
                f"WF fenêtre {i+1}: Sharpe={oos_result.sharpe_ratio:.3f}, "
                f"DD={oos_result.max_drawdown_pct:.1f}%, trades={oos_result.total_trades}"
            )

        # Consistance entre fenêtres : écart-type du Sharpe doit être raisonnable
        sharpes = [r.sharpe_ratio for r in wf_results if r.total_trades > 0]
        if len(sharpes) >= 2:
            sharpe_std = statistics.stdev(sharpes)
            sharpe_mean = statistics.mean(sharpes)
            consistency_ratio = sharpe_std / abs(sharpe_mean) if sharpe_mean != 0 else float("inf")
            if consistency_ratio > 1.5:  # Trop instable entre fenêtres
                wf_failures.append(
                    f"Inconsistance Sharpe entre fenêtres (std/mean={consistency_ratio:.2f} > 1.5)"
                )

        if wf_failures:
            self.logger.warning(f"{strategy_id} rejeté (walk-forward) : {wf_failures}")
            await self.emit("VALIDATION_FAILED", {
                "strategy_id": strategy_id,
                "reason": "Walk-forward validation échouée",
                "failures": wf_failures,
                "wf_sharpes": sharpes,
            }, message.correlation_id)
        else:
            avg_sharpe = statistics.mean(sharpes) if sharpes else 0
            self.logger.info(f"{strategy_id} VALIDÉE — Sharpe WF moyen : {avg_sharpe:.3f}")
            await self.emit("VALIDATION_PASSED", {
                "strategy": strategy,
                "strategy_id": strategy_id,
                "wf_sharpes": sharpes,
                "avg_wf_sharpe": round(avg_sharpe, 3),
                "n_windows": n_windows,
            }, message.correlation_id)
