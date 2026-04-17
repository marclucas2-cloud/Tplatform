"""Phase 10 — Automated research funnel pipeline.

Orchestrates the full path from strategy idea to live promotion:
    1. Backtest (reproductible, with costs)
    2. Walk-forward validation (5 windows, >=3/5 OOS profitable)
    3. Monte Carlo simulation (1000 sims, P(DD>30%)<15%)
    4. Marginal portfolio score (delta_sharpe, delta_dd, correlation)
    5. Promotion committee checklist generation
    6. Status tracking (research → paper → candidate → live)

Usage:
    python scripts/research_funnel.py --strat <strategy_id> --step all
    python scripts/research_funnel.py --strat <strategy_id> --step backtest
    python scripts/research_funnel.py --strat <strategy_id> --step wf
    python scripts/research_funnel.py --strat <strategy_id> --step mc
    python scripts/research_funnel.py --strat <strategy_id> --step score
    python scripts/research_funnel.py --strat <strategy_id> --step promote
    python scripts/research_funnel.py --status            # Show funnel status
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FUNNEL_DIR = ROOT / "data" / "research_funnel"

FUNNEL_STEPS = [
    "backtest",      # Step 1: Reproductible backtest with costs
    "walk_forward",  # Step 2: WF 5 windows, >=3/5 OOS profitable
    "monte_carlo",   # Step 3: MC 1000 sims, P(DD>30%)<15%
    "portfolio_score",  # Step 4: Marginal score vs baseline
    "paper_run",     # Step 5: 30 days paper without divergence
    "promotion",     # Step 6: Promotion committee approval
]

GATE_CRITERIA = {
    "backtest": {
        "min_trades": 30,
        "min_sharpe": 0.5,
        "max_dd_pct": -40.0,
    },
    "walk_forward": {
        "min_windows_pass": 3,
        "total_windows": 5,
    },
    "monte_carlo": {
        "max_prob_dd_30": 0.15,
        "max_prob_ruin": 0.02,
        "min_simulations": 1000,
    },
    "portfolio_score": {
        "min_delta_sharpe": -0.05,
        "max_correlation": 0.70,
    },
    "paper_run": {
        "min_days": 30,
        "max_divergence_sigma": 2.0,
    },
}


def _load_funnel_state(strategy_id: str) -> dict:
    FUNNEL_DIR.mkdir(parents=True, exist_ok=True)
    state_file = FUNNEL_DIR / f"{strategy_id}.json"
    if state_file.exists():
        return json.loads(state_file.read_text(encoding="utf-8"))
    return {
        "strategy_id": strategy_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "current_step": "backtest",
        "steps": {},
    }


def _save_funnel_state(strategy_id: str, state: dict) -> None:
    FUNNEL_DIR.mkdir(parents=True, exist_ok=True)
    state_file = FUNNEL_DIR / f"{strategy_id}.json"
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    state_file.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def run_backtest_step(strategy_id: str) -> dict:
    """Step 1: Run or reference backtest results."""
    bt_dir = ROOT / "data" / "backtests"
    bt_files = list(bt_dir.glob(f"*{strategy_id}*")) if bt_dir.exists() else []

    result = {
        "step": "backtest",
        "ts": datetime.now(timezone.utc).isoformat(),
        "status": "NEEDS_MANUAL",
        "message": (
            f"Run backtest for {strategy_id} using BacktesterV2.\n"
            f"Found {len(bt_files)} existing backtest file(s).\n"
            f"Gates: trades>={GATE_CRITERIA['backtest']['min_trades']}, "
            f"Sharpe>={GATE_CRITERIA['backtest']['min_sharpe']}, "
            f"MaxDD<={GATE_CRITERIA['backtest']['max_dd_pct']}%"
        ),
        "existing_files": [str(f.name) for f in bt_files[:5]],
        "gates": GATE_CRITERIA["backtest"],
    }
    return result


def run_walk_forward_step(strategy_id: str) -> dict:
    """Step 2: Walk-forward validation."""
    wf_dir = ROOT / "data"
    wf_files = []
    for pattern in [f"*wf*{strategy_id}*", f"*{strategy_id}*wf*"]:
        wf_files.extend(wf_dir.rglob(pattern))

    result = {
        "step": "walk_forward",
        "ts": datetime.now(timezone.utc).isoformat(),
        "status": "NEEDS_MANUAL",
        "message": (
            f"Run walk-forward for {strategy_id}.\n"
            f"Use scripts/wf_crypto_all.py or scripts/wf_fx_all.py.\n"
            f"Gate: >={GATE_CRITERIA['walk_forward']['min_windows_pass']}/"
            f"{GATE_CRITERIA['walk_forward']['total_windows']} windows profitable."
        ),
        "existing_files": [str(f.name) for f in wf_files[:5]],
        "gates": GATE_CRITERIA["walk_forward"],
    }
    return result


def run_monte_carlo_step(strategy_id: str) -> dict:
    """Step 3: Monte Carlo simulation."""
    result = {
        "step": "monte_carlo",
        "ts": datetime.now(timezone.utc).isoformat(),
        "status": "NEEDS_MANUAL",
        "message": (
            f"Run Monte Carlo for {strategy_id}.\n"
            f"Gate: P(DD>30%)<={GATE_CRITERIA['monte_carlo']['max_prob_dd_30']*100}%, "
            f"P(ruin)<={GATE_CRITERIA['monte_carlo']['max_prob_ruin']*100}%, "
            f">={GATE_CRITERIA['monte_carlo']['min_simulations']} sims."
        ),
        "gates": GATE_CRITERIA["monte_carlo"],
    }
    return result


def run_portfolio_score_step(strategy_id: str) -> dict:
    """Step 4: Marginal portfolio score."""
    result = {
        "step": "portfolio_score",
        "ts": datetime.now(timezone.utc).isoformat(),
        "status": "NEEDS_MANUAL",
        "message": (
            f"Calculate marginal portfolio impact for {strategy_id}.\n"
            f"Metrics: delta_sharpe, delta_maxDD, delta_CAGR, correlation.\n"
            f"Gate: delta_sharpe>={GATE_CRITERIA['portfolio_score']['min_delta_sharpe']}, "
            f"corr<={GATE_CRITERIA['portfolio_score']['max_correlation']}."
        ),
        "gates": GATE_CRITERIA["portfolio_score"],
    }
    return result


def run_paper_run_step(strategy_id: str) -> dict:
    """Step 5: Paper trading validation."""
    result = {
        "step": "paper_run",
        "ts": datetime.now(timezone.utc).isoformat(),
        "status": "NEEDS_MANUAL",
        "message": (
            f"Deploy {strategy_id} in paper mode for >={GATE_CRITERIA['paper_run']['min_days']} days.\n"
            f"Monitor divergence vs backtest (max {GATE_CRITERIA['paper_run']['max_divergence_sigma']}sigma).\n"
            f"Use scripts/research/monitor_paper_divergence.py."
        ),
        "gates": GATE_CRITERIA["paper_run"],
    }
    return result


def run_promotion_step(strategy_id: str) -> dict:
    """Step 6: Generate promotion committee request."""
    try:
        from scripts.promotion_committee import create_promotion_request
        out = create_promotion_request(strategy_id, "live_probation")
        return {
            "step": "promotion",
            "ts": datetime.now(timezone.utc).isoformat(),
            "status": "GENERATED",
            "file": str(out),
            "message": f"Promotion request created at {out}. Fill in evidence and submit.",
        }
    except Exception as e:
        return {
            "step": "promotion",
            "ts": datetime.now(timezone.utc).isoformat(),
            "status": "ERROR",
            "error": str(e),
        }


STEP_RUNNERS = {
    "backtest": run_backtest_step,
    "walk_forward": run_walk_forward_step,
    "monte_carlo": run_monte_carlo_step,
    "portfolio_score": run_portfolio_score_step,
    "paper_run": run_paper_run_step,
    "promotion": run_promotion_step,
}


def run_step(strategy_id: str, step: str) -> dict:
    """Run a specific funnel step and update state."""
    state = _load_funnel_state(strategy_id)
    runner = STEP_RUNNERS.get(step)
    if runner is None:
        return {"error": f"Unknown step: {step}"}

    result = runner(strategy_id)
    state["steps"][step] = result
    state["current_step"] = step
    _save_funnel_state(strategy_id, state)
    return result


def run_all_steps(strategy_id: str) -> dict:
    """Run all funnel steps sequentially."""
    results = {}
    for step in FUNNEL_STEPS:
        results[step] = run_step(strategy_id, step)
    return results


def get_funnel_status() -> list[dict]:
    """Get status of all strategies in the funnel."""
    FUNNEL_DIR.mkdir(parents=True, exist_ok=True)
    states = []
    for f in sorted(FUNNEL_DIR.glob("*.json")):
        try:
            state = json.loads(f.read_text(encoding="utf-8"))
            completed = sum(
                1 for s in FUNNEL_STEPS
                if state.get("steps", {}).get(s, {}).get("status") in ("PASS", "GENERATED")
            )
            state["progress"] = f"{completed}/{len(FUNNEL_STEPS)}"
            states.append(state)
        except Exception:
            pass
    return states


def main():
    ap = argparse.ArgumentParser(description="Research funnel pipeline")
    ap.add_argument("--strat", help="Strategy ID")
    ap.add_argument("--step", default="all",
                    choices=["all"] + FUNNEL_STEPS,
                    help="Which step to run")
    ap.add_argument("--status", action="store_true", help="Show funnel status")
    args = ap.parse_args()

    if args.status:
        states = get_funnel_status()
        if not states:
            print("No strategies in the funnel.")
            return 0
        print(f"=== RESEARCH FUNNEL ({len(states)} strategies) ===\n")
        for s in states:
            sid = s.get("strategy_id", "?")
            step = s.get("current_step", "?")
            progress = s.get("progress", "?")
            print(f"  {sid}: step={step}, progress={progress}")
        return 0

    if not args.strat:
        ap.print_help()
        return 1

    if args.step == "all":
        print(f"Running full funnel for {args.strat}...\n")
        results = run_all_steps(args.strat)
        for step, result in results.items():
            status = result.get("status", "?")
            print(f"  [{status}] {step}: {result.get('message', '')[:80]}")
    else:
        print(f"Running {args.step} for {args.strat}...\n")
        result = run_step(args.strat, args.step)
        print(f"  [{result.get('status', '?')}] {result.get('message', '')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
