"""
Backfill-to-live resync guard.

Detects gaps, duplicates, and drift when transitioning from historical
backfill data to a live feed.  Multi-broker: Binance WS (crypto),
IBKR historical bars (FX/equities), Alpaca API.

Typical failure modes at the backfill/live boundary:
  - Off-by-one: last backfill candle == first live candle (duplicate)
  - Missing candle: gap between backfill end and live start
  - Clock drift: server_time vs local_time diverges over time
  - Price jump: open of live candle far from close of last backfill

Log output: data/resync_log.jsonl (append-only, one JSON object per line).
"""
from __future__ import annotations

import json
import logging
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Frequency configuration
# ---------------------------------------------------------------------------

FREQ_SECONDS: Dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

# Tolerance multiplier: a gap up to N * expected is still acceptable.
DEFAULT_GAP_TOLERANCE = 2.0

# Price discontinuity threshold (fraction of last close).
DEFAULT_PRICE_DISC_THRESHOLD = 0.05  # 5 %

# Drift sliding-window size.
DRIFT_WINDOW_SIZE = 10


# ---------------------------------------------------------------------------
# ResyncGuard
# ---------------------------------------------------------------------------

class ResyncGuard:
    """Guard that validates the backfill-to-live transition and ongoing feed
    integrity for a single data stream (one symbol / one timeframe)."""

    def __init__(
        self,
        max_gap_seconds: Optional[Dict[str, int]] = None,
        max_drift_ms: float = 500.0,
        gap_tolerance: float = DEFAULT_GAP_TOLERANCE,
        price_disc_threshold: float = DEFAULT_PRICE_DISC_THRESHOLD,
        log_dir: str = "data",
    ):
        """
        Args:
            max_gap_seconds: Override per-frequency max gap. Keys are freq
                strings ("5m", "1h" ...), values are seconds.  Defaults come
                from ``FREQ_SECONDS * gap_tolerance``.
            max_drift_ms: Maximum acceptable server-local clock delta (ms).
            gap_tolerance: Multiplier applied to expected candle interval to
                compute the maximum tolerated gap (default 2x).
            price_disc_threshold: Fractional price jump that triggers a
                discontinuity warning (default 5 %).
            log_dir: Directory for ``resync_log.jsonl``.
        """
        self._freq_seconds = dict(FREQ_SECONDS)
        if max_gap_seconds:
            self._freq_seconds.update(max_gap_seconds)

        self.max_drift_ms = max_drift_ms
        self.gap_tolerance = gap_tolerance
        self.price_disc_threshold = price_disc_threshold

        # Drift tracking: sliding window of (server_ts, local_ts, delta_ms)
        self._drift_window: deque = deque(maxlen=DRIFT_WINDOW_SIZE)

        # JSONL log path
        self._log_path = Path(log_dir) / "resync_log.jsonl"
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 1. Backfill-to-live transition check
    # ------------------------------------------------------------------

    def check_backfill_to_live(
        self,
        backfill_df: pd.DataFrame,
        live_candle: Dict[str, Any],
        freq: str,
    ) -> Dict[str, Any]:
        """Compare the tail of a backfill DataFrame with an incoming live candle.

        Args:
            backfill_df: OHLCV DataFrame with DatetimeIndex (UTC).
            live_candle: dict with at least ``timestamp`` (datetime or str)
                and ``open`` (float).
            freq: Candle frequency string ("5m", "15m", "1h", "1d").

        Returns:
            {ok, gap_seconds, has_duplicate, has_gap, has_price_disc, detail}
        """
        result: Dict[str, Any] = {
            "ok": True,
            "gap_seconds": 0.0,
            "has_duplicate": False,
            "has_gap": False,
            "has_price_disc": False,
            "detail": "clean transition",
        }

        if backfill_df.empty:
            result["ok"] = False
            result["detail"] = "backfill DataFrame is empty"
            self._log("backfill_to_live", result)
            return result

        expected_seconds = self._expected_gap(freq)
        max_allowed = expected_seconds * self.gap_tolerance

        # Parse timestamps
        last_backfill_ts = backfill_df.index[-1]
        live_ts = self._parse_ts(live_candle.get("timestamp"))

        if live_ts is None:
            result["ok"] = False
            result["detail"] = "live_candle has no valid timestamp"
            self._log("backfill_to_live", result)
            return result

        # Ensure both are tz-aware for comparison
        last_backfill_ts = self._ensure_utc(last_backfill_ts)
        live_ts = self._ensure_utc(live_ts)

        gap = (live_ts - last_backfill_ts).total_seconds()
        result["gap_seconds"] = gap

        issues: List[str] = []

        # (a) Duplicate detection
        if gap == 0:
            result["has_duplicate"] = True
            result["ok"] = False
            issues.append(f"duplicate: live timestamp == last backfill ({live_ts})")

        # (b) Gap detection
        if gap > max_allowed:
            result["has_gap"] = True
            result["ok"] = False
            issues.append(
                f"gap {gap:.0f}s exceeds {max_allowed:.0f}s "
                f"(expected {expected_seconds}s for {freq})"
            )
        elif gap < 0:
            result["has_gap"] = True
            result["ok"] = False
            issues.append(f"negative gap {gap:.0f}s: live is BEFORE backfill end")

        # (c) Price discontinuity
        last_close = float(backfill_df["close"].iloc[-1])
        live_open = live_candle.get("open")
        if live_open is not None and last_close != 0:
            disc = abs(float(live_open) - last_close) / abs(last_close)
            if disc > self.price_disc_threshold:
                result["has_price_disc"] = True
                result["ok"] = False
                issues.append(
                    f"price discontinuity {disc:.2%}: "
                    f"backfill close={last_close}, live open={live_open}"
                )

        if issues:
            result["detail"] = "; ".join(issues)

        self._log("backfill_to_live", result)
        return result

    # ------------------------------------------------------------------
    # 2. Feed latency / drift check
    # ------------------------------------------------------------------

    def check_feed_latency(
        self,
        server_timestamp: datetime,
        local_timestamp: datetime,
    ) -> Dict[str, Any]:
        """Measure and track server-to-local clock delta.

        Args:
            server_timestamp: Timestamp reported by the exchange / broker.
            local_timestamp: ``datetime.now(timezone.utc)`` on the worker.

        Returns:
            {latency_ms, ok, drifting}
        """
        server_ts = self._ensure_utc(server_timestamp)
        local_ts = self._ensure_utc(local_timestamp)

        delta_ms = (local_ts - server_ts).total_seconds() * 1000.0

        self._drift_window.append(delta_ms)

        drifting = self._is_drifting()

        ok = abs(delta_ms) <= self.max_drift_ms and not drifting

        result = {
            "latency_ms": round(delta_ms, 2),
            "ok": ok,
            "drifting": drifting,
        }

        if not ok:
            self._log("feed_latency", result)

        return result

    def reset_drift(self) -> None:
        """Clear the drift sliding window (call on reconnect)."""
        self._drift_window.clear()
        logger.info("ResyncGuard: drift window reset")

    # ------------------------------------------------------------------
    # 3. Sequence integrity check
    # ------------------------------------------------------------------

    def check_sequence_integrity(
        self,
        timestamps: List[datetime],
        freq: str,
    ) -> Dict[str, Any]:
        """Validate that a list of timestamps is monotonically increasing
        with the expected frequency.

        Args:
            timestamps: Ordered list of candle open timestamps.
            freq: Expected frequency ("5m", "1h", etc.).

        Returns:
            {ok, gaps, duplicates, out_of_order}
        """
        result: Dict[str, Any] = {
            "ok": True,
            "gaps": [],
            "duplicates": [],
            "out_of_order": [],
        }

        if len(timestamps) < 2:
            return result

        expected_seconds = self._expected_gap(freq)
        max_allowed = expected_seconds * self.gap_tolerance

        for i in range(1, len(timestamps)):
            prev = self._ensure_utc(self._parse_ts(timestamps[i - 1]))
            curr = self._ensure_utc(self._parse_ts(timestamps[i]))
            delta = (curr - prev).total_seconds()

            if delta == 0:
                result["duplicates"].append({
                    "index": i,
                    "timestamp": curr.isoformat(),
                })
            elif delta < 0:
                result["out_of_order"].append({
                    "index": i,
                    "timestamp": curr.isoformat(),
                    "delta_s": delta,
                })
            elif delta > max_allowed:
                result["gaps"].append({
                    "index": i,
                    "from": prev.isoformat(),
                    "to": curr.isoformat(),
                    "delta_s": delta,
                    "expected_s": expected_seconds,
                })

        if result["gaps"] or result["duplicates"] or result["out_of_order"]:
            result["ok"] = False

        self._log("sequence_integrity", result)
        return result

    # ------------------------------------------------------------------
    # 4. Resync recommendation
    # ------------------------------------------------------------------

    @staticmethod
    def get_resync_recommendation(check_results: Dict[str, Any]) -> str:
        """Translate check results into an actionable recommendation.

        Returns one of: "CONTINUE", "RELOAD_BACKFILL", "WAIT", "ALERT".
        """
        if check_results.get("ok", True):
            return "CONTINUE"

        # Duplicate only -> safe to skip the duplicate and continue
        if check_results.get("has_duplicate") and not check_results.get("has_gap"):
            return "CONTINUE"

        # Drifting feed -> wait for clock to stabilise
        if check_results.get("drifting"):
            return "WAIT"

        # Gap detected -> reload backfill to fill the hole
        if check_results.get("has_gap"):
            return "RELOAD_BACKFILL"

        # Price discontinuity alone -> alert but keep going
        if check_results.get("has_price_disc"):
            return "ALERT"

        # Sequence problems (out_of_order, many gaps, etc.)
        if check_results.get("out_of_order"):
            return "RELOAD_BACKFILL"
        if check_results.get("gaps"):
            return "RELOAD_BACKFILL"

        # Catch-all for anything unexpected
        return "ALERT"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _expected_gap(self, freq: str) -> float:
        """Return expected gap in seconds for *freq*, with fallback."""
        if freq in self._freq_seconds:
            return float(self._freq_seconds[freq])
        # Try to parse pandas-style offset
        try:
            offset = pd.tseries.frequencies.to_offset(freq)
            if offset is not None:
                return offset.delta.total_seconds()
        except Exception:
            pass
        logger.warning("ResyncGuard: unknown freq '%s', defaulting to 900s", freq)
        return 900.0

    def _is_drifting(self) -> bool:
        """Return True if the drift is monotonically increasing over the
        full sliding window (buffer saturation signal)."""
        if len(self._drift_window) < DRIFT_WINDOW_SIZE:
            return False
        values = list(self._drift_window)
        # Check monotonically increasing absolute drift
        abs_values = [abs(v) for v in values]
        return all(abs_values[i] < abs_values[i + 1] for i in range(len(abs_values) - 1))

    @staticmethod
    def _parse_ts(ts: Any) -> Optional[datetime]:
        """Coerce *ts* to a datetime, returning None on failure."""
        if ts is None:
            return None
        if isinstance(ts, datetime):
            return ts
        if isinstance(ts, pd.Timestamp):
            return ts.to_pydatetime()
        if isinstance(ts, str):
            try:
                return pd.Timestamp(ts).to_pydatetime()
            except Exception:
                return None
        return None

    @staticmethod
    def _ensure_utc(ts: datetime) -> datetime:
        """Attach UTC if tz-naive, else convert to UTC."""
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)

    def _log(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Append one JSON line to the resync log."""
        try:
            record = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": event_type,
                **payload,
            }
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception as exc:
            logger.error("ResyncGuard: failed to write log: %s", exc)
