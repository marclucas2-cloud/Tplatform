# Proxy — load from intraday-backtesterV2/strategies/
import importlib.util
import os

_p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "archive", "intraday-backtesterV2", "strategies", "failed_rally_short.py")
_s = importlib.util.spec_from_file_location("_ibt_frs", _p)
_m = importlib.util.module_from_spec(_s)
_s.loader.exec_module(_m)
FailedRallyShortStrategy = _m.FailedRallyShortStrategy
