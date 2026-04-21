"""Tests mes_calendar_paper.py - regression weekday detection.

Bug observe 2026-04-20 14:00 UTC: mes_monday_long_oc (paper) log
"pas un jour pattern" un lundi car bar.timestamp = close vendredi
precedent (dernier bar disponible avant close US lundi).
Fix: runtime_today override pour paper/live runner.
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from strategies_v2.futures.mes_calendar_paper import (
    MESMondayLong,
    MESWednesdayLong,
    MESPreHolidayLong,
)


def _mk_bar(ts: str, close: float = 5000.0):
    return SimpleNamespace(
        timestamp=pd.Timestamp(ts),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1000,
    )


def _mk_portfolio():
    return SimpleNamespace()


class TestMESMondayLong:

    def test_backtest_semantic_bar_on_monday(self):
        """Backtest: bar.timestamp est lundi -> signal."""
        strat = MESMondayLong()
        strat.set_data_feed(SimpleNamespace())
        bar = _mk_bar("2026-04-20")  # lundi
        sig = strat.on_bar(bar, _mk_portfolio())
        assert sig is not None
        assert sig.side == "BUY"

    def test_backtest_bar_on_friday_no_signal(self):
        """Backtest: bar.timestamp est vendredi -> pas de signal."""
        strat = MESMondayLong()
        strat.set_data_feed(SimpleNamespace())
        bar = _mk_bar("2026-04-17")  # vendredi
        sig = strat.on_bar(bar, _mk_portfolio())
        assert sig is None

    def test_paper_runner_friday_bar_monday_today_signals(self):
        """Regression bug 2026-04-20: cycle tourne lundi 14:00 UTC, feed a
        le bar de vendredi close -> avant fix: pas un jour pattern. Apres
        fix: runtime_today override -> signal car today=lundi."""
        strat = MESMondayLong()
        strat.set_data_feed(SimpleNamespace())
        strat.set_runtime_today(pd.Timestamp("2026-04-20"))  # lundi
        bar = _mk_bar("2026-04-17")  # bar = close vendredi
        sig = strat.on_bar(bar, _mk_portfolio())
        assert sig is not None
        assert sig.side == "BUY"

    def test_paper_runner_today_tuesday_no_signal(self):
        """Runtime override mardi -> pas de signal meme si bar lundi."""
        strat = MESMondayLong()
        strat.set_data_feed(SimpleNamespace())
        strat.set_runtime_today(pd.Timestamp("2026-04-21"))  # mardi
        bar = _mk_bar("2026-04-20")  # bar = lundi
        sig = strat.on_bar(bar, _mk_portfolio())
        assert sig is None

    def test_idempotence_one_signal_per_day(self):
        """Deux on_bar la meme journee -> 1 seul signal."""
        strat = MESMondayLong()
        strat.set_data_feed(SimpleNamespace())
        strat.set_runtime_today(pd.Timestamp("2026-04-20"))
        bar = _mk_bar("2026-04-17")
        sig1 = strat.on_bar(bar, _mk_portfolio())
        sig2 = strat.on_bar(bar, _mk_portfolio())
        assert sig1 is not None
        assert sig2 is None


class TestMESWednesdayLong:

    def test_paper_runner_wednesday_today_signals(self):
        strat = MESWednesdayLong()
        strat.set_data_feed(SimpleNamespace())
        strat.set_runtime_today(pd.Timestamp("2026-04-22"))  # mercredi
        bar = _mk_bar("2026-04-21")  # bar = close mardi
        sig = strat.on_bar(bar, _mk_portfolio())
        assert sig is not None

    def test_paper_runner_wednesday_bar_monday_no_signal(self):
        strat = MESWednesdayLong()
        strat.set_data_feed(SimpleNamespace())
        strat.set_runtime_today(pd.Timestamp("2026-04-20"))  # lundi
        bar = _mk_bar("2026-04-17")
        sig = strat.on_bar(bar, _mk_portfolio())
        assert sig is None


class TestMESPreHolidayLong:

    def test_paper_runner_before_holiday(self):
        """Ex: 2026-04-02 jeudi, holiday 2026-04-03 vendredi (Good Friday
        pas en _US_HOLIDAYS_2026) mais on teste avec 2026-05-22 vendredi
        avant Memorial Day lundi 2026-05-25."""
        strat = MESPreHolidayLong()
        strat.set_data_feed(SimpleNamespace())
        strat.set_runtime_today(pd.Timestamp("2026-05-22"))  # ven avant mémorial
        bar = _mk_bar("2026-05-21")
        sig = strat.on_bar(bar, _mk_portfolio())
        assert sig is not None

    def test_paper_runner_not_before_holiday(self):
        strat = MESPreHolidayLong()
        strat.set_data_feed(SimpleNamespace())
        strat.set_runtime_today(pd.Timestamp("2026-04-20"))  # lundi normal
        bar = _mk_bar("2026-04-17")
        sig = strat.on_bar(bar, _mk_portfolio())
        assert sig is None
