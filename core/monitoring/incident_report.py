"""Automatic incident report generation.

When a CRITICAL anomaly is detected, generates a structured report:
1. Temporal context: 30 minutes of events before the anomaly
2. System state: positions, regime, DD, Kelly mode
3. Key metrics: broker latency, cycle durations, queue depth
4. State transitions: which orders/positions changed state
5. Previous alerts: were there WARNs before the CRITICAL?

Reports are saved as Markdown in data/incidents/ and summarized on Telegram.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("monitoring.incident")

ROOT = Path(__file__).resolve().parent.parent.parent


class IncidentReportGenerator:
    """Generates structured incident reports from events and metrics."""

    def __init__(
        self,
        events_dir: Optional[str] = None,
        output_dir: Optional[str] = None,
    ):
        self._events_dir = Path(events_dir) if events_dir else ROOT / "data" / "events"
        self._output_dir = Path(output_dir) if output_dir else ROOT / "data" / "incidents"
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def generate(
        self,
        anomaly_message: str,
        anomaly_level: str = "CRITICAL",
        context_minutes: int = 30,
        metrics_snapshot: Optional[dict] = None,
        worker_state_snapshot: Optional[dict] = None,
    ) -> str:
        """Generate an incident report. Returns the file path."""
        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%d_%H%M%S")
        filename = f"incident_{timestamp}.md"
        filepath = self._output_dir / filename

        # Load recent events
        recent_events = self._load_recent_events(context_minutes)

        # Build report
        lines = [
            f"# Incident Report — {now.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            f"**Level**: {anomaly_level}",
            f"**Trigger**: {anomaly_message}",
            f"**Context window**: {context_minutes} minutes before trigger",
            "",
            "---",
            "",
            "## Timeline",
            "",
        ]

        if recent_events:
            for e in recent_events[-50:]:  # Cap at 50 events
                ts = e.get("ts", "?")[:19]
                cycle = e.get("cycle", "?")
                etype = e.get("type", "?")
                data = e.get("data", {})

                if etype == "ERROR":
                    lines.append(f"- `{ts}` **{cycle}** ERROR: {data.get('error', '?')}")
                elif etype == "SIGNAL":
                    sym = data.get("symbol", "?")
                    side = data.get("side", "?")
                    lines.append(f"- `{ts}` **{cycle}** SIGNAL: {side} {sym}")
                elif etype == "ORDER":
                    state = data.get("state", "?")
                    lines.append(f"- `{ts}` **{cycle}** ORDER: {state}")
                elif etype == "CYCLE_START":
                    lines.append(f"- `{ts}` **{cycle}** cycle start")
                elif etype == "CYCLE_END":
                    dur = data.get("duration_ms", "?")
                    ok = data.get("success", "?")
                    lines.append(f"- `{ts}` **{cycle}** cycle end ({dur}ms, {'OK' if ok else 'FAIL'})")
                else:
                    lines.append(f"- `{ts}` **{cycle}** {etype}")
        else:
            lines.append("_No events found in context window_")

        lines.extend(["", "---", "", "## System State", ""])

        if worker_state_snapshot:
            lines.append(f"**Positions**: {len(worker_state_snapshot.get('positions', {}))}")
            lines.append(f"**Regimes**: {json.dumps(worker_state_snapshot.get('regimes', {}))}")
            kills = worker_state_snapshot.get("kills", {})
            if kills:
                lines.append(f"**Active Kill Switches**: {json.dumps(kills)}")
            else:
                lines.append("**Kill Switches**: None active")
        else:
            lines.append("_Worker state snapshot not available_")

        lines.extend(["", "---", "", "## Metrics Snapshot", ""])

        if metrics_snapshot:
            for k, v in metrics_snapshot.items():
                lines.append(f"- **{k}**: {v}")
        else:
            lines.append("_Metrics snapshot not available_")

        # Count errors in window
        error_count = sum(
            1 for e in recent_events if e.get("type") == "ERROR"
        )
        signal_count = sum(
            1 for e in recent_events if e.get("type") == "SIGNAL"
        )

        lines.extend([
            "", "---", "",
            "## Summary",
            "",
            f"- Events in window: {len(recent_events)}",
            f"- Errors: {error_count}",
            f"- Signals: {signal_count}",
            "",
            "---",
            f"_Generated automatically at {now.isoformat()}_",
        ])

        report = "\n".join(lines)
        filepath.write_text(report, encoding="utf-8")
        logger.info(f"Incident report generated: {filepath}")

        return str(filepath)

    def get_summary(
        self,
        anomaly_message: str,
        worker_state_snapshot: Optional[dict] = None,
    ) -> str:
        """Generate a 5-line Telegram summary."""
        now = datetime.now().strftime("%H:%M")
        lines = [
            f"INCIDENT {now}: {anomaly_message}",
        ]
        if worker_state_snapshot:
            kills = worker_state_snapshot.get("kills", {})
            if kills:
                lines.append(f"Kill switches: {', '.join(kills.keys())}")
            pos_count = len(worker_state_snapshot.get("positions", {}))
            lines.append(f"Positions: {pos_count}")
        lines.append("Full report in data/incidents/")
        return "\n".join(lines[:5])

    def _load_recent_events(self, minutes: int) -> list[dict]:
        """Load events from the last N minutes."""
        events = []
        cutoff = (datetime.now() - timedelta(minutes=minutes)).isoformat()

        # Check today's and yesterday's files
        from datetime import date
        for day_offset in [0, 1]:
            d = date.today() - timedelta(days=day_offset)
            filepath = self._events_dir / f"events_{d.isoformat()}.jsonl"
            if filepath.exists():
                try:
                    with open(filepath) as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                event = json.loads(line)
                                if event.get("ts", "") >= cutoff:
                                    events.append(event)
                            except json.JSONDecodeError:
                                continue
                except Exception as e:
                    logger.error(f"Error reading events: {e}")

        return sorted(events, key=lambda e: e.get("ts", ""))
