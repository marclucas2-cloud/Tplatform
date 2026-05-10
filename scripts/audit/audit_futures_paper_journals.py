"""Audit futures paper strategy journal freshness.

Used by checkups to ensure newly wired paper sleeves are actually exercised by
the scheduled futures_paper cycle. A promotion clock is not considered real if
the strategy journal has no observation in the last 24h.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAX_AGE_HOURS = 24.0
STRATEGIES = (
    "mes_mr_vix_spike",
    "mes_estx50_divergence",
)


def _parse_ts(raw: object) -> datetime | None:
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _last_event(strategy_id: str) -> tuple[datetime | None, dict | None]:
    path = ROOT / "data" / "state" / strategy_id / "journal.jsonl"
    if not path.exists():
        return None, None
    last_payload = None
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            payload = json.loads(line)
        except Exception:
            continue
        last_payload = payload
    if not last_payload:
        return None, None
    return _parse_ts(last_payload.get("ts_utc") or last_payload.get("logged_at_utc")), last_payload


def main() -> int:
    now = datetime.now(timezone.utc)
    failures: list[str] = []
    print("# futures paper journal freshness")
    for strategy_id in STRATEGIES:
        ts, payload = _last_event(strategy_id)
        if ts is None:
            failures.append(f"{strategy_id}: missing journal event")
            print(f"FAIL {strategy_id}: missing journal event")
            continue
        age_hours = (now - ts).total_seconds() / 3600.0
        status = "OK" if age_hours <= MAX_AGE_HOURS else "FAIL"
        print(
            f"{status} {strategy_id}: age_hours={age_hours:.2f} "
            f"event={payload.get('event')}"
        )
        if age_hours > MAX_AGE_HOURS:
            failures.append(f"{strategy_id}: last event {age_hours:.1f}h old")
    if failures:
        print("\nFailures:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
