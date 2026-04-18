"""MIB/ESTX50 Spread — Relative Value Mean Reversion (paper-only).

Edge: Italy (high beta, peripheral) vs Eurozone (core, diversified).
Spread mean-reverts except during existential EU crises.

WF corrige (scripts/wf_mib_estx50_corrected.py, 2026-04-18):
  12 trades, +EUR 22,639, avg Sharpe 3.91, WF 4/5 (vs original buggy
  +$57K Sharpe 14.35 — bugs fixes: MtM Sharpe, hedge ratio notional,
  slippage 4 legs, exit z-score uniquement).

Signal:
  Log ratio = log(MIB / ESTX50)
  Z-score = rolling 60-day normalize(log_ratio)
  LONG spread (long MIB, short ESTX50) si z < -2.0
  SHORT spread (short MIB, long ESTX50) si z > +2.0
  EXIT: z revient a 0 (TP) | |z| > 3.5 (SL) | 60j max hold

Sizing (notional dollar-neutral hedge):
  1 FIB (FTSE MIB futures, IDEM, EUR 5/pt)
  + 3 FESX (Euro Stoxx 50 futures, EUREX, EUR 10/pt)
  ratio recalcule a chaque entree

Mode: PAPER UNIQUEMENT via core/runtime/spread_paper_runner.py.
Pas de wiring StrategyBase/Signal/PaperBroker (multi-leg atomique
incompatible avec framework single-signal actuel).

Promotion vers paper IBKR puis live: a discuter apres 30j de paper data.
"""
from __future__ import annotations

# Re-export config et runner pour usage direct
from core.runtime.spread_paper_runner import (
    SpreadConfig,
    SpreadPaperRunner,
)

__all__ = ["SpreadConfig", "SpreadPaperRunner", "build_runner"]


def build_runner() -> SpreadPaperRunner:
    """Factory officielle MIB/ESTX50 spread paper runner."""
    return SpreadPaperRunner.for_mib_estx50()
