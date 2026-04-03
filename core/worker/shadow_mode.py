"""Shadow mode for canary deploys.

A shadow worker executes the same cycles but logs signals
instead of submitting orders. Signals are compared with
the live worker to detect regressions before deploy.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("worker.shadow")

ROOT = Path(__file__).resolve().parent.parent.parent


class ShadowSignalLogger:
    """Logs signals in shadow mode instead of executing them."""

    def __init__(self, output_dir: Optional[str] = None):
        self._output_dir = (
            Path(output_dir) if output_dir
            else ROOT / "data" / "shadow"
        )
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._signal_count = 0

    def log_signal(
        self,
        cycle_name: str,
        signal: dict,
    ) -> None:
        """Log a trading signal (instead of executing it)."""
        entry = {
            "ts": datetime.now().isoformat(timespec="microseconds"),
            "cycle": cycle_name,
            "signal": signal,
        }
        filepath = self._output_dir / "shadow_signals.jsonl"
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
        self._signal_count += 1

    @property
    def signal_count(self) -> int:
        return self._signal_count


class ShadowComparator:
    """Compares live signals with shadow signals to detect divergences."""

    def __init__(
        self,
        live_signals_path: Optional[str] = None,
        shadow_signals_path: Optional[str] = None,
    ):
        self._live_path = (
            Path(live_signals_path) if live_signals_path
            else ROOT / "data" / "events"
        )
        self._shadow_path = (
            Path(shadow_signals_path) if shadow_signals_path
            else ROOT / "data" / "shadow" / "shadow_signals.jsonl"
        )

    def compare(self, hours: int = 1) -> list[dict]:
        """Compare live vs shadow signals for the last N hours.

        Returns list of divergences.
        """
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()

        live_signals = self._load_signals(self._live_path, cutoff, is_live=True)
        shadow_signals = self._load_shadow_signals(cutoff)

        divergences = []

        # Match by (cycle, approximate timestamp)
        for shadow in shadow_signals:
            matched = False
            for live in live_signals:
                if (
                    live["cycle"] == shadow["cycle"]
                    and abs(self._ts_diff(live["ts"], shadow["ts"])) < 120
                ):
                    # Compare signal content
                    if live.get("signal") != shadow.get("signal"):
                        divergences.append({
                            "type": "SIGNAL_MISMATCH",
                            "cycle": shadow["cycle"],
                            "live_ts": live["ts"],
                            "shadow_ts": shadow["ts"],
                            "live_signal": live.get("signal"),
                            "shadow_signal": shadow.get("signal"),
                        })
                    matched = True
                    break
            if not matched:
                divergences.append({
                    "type": "SHADOW_ONLY",
                    "cycle": shadow["cycle"],
                    "shadow_ts": shadow["ts"],
                    "shadow_signal": shadow.get("signal"),
                })

        # Check for live signals not in shadow
        for live in live_signals:
            matched = any(
                s["cycle"] == live["cycle"]
                and abs(self._ts_diff(s["ts"], live["ts"])) < 120
                for s in shadow_signals
            )
            if not matched:
                divergences.append({
                    "type": "LIVE_ONLY",
                    "cycle": live["cycle"],
                    "live_ts": live["ts"],
                    "live_signal": live.get("signal"),
                })

        return divergences

    def _load_signals(self, path: Path, cutoff: str, is_live: bool) -> list[dict]:
        """Load SIGNAL events from event logger files."""
        signals = []
        if not path.exists():
            return signals

        if path.is_dir():
            for fp in sorted(path.glob("events_*.jsonl")):
                try:
                    with open(fp) as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            event = json.loads(line)
                            if (
                                event.get("type") == "SIGNAL"
                                and event.get("ts", "") >= cutoff
                            ):
                                signals.append({
                                    "ts": event["ts"],
                                    "cycle": event["cycle"],
                                    "signal": event.get("data", {}),
                                })
                except Exception:
                    continue
        return signals

    def _load_shadow_signals(self, cutoff: str) -> list[dict]:
        """Load shadow signals from JSONL."""
        signals = []
        if not self._shadow_path.exists():
            return signals
        try:
            with open(self._shadow_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    if entry.get("ts", "") >= cutoff:
                        signals.append(entry)
        except Exception:
            pass
        return signals

    @staticmethod
    def _ts_diff(ts1: str, ts2: str) -> float:
        """Difference in seconds between two ISO timestamps."""
        try:
            dt1 = datetime.fromisoformat(ts1)
            dt2 = datetime.fromisoformat(ts2)
            return abs((dt1 - dt2).total_seconds())
        except (ValueError, TypeError):
            return float("inf")
