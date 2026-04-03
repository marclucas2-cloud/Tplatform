"""ReplayRecorder -- Record and replay market data and signals for determinism testing.

Supports recording candles and signals to JSONL files, loading them back
for replay, and comparing two recordings to find divergences.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List


class ReplayRecorder:
    """Record, save, load, and compare market replay sessions.

    Usage:
        recorder = ReplayRecorder()
        recorder.record_candle({"symbol": "BTCUSDT", "close": 40000.0, ...})
        recorder.record_signal({"action": "BUY", "symbol": "BTCUSDT", ...})
        recorder.save("data/replay_2024.jsonl")

        loaded = ReplayRecorder.load("data/replay_2024.jsonl")
        diffs = ReplayRecorder.compare_recordings(session_a, session_b)
    """

    def __init__(self) -> None:
        self._buffer: List[Dict[str, Any]] = []
        self._seq: int = 0

    def record_candle(self, candle: dict) -> None:
        """Append a candle record to the recording buffer.

        Args:
            candle: Dictionary with at minimum 'symbol', 'timestamp',
                    'open', 'high', 'low', 'close', 'volume' keys.
        """
        entry = {
            "seq": self._seq,
            "type": "candle",
            "data": dict(candle),
        }
        self._buffer.append(entry)
        self._seq += 1

    def record_signal(self, signal: dict) -> None:
        """Record a strategy output signal.

        Args:
            signal: Dictionary describing the signal (action, symbol,
                    side, strategy_name, strength, etc.).
        """
        entry = {
            "seq": self._seq,
            "type": "signal",
            "data": dict(signal),
        }
        self._buffer.append(entry)
        self._seq += 1

    def record_order(self, order: dict) -> None:
        """Record an order event.

        Args:
            order: Dictionary with order fields (symbol, side, quantity, etc.).
        """
        entry = {
            "seq": self._seq,
            "type": "order",
            "data": dict(order),
        }
        self._buffer.append(entry)
        self._seq += 1

    def record_fill(self, fill: dict) -> None:
        """Record a fill event.

        Args:
            fill: Dictionary with fill fields (price, quantity, commission, etc.).
        """
        entry = {
            "seq": self._seq,
            "type": "fill",
            "data": dict(fill),
        }
        self._buffer.append(entry)
        self._seq += 1

    def record_state(self, state: dict) -> None:
        """Record a state snapshot (equity, positions, cash, etc.).

        Args:
            state: Dictionary with portfolio state fields.
        """
        entry = {
            "seq": self._seq,
            "type": "state",
            "data": dict(state),
        }
        self._buffer.append(entry)
        self._seq += 1

    @property
    def entries(self) -> List[Dict[str, Any]]:
        """Return a copy of the current recording buffer."""
        return list(self._buffer)

    @property
    def candles(self) -> List[Dict[str, Any]]:
        """Return only candle entries."""
        return [e for e in self._buffer if e["type"] == "candle"]

    @property
    def signals(self) -> List[Dict[str, Any]]:
        """Return only signal entries."""
        return [e for e in self._buffer if e["type"] == "signal"]

    def __len__(self) -> int:
        return len(self._buffer)

    def clear(self) -> None:
        """Reset the recording buffer."""
        self._buffer.clear()
        self._seq = 0

    def save(self, path: str) -> None:
        """Save the recording to a JSONL file (one JSON object per line).

        Args:
            path: File path for the output JSONL file.

        Raises:
            ValueError: If the buffer is empty.
        """
        if not self._buffer:
            raise ValueError("Cannot save empty recording")

        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        with open(out_path, "w", encoding="utf-8") as f:
            for entry in self._buffer:
                line = json.dumps(entry, default=_json_serializer, sort_keys=True)
                f.write(line + "\n")

    @staticmethod
    def load(path: str) -> List[Dict[str, Any]]:
        """Load a recording from a JSONL file.

        Args:
            path: File path to the JSONL recording.

        Returns:
            List of recorded entries (dicts with seq, type, data).

        Raises:
            FileNotFoundError: If the file does not exist.
        """
        entries: List[Dict[str, Any]] = []
        with open(path, encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError as e:
                    raise ValueError(
                        f"Invalid JSON on line {line_num} of {path}: {e}"
                    ) from e
        return entries

    @staticmethod
    def compare_recordings(
        a: List[Dict[str, Any]],
        b: List[Dict[str, Any]],
        float_tolerance: float = 1e-10,
    ) -> Dict[str, Any]:
        """Compare two recordings and return a detailed diff report.

        Args:
            a: First recording (list of entries).
            b: Second recording (list of entries).
            float_tolerance: Maximum allowed difference for float comparisons.

        Returns:
            Dictionary with:
                - identical (bool): True if recordings match.
                - length_a, length_b (int): Lengths of each recording.
                - mismatches (list): Details of each divergence found.
                - first_divergence_seq (int or None): Sequence number of
                  the first mismatch, or None if identical.
        """
        result: Dict[str, Any] = {
            "identical": True,
            "length_a": len(a),
            "length_b": len(b),
            "mismatches": [],
            "first_divergence_seq": None,
        }

        if len(a) != len(b):
            result["identical"] = False
            result["mismatches"].append({
                "type": "length_mismatch",
                "length_a": len(a),
                "length_b": len(b),
            })

        min_len = min(len(a), len(b))
        for i in range(min_len):
            entry_a = a[i]
            entry_b = b[i]

            # Compare type
            if entry_a.get("type") != entry_b.get("type"):
                mismatch = {
                    "seq": i,
                    "field": "type",
                    "a": entry_a.get("type"),
                    "b": entry_b.get("type"),
                }
                result["mismatches"].append(mismatch)
                result["identical"] = False
                if result["first_divergence_seq"] is None:
                    result["first_divergence_seq"] = i
                continue

            # Deep compare data
            data_diffs = _compare_dicts(
                entry_a.get("data", {}),
                entry_b.get("data", {}),
                float_tolerance=float_tolerance,
                prefix=f"seq[{i}].data",
            )
            if data_diffs:
                for diff in data_diffs:
                    diff["seq"] = i
                result["mismatches"].extend(data_diffs)
                result["identical"] = False
                if result["first_divergence_seq"] is None:
                    result["first_divergence_seq"] = i

        # Report entries only in one recording
        if len(a) > min_len:
            for i in range(min_len, len(a)):
                result["mismatches"].append({
                    "seq": i,
                    "type": "extra_in_a",
                    "data": a[i],
                })
            if result["first_divergence_seq"] is None:
                result["first_divergence_seq"] = min_len

        if len(b) > min_len:
            for i in range(min_len, len(b)):
                result["mismatches"].append({
                    "seq": i,
                    "type": "extra_in_b",
                    "data": b[i],
                })
            if result["first_divergence_seq"] is None:
                result["first_divergence_seq"] = min_len

        return result


def _compare_dicts(
    a: dict,
    b: dict,
    float_tolerance: float = 1e-10,
    prefix: str = "",
) -> List[Dict[str, Any]]:
    """Recursively compare two dicts, returning a list of differences.

    Args:
        a: First dictionary.
        b: Second dictionary.
        float_tolerance: Tolerance for float comparisons.
        prefix: Key path prefix for reporting.

    Returns:
        List of difference dicts, each with 'field', 'a', 'b'.
    """
    diffs: List[Dict[str, Any]] = []
    all_keys = set(list(a.keys()) + list(b.keys()))

    for key in sorted(all_keys):
        field = f"{prefix}.{key}" if prefix else key
        val_a = a.get(key)
        val_b = b.get(key)

        if key not in a:
            diffs.append({"field": field, "a": "<missing>", "b": val_b})
            continue
        if key not in b:
            diffs.append({"field": field, "a": val_a, "b": "<missing>"})
            continue

        if isinstance(val_a, dict) and isinstance(val_b, dict):
            diffs.extend(_compare_dicts(val_a, val_b, float_tolerance, field))
        elif isinstance(val_a, (list, tuple)) and isinstance(val_b, (list, tuple)):
            if len(val_a) != len(val_b):
                diffs.append({
                    "field": field,
                    "a": f"list[{len(val_a)}]",
                    "b": f"list[{len(val_b)}]",
                })
            else:
                for idx, (item_a, item_b) in enumerate(zip(val_a, val_b)):
                    if isinstance(item_a, dict) and isinstance(item_b, dict):
                        diffs.extend(_compare_dicts(
                            item_a, item_b, float_tolerance, f"{field}[{idx}]"
                        ))
                    elif not _values_equal(item_a, item_b, float_tolerance):
                        diffs.append({
                            "field": f"{field}[{idx}]",
                            "a": item_a,
                            "b": item_b,
                        })
        elif not _values_equal(val_a, val_b, float_tolerance):
            diffs.append({"field": field, "a": val_a, "b": val_b})

    return diffs


def _values_equal(a: Any, b: Any, tolerance: float = 1e-10) -> bool:
    """Compare two values, using tolerance for floats.

    Args:
        a: First value.
        b: Second value.
        tolerance: Maximum allowed absolute difference for floats.

    Returns:
        True if values are considered equal.
    """
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) and math.isnan(b):
            return True
        if math.isinf(a) and math.isinf(b):
            return a == b
        return abs(a - b) <= tolerance
    return a == b


def _json_serializer(obj: Any) -> Any:
    """Custom JSON serializer for objects not natively serializable.

    Args:
        obj: Object to serialize.

    Returns:
        JSON-serializable representation.

    Raises:
        TypeError: If object type is not supported.
    """
    import datetime

    import numpy as np
    import pandas as pd

    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (pd.Timestamp, datetime.datetime)):
        return obj.isoformat()
    if isinstance(obj, datetime.date):
        return obj.isoformat()
    if isinstance(obj, pd.Timedelta):
        return str(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
