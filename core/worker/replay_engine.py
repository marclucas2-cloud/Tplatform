"""Replay engine for post-mortem debugging.

Replays a sequence of events recorded by EventLogger.
Allows step-by-step investigation of what happened during an incident.

Usage:
  python -m core.worker.replay_engine \\
      --events data/events/events_2026-04-03.jsonl \\
      --cycle crypto \\
      --from "2026-04-03T03:15:00" \\
      --to "2026-04-03T03:30:00" \\
      --step
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("worker.replay")


class ReplayEngine:
    """Replays recorded events for debugging."""

    def __init__(self, events_file: str):
        self.events = self._load_events(events_file)
        self._divergences: list[dict] = []

    def replay(
        self,
        cycle_name: Optional[str] = None,
        from_ts: Optional[str] = None,
        to_ts: Optional[str] = None,
        step_mode: bool = False,
        event_types: Optional[list[str]] = None,
    ) -> list[dict]:
        """Replay events for a specific cycle in a time window.

        Returns list of events with optional divergence info.
        """
        filtered = self._filter_events(
            cycle_name, from_ts, to_ts, event_types
        )

        results = []
        for event in filtered:
            result = {
                "timestamp": event["ts"],
                "cycle": event["cycle"],
                "type": event["type"],
                "data": event.get("data", {}),
            }

            if event["type"] == "CYCLE_START" and "snapshot" in event:
                result["snapshot"] = event["snapshot"]
                # Find corresponding CYCLE_END
                end_event = self._find_cycle_end(
                    event["ts"], event["cycle"]
                )
                if end_event:
                    result["cycle_output"] = end_event.get("data", {})
                    result["duration_ms"] = end_event.get("data", {}).get(
                        "duration_ms"
                    )
                    result["success"] = end_event.get("data", {}).get("success")

            results.append(result)

            if step_mode:
                self._print_event(result)
                try:
                    input("Press Enter to continue...")
                except EOFError:
                    break

        return results

    def get_timeline(
        self,
        cycle_name: Optional[str] = None,
        from_ts: Optional[str] = None,
        to_ts: Optional[str] = None,
    ) -> list[dict]:
        """Get a summary timeline of events."""
        filtered = self._filter_events(cycle_name, from_ts, to_ts)
        timeline = []
        for e in filtered:
            entry = {
                "ts": e["ts"],
                "cycle": e["cycle"],
                "type": e["type"],
            }
            if e["type"] == "SIGNAL":
                entry["signal"] = e.get("data", {}).get("symbol", "?")
            elif e["type"] == "ORDER":
                entry["order"] = e.get("data", {}).get("state", "?")
            elif e["type"] == "ERROR":
                entry["error"] = e.get("data", {}).get("error", "?")[:100]
            elif e["type"] == "CYCLE_END":
                entry["duration_ms"] = e.get("data", {}).get("duration_ms")
                entry["success"] = e.get("data", {}).get("success")
            timeline.append(entry)
        return timeline

    def get_signals(
        self,
        cycle_name: Optional[str] = None,
        from_ts: Optional[str] = None,
        to_ts: Optional[str] = None,
    ) -> list[dict]:
        """Extract only SIGNAL events."""
        return [
            e for e in self._filter_events(
                cycle_name, from_ts, to_ts, ["SIGNAL"]
            )
        ]

    def get_errors(
        self,
        from_ts: Optional[str] = None,
        to_ts: Optional[str] = None,
    ) -> list[dict]:
        """Extract only ERROR events."""
        return [
            e for e in self._filter_events(
                None, from_ts, to_ts, ["ERROR"]
            )
        ]

    def _filter_events(
        self,
        cycle_name: Optional[str] = None,
        from_ts: Optional[str] = None,
        to_ts: Optional[str] = None,
        event_types: Optional[list[str]] = None,
    ) -> list[dict]:
        filtered = self.events
        if cycle_name:
            filtered = [e for e in filtered if e["cycle"] == cycle_name]
        if from_ts:
            filtered = [e for e in filtered if e["ts"] >= from_ts]
        if to_ts:
            filtered = [e for e in filtered if e["ts"] <= to_ts]
        if event_types:
            filtered = [e for e in filtered if e["type"] in event_types]
        return filtered

    def _find_cycle_end(self, start_ts: str, cycle_name: str) -> Optional[dict]:
        """Find the CYCLE_END event matching a CYCLE_START."""
        for e in self.events:
            if (
                e["cycle"] == cycle_name
                and e["type"] == "CYCLE_END"
                and e["ts"] > start_ts
            ):
                return e
        return None

    def _load_events(self, path: str) -> list[dict]:
        events = []
        filepath = Path(path)
        if not filepath.exists():
            logger.warning(f"Events file not found: {path}")
            return events
        with open(filepath) as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError as e:
                    logger.warning(f"Invalid JSON at line {line_num}: {e}")
        logger.info(f"Loaded {len(events)} events from {path}")
        return events

    def _print_event(self, result: dict) -> None:
        """Pretty-print an event for step mode."""
        print(f"\n{'='*60}")
        print(f"  [{result['timestamp']}] {result['cycle']} / {result['type']}")
        if "snapshot" in result:
            print(f"  Snapshot: {json.dumps(result['snapshot'], indent=2)[:500]}")
        if "cycle_output" in result:
            print(f"  Output: {json.dumps(result['cycle_output'], indent=2)[:500]}")
        if result.get("data"):
            print(f"  Data: {json.dumps(result['data'], indent=2)[:500]}")
        print(f"{'='*60}")


def main():
    """CLI entry point for replay engine."""
    import argparse

    parser = argparse.ArgumentParser(description="Replay worker events")
    parser.add_argument("--events", required=True, help="Path to events JSONL file")
    parser.add_argument("--cycle", help="Filter by cycle name")
    parser.add_argument("--from", dest="from_ts", help="Start timestamp (ISO)")
    parser.add_argument("--to", dest="to_ts", help="End timestamp (ISO)")
    parser.add_argument("--step", action="store_true", help="Step-by-step mode")
    parser.add_argument("--timeline", action="store_true", help="Show timeline only")
    parser.add_argument("--errors", action="store_true", help="Show errors only")

    args = parser.parse_args()
    engine = ReplayEngine(args.events)

    if args.timeline:
        timeline = engine.get_timeline(args.cycle, args.from_ts, args.to_ts)
        for entry in timeline:
            print(json.dumps(entry))
    elif args.errors:
        errors = engine.get_errors(args.from_ts, args.to_ts)
        for e in errors:
            print(f"[{e['ts']}] {e['cycle']}: {e.get('data', {}).get('error', '?')}")
    else:
        results = engine.replay(
            args.cycle, args.from_ts, args.to_ts, step_mode=args.step,
        )
        if not args.step:
            for r in results:
                print(json.dumps(r, indent=2))

    print(f"\nTotal events: {len(engine.events)}")


if __name__ == "__main__":
    main()
