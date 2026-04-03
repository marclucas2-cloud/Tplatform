"""
Système de logging structuré et audit trail.
Chaque ligne est un JSON loggable, reproductible et indexable.
"""
import json
import logging
import logging.handlers
import sys
from datetime import UTC, datetime
from pathlib import Path


def setup_logging(log_level: str = "INFO", log_dir: str = "logs"):
    """Configure le logging structuré pour toute la plateforme."""
    level = getattr(logging, log_level.upper(), logging.INFO)

    # Formatter JSON pour fichiers
    formatter = logging.Formatter(
        fmt='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":%(message)s}',
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # Formatter lisible pour console
    console_fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    # Console (UTF-8 forcé pour Windows)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(console_fmt)
    console.setLevel(level)
    root.addHandler(console)

    # Fichier rotatif
    log_path = Path(log_dir)
    log_path.mkdir(exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_path / "trading.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Fichier audit (append uniquement — trace complète)
    audit_handler = logging.FileHandler(
        log_path / "audit.log", mode="a", encoding="utf-8"
    )
    audit_handler.setFormatter(formatter)
    audit_handler.setLevel(logging.INFO)
    logging.getLogger("agent").addHandler(audit_handler)


class AuditLogger:
    """
    Logger d'audit — chaque événement critique est horodaté et sérialisé.
    Utilisé pour la reproductibilité des backtests et la traçabilité des ordres.
    """

    def __init__(self, path: str = "logs/audit_events.jsonl"):
        self._path = Path(path)
        self._path.parent.mkdir(exist_ok=True)

    def log(self, event_type: str, data: dict):
        entry = {
            "ts": datetime.now(UTC).isoformat(),
            "type": event_type,
            **data,
        }
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
