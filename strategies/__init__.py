# Trading strategies
# Re-export intraday strategies so paper_portfolio.py can import them
# Actual implementations live in intraday-backtesterV2/strategies/
import importlib.util
import os
import sys

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ibt_root = os.path.join(_root, "archive", "intraday-backtesterV2")
_ibt_dir = os.path.join(_ibt_root, "strategies")

# Ensure intraday-backtesterV2 is in sys.path for backtest_engine imports
if os.path.isdir(_ibt_root) and _ibt_root not in sys.path:
    sys.path.insert(1, _ibt_root)

def _import_class(module_file, class_name):
    path = os.path.join(_ibt_dir, module_file)
    if not os.path.isfile(path):
        return None
    spec = importlib.util.spec_from_file_location(
        f"_ibt_{module_file.replace('.py', '')}", path
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, class_name, None)

if os.path.isdir(_ibt_dir):
    DayOfWeekSeasonalStrategy = _import_class("day_of_week_seasonal.py", "DayOfWeekSeasonalStrategy")
    LateDayMeanReversionStrategy = _import_class("late_day_mean_reversion.py", "LateDayMeanReversionStrategy")
