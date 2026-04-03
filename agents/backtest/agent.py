"""
Backtest Agent — orchestre les backtests via le BacktestEngine.

Flux :
  STRATEGY_READY {strategy} →
  Chargement données →
  BacktestEngine.run() →
  BACKTEST_COMPLETE {result}
"""
from __future__ import annotations

import logging

from agents.base_agent import AgentMessage, BaseAgent
from core.backtest.engine import BacktestEngine
from core.data.loader import OHLCVLoader

logger = logging.getLogger(__name__)


class BacktestAgent(BaseAgent):
    """
    Exécute les backtests sur demande.
    Délègue 100% du calcul au BacktestEngine — zéro logique ici.
    """

    def __init__(self, bus, initial_capital: float = 10_000.0):
        super().__init__("backtest", bus)
        self._engine = BacktestEngine(initial_capital=initial_capital)

    async def process(self, message: AgentMessage):
        if message.type != "STRATEGY_READY":
            return

        strategy = message.payload.get("strategy")
        if not strategy:
            self.logger.error("Message STRATEGY_READY sans payload strategy")
            return

        data_config = message.payload.get("data_config", {})
        asset = strategy["asset"]
        timeframe = strategy["timeframe"]

        self.logger.info(f"Backtest démarré : {strategy['strategy_id']} sur {asset} {timeframe}")

        # Chargement des données
        data_source = data_config.get("source", "synthetic")
        if data_source == "csv":
            data = OHLCVLoader.from_csv(
                path=data_config["path"],
                asset=asset,
                timeframe=timeframe,
            )
        else:
            # Données synthétiques pour développement/tests
            n_bars = data_config.get("n_bars", 3000)
            data = OHLCVLoader.generate_synthetic(asset=asset, timeframe=timeframe, n_bars=n_bars)
            self.logger.warning("Utilisation de données synthétiques — ne pas utiliser en prod")

        # Exécution du backtest
        result = self._engine.run(data, strategy)

        self.logger.info(f"Backtest terminé : {result.total_trades} trades, Sharpe={result.sharpe_ratio:.3f}")

        await self.emit(
            "BACKTEST_COMPLETE",
            {"result": result.to_dict(), "strategy": strategy},
            message.correlation_id,
        )
