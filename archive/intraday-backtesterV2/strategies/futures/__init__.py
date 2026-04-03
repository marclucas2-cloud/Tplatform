"""Futures proxy strategies package."""
from .es_trend_1h import ESTrend1HStrategy

FUTURES_STRATEGIES = [
    ESTrend1HStrategy,
]
