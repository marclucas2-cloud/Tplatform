"""API response snapshot storage for contract testing.

Saves a snapshot of each broker API response hourly.
Used to:
1. Compare with last OK snapshot when a contract fails
2. Build a library of real responses for tests
3. Detect progressive changes

Retention: 7 days (auto-purge).
"""

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("broker.snapshots")

ROOT = Path(__file__).resolve().parent.parent.parent.parent


class ResponseSnapshotStore:
    """Stores and manages API response snapshots."""

    def __init__(self, base_dir: Optional[str] = None):
        self._base_dir = (
            Path(base_dir) if base_dir
            else ROOT / "data" / "contracts" / "snapshots"
        )
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        broker: str,
        endpoint: str,
        response: object,
    ) -> str:
        """Save a snapshot. Returns the file path."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{broker}_{endpoint}_{timestamp}.json"
        filepath = self._base_dir / filename

        try:
            data = {
                "broker": broker,
                "endpoint": endpoint,
                "timestamp": datetime.now().isoformat(),
                "response": response,
            }
            filepath.write_text(
                json.dumps(data, default=str, indent=2),
                encoding="utf-8",
            )
            logger.debug(f"Snapshot saved: {filename}")
            return str(filepath)
        except Exception as e:
            logger.error(f"Snapshot save error: {e}")
            return ""

    def get_latest(
        self, broker: str, endpoint: str
    ) -> Optional[dict]:
        """Get the most recent snapshot for a broker/endpoint."""
        pattern = f"{broker}_{endpoint}_*.json"
        files = sorted(self._base_dir.glob(pattern), reverse=True)
        if not files:
            return None
        try:
            return json.loads(files[0].read_text(encoding="utf-8"))
        except Exception:
            return None

    def get_last_ok(
        self, broker: str, endpoint: str
    ) -> Optional[dict]:
        """Get the most recent snapshot that passed contract validation."""
        # For now, return the latest snapshot
        # In future, we could tag snapshots with pass/fail
        return self.get_latest(broker, endpoint)

    def purge_old(self, retention_days: int = 7) -> int:
        """Delete snapshots older than retention_days."""
        cutoff = datetime.now() - timedelta(days=retention_days)
        deleted = 0
        for f in self._base_dir.glob("*.json"):
            try:
                # Parse timestamp from filename
                parts = f.stem.split("_")
                if len(parts) >= 3:
                    date_part = parts[-2]  # YYYYMMDD
                    file_date = datetime.strptime(date_part, "%Y%m%d")
                    if file_date < cutoff:
                        f.unlink()
                        deleted += 1
            except (ValueError, IndexError):
                continue
        if deleted:
            logger.info(f"Purged {deleted} old snapshots")
        return deleted

    def list_snapshots(
        self, broker: Optional[str] = None
    ) -> list[str]:
        """List available snapshot files."""
        pattern = f"{broker}_*.json" if broker else "*.json"
        return [f.name for f in sorted(self._base_dir.glob(pattern))]
