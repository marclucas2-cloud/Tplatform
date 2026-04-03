"""
State Guard -- safe JSON state file loading and saving.

Protects against corruption from truncated writes, power loss, or disk errors.
All state files (engine_state, crypto_dd_state, active_brackets, etc.) should
use these functions instead of raw json.load/json.dump.

Key guarantees:
  - safe_load_json: never raises, falls back to .bak then default
  - safe_save_json: atomic write via tmp+rename, auto-backup
  - All corruption events are logged to data/state_corruption_log.jsonl
"""
from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent
_CORRUPTION_LOG = _ROOT / "data" / "state_corruption_log.jsonl"


def _log_corruption(path: Path, error: str, recovered_from: str | None = None) -> None:
    """Append a corruption event to the JSONL log."""
    try:
        _CORRUPTION_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "file": str(path),
            "error": error,
            "recovered_from": recovered_from,
        }
        with open(_CORRUPTION_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.error(f"Failed to write corruption log: {e}")


def safe_load_json(
    path: Path,
    default: Any = None,
    backup: bool = True,
) -> Any:
    """Safely load a JSON file with fallback to backup.

    Guarantees:
      1. If the main file is valid JSON (dict or list), return it.
      2. If main is corrupt AND a .bak file exists and is valid, return .bak.
      3. If both fail, return ``default`` (never raises).

    Args:
        path: Path to the JSON file.
        default: Value returned when all sources fail.
        backup: If True, attempt fallback to path.bak on corruption.

    Returns:
        Parsed JSON data (dict or list), or ``default``.
    """
    path = Path(path)

    # --- Try main file ---
    main_data = _try_load(path)
    if main_data is not None:
        return main_data

    # Main file is missing or corrupt -- try backup
    if backup:
        bak_path = path.with_suffix(path.suffix + ".bak")
        bak_data = _try_load(bak_path)
        if bak_data is not None:
            _log_corruption(path, "main file corrupt, recovered from .bak", "backup")
            logger.warning(
                f"State file {path.name} corrupt -- recovered from {bak_path.name}"
            )
            return bak_data

    # Both failed -- log and return default
    if path.exists():
        _log_corruption(path, "main file corrupt, no valid backup, using default")
        logger.error(
            f"State file {path.name} corrupt with no backup -- returning default"
        )

    return default


def _try_load(path: Path) -> Any | None:
    """Attempt to load and validate a JSON file.

    Returns the parsed data if valid (dict or list), otherwise None.
    """
    if not path.exists():
        return None

    try:
        raw = path.read_text(encoding="utf-8").strip()
    except Exception as e:
        logger.warning(f"Failed to read {path}: {e}")
        return None

    if not raw:
        return None

    def _reject_nan(constant: str):
        """Reject NaN/Infinity which Python json.loads accepts by default."""
        raise ValueError(f"Invalid JSON constant: {constant}")

    try:
        data = json.loads(raw, parse_constant=_reject_nan)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"Invalid JSON in {path}: {e}")
        return None

    # Validate type -- state files must be dict or list
    if not isinstance(data, (dict, list)):
        logger.warning(f"Unexpected JSON type in {path}: {type(data).__name__}")
        return None

    return data


def safe_save_json(path: Path, data: Any) -> bool:
    """Atomically write JSON data to a file.

    Steps:
      1. Serialize to .tmp file
      2. Read back .tmp and validate it is parseable JSON
      3. Rename existing file to .bak
      4. Rename .tmp to main file

    If any step fails, the original file is left untouched.

    Args:
        path: Destination path.
        data: Data to serialize (must be JSON-serializable).

    Returns:
        True on success, False on failure.
    """
    path = Path(path)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    bak_path = path.with_suffix(path.suffix + ".bak")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)

        # Step 1: Write to tmp
        serialized = json.dumps(data, indent=2, default=str)
        tmp_path.write_text(serialized, encoding="utf-8")

        # Step 2: Validate tmp by reading it back
        verify = json.loads(tmp_path.read_text(encoding="utf-8"))
        if verify != data:
            logger.error(f"Verification mismatch for {path}")
            _cleanup_tmp(tmp_path)
            return False

        # Step 3: Backup existing file
        if path.exists():
            try:
                if bak_path.exists():
                    bak_path.unlink()
                os.replace(str(path), str(bak_path))
            except Exception as e:
                logger.warning(f"Failed to create backup of {path}: {e}")
                # Continue anyway -- the tmp is valid

        # Step 4: Rename tmp to main
        os.replace(str(tmp_path), str(path))
        return True

    except Exception as e:
        logger.error(f"Failed to save {path}: {e}")
        _cleanup_tmp(tmp_path)
        return False


def _cleanup_tmp(tmp_path: Path) -> None:
    """Remove a leftover .tmp file if it exists."""
    try:
        if tmp_path.exists():
            tmp_path.unlink()
    except Exception:
        pass
