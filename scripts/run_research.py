"""
Lance le pipeline Research → Backtest → Validation pour générer une nouvelle stratégie.

L'agent Research appelle Claude (ANTHROPIC_API_KEY requis) pour générer un JSON
de stratégie, qui est ensuite backtesté et validé automatiquement.

Usage :
    python scripts/run_research.py
    python scripts/run_research.py --asset EURUSD --timeframe 1H --style momentum
    python scripts/run_research.py --asset SPY --timeframe 1D --style mean_reversion
    python scripts/run_research.py --asset NVDA --timeframe 15M --style breakout
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ["RESEARCH_MODE"] = "api"  # Force le mode API Claude

from orchestrator.main import Orchestrator

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-20s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
# Réduire le bruit des libs externes
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

log = logging.getLogger("run_research")


# ─── Runner ───────────────────────────────────────────────────────────────────

async def run(asset: str, timeframe: str, style: str, timeout: int = 120):
    """
    Lance le pipeline complet et attend le résultat VALIDATION_PASSED/FAILED.
    Capture le résultat final via un asyncio.Event.
    """
    result_event = asyncio.Event()
    result_data: dict = {}

    # ── Sous-classe MonitoringAgent pour capturer le résultat final ──────────
    from agents.base_agent import AgentMessage
    from agents.monitoring.agent import MonitoringAgent

    class ResultCapture(MonitoringAgent):
        async def process(self, message: AgentMessage):
            await super().process(message)

            if message.type == "VALIDATION_PASSED":
                result_data["status"]    = "VALIDE"
                result_data["strategy"]  = message.payload.get("strategy", {})
                result_data["wf_sharpes"] = message.payload.get("wf_sharpes", [])
                result_data["mc_metrics"] = message.payload.get("mc_metrics", {})
                result_data["avg_wf_sharpe"] = message.payload.get("avg_wf_sharpe", 0)
                result_event.set()

            elif message.type == "VALIDATION_FAILED":
                result_data["status"]   = "REJETE"
                result_data["reason"]   = message.payload.get("reason", "")
                result_data["failures"] = message.payload.get("failures", [])
                result_data["wf_sharpes"] = message.payload.get("wf_sharpes", [])
                result_data["mc_metrics"] = message.payload.get("mc_metrics", {})
                result_event.set()

    # ── Patcher l'orchestrateur pour injecter notre MonitoringAgent ──────────
    import agents.monitoring.agent as mon_module
    original_cls = mon_module.MonitoringAgent
    mon_module.MonitoringAgent = ResultCapture

    orch = Orchestrator(initial_capital=10_000.0)

    # Restaurer après instanciation
    mon_module.MonitoringAgent = original_cls

    print(f"\n{'='*65}")
    print("  RESEARCH AGENT — Generation de strategie")
    print(f"{'='*65}")
    print(f"  Asset      : {asset}")
    print(f"  Timeframe  : {timeframe}")
    print(f"  Style      : {style}")
    print("  Pipeline   : Research -> Backtest -> Validation")
    print(f"{'='*65}\n")

    await orch.start()

    # Envoyer la requête de recherche
    await orch.send("RESEARCH_REQUEST", {
        "asset":     asset,
        "timeframe": timeframe,
        "style":     style,
    })

    # Attendre le résultat (avec timeout)
    try:
        await asyncio.wait_for(result_event.wait(), timeout=timeout)
    except TimeoutError:
        log.error(f"Timeout ({timeout}s) — aucun résultat reçu")
        await orch.stop()
        return None

    await orch.stop()
    return result_data


def print_result(result: dict):
    """Affiche le résultat final de façon lisible."""
    if not result:
        return

    status = result.get("status", "?")
    print(f"\n{'='*65}")
    print(f"  RESULTAT : {status}")
    print(f"{'='*65}")

    if status == "VALIDE":
        s = result.get("strategy", {})
        print(f"  Strategie   : {s.get('strategy_id', '?')}")
        print(f"  Asset       : {s.get('asset', '?')}  {s.get('timeframe', '?')}")

        wf = result.get("wf_sharpes", [])
        if wf:
            print(f"\n  Walk-Forward Sharpes : {[round(x, 3) for x in wf]}")
            print(f"  WF Sharpe moyen      : {result.get('avg_wf_sharpe', 0):.3f}")

        mc = result.get("mc_metrics", {})
        if mc:
            print(f"\n  Monte Carlo ({mc.get('n_simulations', 0)} sims) :")
            print(f"    Sharpe P5  : {mc.get('sharpe_p5', 0):.3f}")
            print(f"    Sharpe P50 : {mc.get('sharpe_p50', 0):.3f}")
            print(f"    DD P95     : {mc.get('dd_p95', 0):.1f}%")
            print(f"    % positif  : {mc.get('pct_positive', 0):.0f}%")

        print(f"\n  Fichier sauvegarde : strategies/{s.get('strategy_id', '?')}.json")

    else:
        print(f"  Raison   : {result.get('reason', '?')}")
        for f in result.get("failures", []):
            print(f"    - {f}")

        wf = result.get("wf_sharpes", [])
        if wf:
            print(f"\n  WF Sharpes : {[round(x, 3) for x in wf]}")

        mc = result.get("mc_metrics", {})
        if mc:
            print(f"  MC Sharpe P5 : {mc.get('sharpe_p5', 0):.3f}")

    print(f"{'='*65}\n")


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Lance le Research Agent pour générer une stratégie")
    parser.add_argument("--asset",     default="EURUSD", help="Actif cible (ex: EURUSD, SPY, NVDA)")
    parser.add_argument("--timeframe", default="1H",     help="Timeframe (ex: 1M, 5M, 15M, 1H, 1D)")
    parser.add_argument("--style",     default="mean_reversion",
                        help="Style : mean_reversion | momentum | breakout | trend_following")
    parser.add_argument("--timeout",   type=int, default=120, help="Timeout en secondes")
    args = parser.parse_args()

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("\nErreur : ANTHROPIC_API_KEY non defini.")
        print("Definir la variable d'environnement et relancer.\n")
        sys.exit(1)

    result = asyncio.run(run(
        asset=args.asset,
        timeframe=args.timeframe,
        style=args.style,
        timeout=args.timeout,
    ))

    print_result(result)


if __name__ == "__main__":
    main()
