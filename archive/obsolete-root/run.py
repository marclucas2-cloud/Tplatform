"""
Point d'entrée de la plateforme de trading.

Modes :
  python run.py                    # Pipeline complet (research → exécution)
  python run.py --mode backtest    # Backtest rapide sur une stratégie JSON
  python run.py --mode paper       # Paper trading IG démo
  python run.py --mode validate    # Validation walk-forward uniquement

Exemples :
  python run.py --mode backtest --strategy strategies/rsi_mean_reversion.json
  python run.py --mode paper --strategy strategies/rsi_mean_reversion.json
"""
import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Chargement des variables d'environnement EN PREMIER
load_dotenv()

from core.logging.audit import setup_logging
from core.strategy_schema.validator import StrategyValidator, StrategyValidationError


def parse_args():
    parser = argparse.ArgumentParser(description="Trading Platform — Multi-Agent System")
    parser.add_argument("--mode", choices=["full", "backtest", "paper", "validate"],
                        default="backtest", help="Mode d'exécution")
    parser.add_argument("--strategy", type=str,
                        default="strategies/rsi_mean_reversion.json",
                        help="Chemin vers le fichier JSON de stratégie")
    parser.add_argument("--log-level", type=str, default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--capital", type=float, default=10_000.0,
                        help="Capital initial en EUR")
    return parser.parse_args()


async def run_backtest_mode(strategy: dict, capital: float):
    """Mode backtest rapide — pas besoin des agents, moteur direct."""
    from core.backtest.engine import BacktestEngine
    from core.data.loader import OHLCVLoader

    logger = logging.getLogger("run.backtest")
    logger.info(f"Mode BACKTEST — stratégie : {strategy['strategy_id']}")

    # Données synthétiques pour demo (remplacer par CSV ou IG en prod)
    data = OHLCVLoader.generate_synthetic(
        asset=strategy["asset"],
        timeframe=strategy["timeframe"],
        n_bars=5000,
        seed=42,
    )
    logger.info(f"Données chargées : {data.n_bars} bougies ({data.source})")

    engine = BacktestEngine(initial_capital=capital)
    result = engine.run(data, strategy)

    print(result.summary())

    # Walk-forward validation
    logger.info("\nLancement walk-forward validation...")
    windows = data.walk_forward_windows(n_windows=4, oos_pct=0.3)
    wf_sharpes = []
    for i, (is_data, oos_data) in enumerate(windows):
        wf_result = engine.run(oos_data, strategy)
        wf_sharpes.append(wf_result.sharpe_ratio)
        logger.info(
            f"  Fenêtre OOS {i+1}/4 : Sharpe={wf_result.sharpe_ratio:.3f}, "
            f"DD={wf_result.max_drawdown_pct:.1f}%, trades={wf_result.total_trades}"
        )

    if wf_sharpes:
        import statistics
        print(f"\n  Walk-Forward Sharpe moyen : {statistics.mean(wf_sharpes):.3f}")
        print(f"  Walk-Forward Sharpe std   : {statistics.stdev(wf_sharpes):.3f}" if len(wf_sharpes) > 1 else "")

    return result


async def run_full_pipeline(strategy: dict, capital: float):
    """
    Pipeline complet multi-agents :
    Research → Backtest → Validation → Portfolio → Execution
    """
    from orchestrator.main import Orchestrator

    logger = logging.getLogger("run.pipeline")
    logger.info(f"Mode PIPELINE COMPLET — capital : {capital}€")

    orch = Orchestrator(initial_capital=capital)
    await orch.start()

    try:
        # Injecter la stratégie directement dans le pipeline
        # (bypasse le Research Agent pour utiliser le JSON existant)
        await orch.send("STRATEGY_READY", {
            "strategy": strategy,
            "data_config": {"source": "synthetic", "n_bars": 3000},
        })

        # Attendre que le pipeline traite les messages
        # En prod : remplacer par une boucle de trading temps réel
        await asyncio.sleep(5)

        metrics = orch.get_metrics()
        logger.info(f"Métriques finales : {json.dumps(metrics, indent=2, default=str)}")

    finally:
        await orch.stop()


async def main():
    args = parse_args()
    setup_logging(args.log_level)
    logger = logging.getLogger("run")

    # Chargement et validation de la stratégie
    validator = StrategyValidator()
    try:
        strategy = validator.load_and_validate(args.strategy)
        logger.info(f"Stratégie validée : {strategy['strategy_id']} (fingerprint={strategy['_fingerprint'][:12]}...)")
    except (FileNotFoundError, StrategyValidationError) as e:
        logger.error(f"Erreur stratégie : {e}")
        sys.exit(1)

    # Dispatch selon le mode
    if args.mode == "backtest":
        await run_backtest_mode(strategy, args.capital)
    elif args.mode in ("full", "paper"):
        await run_full_pipeline(strategy, args.capital)
    elif args.mode == "validate":
        result = await run_backtest_mode(strategy, args.capital)
        status = "VALIDÉE ✅" if result.passes_validation else "REJETÉE ❌"
        print(f"\nSTATUT FINAL : {status}")
        if not result.passes_validation:
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
