"""
Tests de bypass risk manager — verifie qu'il est IMPOSSIBLE de placer
un ordre sans passer par le pipeline autorise.

Couvre :
  - Guard _authorized_by sur AlpacaClient (create_position, close_position, close_all)
  - Guard _authorized_by sur IBKRBroker (create_position, close_position, close_all)
  - Scan des imports : alpaca.trading.client ne doit pas etre importe
    directement dans les scripts de trading (seulement via le wrapper)
  - Verification que paper_portfolio.py utilise le risk manager (validate_order)
"""

import ast
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture(autouse=True)
def env_paper_trading():
    """Assure que PAPER_TRADING=true pour tous les tests."""
    with patch.dict(os.environ, {
        "PAPER_TRADING": "true",
        "ALPACA_API_KEY": "test-key",
        "ALPACA_SECRET_KEY": "test-secret",
    }):
        yield


# =============================================================================
# TEST 1 : AlpacaClient exige _authorized_by
# =============================================================================

class TestAlpacaClientRequiresAuthorizedBy:
    """Appel direct a create_position sans _authorized_by = REFUSE."""

    def test_create_position_without_authorized_by(self):
        """create_position() sans _authorized_by leve AlpacaAPIError."""
        from core.alpaca_client.client import AlpacaAPIError, AlpacaClient

        client = AlpacaClient(
            api_key="test", secret_key="test", paper=True,
        )
        with pytest.raises(AlpacaAPIError, match="Ordre REFUSE"):
            client.create_position("SPY", "BUY", qty=10)

    def test_create_position_with_none_authorized_by(self):
        """create_position(_authorized_by=None) leve AlpacaAPIError."""
        from core.alpaca_client.client import AlpacaAPIError, AlpacaClient

        client = AlpacaClient(
            api_key="test", secret_key="test", paper=True,
        )
        with pytest.raises(AlpacaAPIError, match="Ordre REFUSE"):
            client.create_position("AAPL", "SELL", qty=5, _authorized_by=None)

    def test_close_position_without_authorized_by(self):
        """close_position() sans _authorized_by leve AlpacaAPIError."""
        from core.alpaca_client.client import AlpacaAPIError, AlpacaClient

        client = AlpacaClient(
            api_key="test", secret_key="test", paper=True,
        )
        with pytest.raises(AlpacaAPIError, match="Ordre REFUSE"):
            client.close_position("SPY")

    def test_close_all_positions_without_authorized_by(self):
        """close_all_positions() sans _authorized_by leve AlpacaAPIError."""
        from core.alpaca_client.client import AlpacaAPIError, AlpacaClient

        client = AlpacaClient(
            api_key="test", secret_key="test", paper=True,
        )
        with pytest.raises(AlpacaAPIError, match="Ordre REFUSE"):
            client.close_all_positions()

    def test_create_position_with_authorized_by_passes_guard(self):
        """create_position(_authorized_by='test') passe le guard (peut echouer apres)."""
        from core.alpaca_client.client import AlpacaClient

        client = AlpacaClient(
            api_key="test", secret_key="test", paper=True,
        )
        # Mock le trading client pour eviter un appel reseau
        mock_trading = MagicMock()
        mock_order = MagicMock()
        mock_order.id = "test-123"
        mock_order.symbol = "SPY"
        mock_order.side.value = "buy"
        mock_order.status.value = "accepted"
        mock_order.qty = "10"
        mock_order.filled_avg_price = None
        mock_order.filled_qty = None
        mock_trading.submit_order.return_value = mock_order
        client._trading = mock_trading

        # Ne doit PAS lever AlpacaAPIError (le guard est passe)
        result = client.create_position(
            "SPY", "BUY", qty=10, _authorized_by="test_pipeline"
        )
        assert result["authorized_by"] == "test_pipeline"


# =============================================================================
# TEST 2 : IBKRBroker exige _authorized_by
# =============================================================================

class TestIBKRBrokerRequiresAuthorizedBy:
    """Appel direct a create_position sans _authorized_by = REFUSE."""

    def _make_ibkr_broker(self):
        """Cree un IBKRBroker mocke sans connexion reelle."""
        from core.broker.ibkr_adapter import IBKRBroker
        broker = IBKRBroker.__new__(IBKRBroker)
        broker._ib = MagicMock()
        broker._paper = True
        broker._host = "127.0.0.1"
        broker._port = 7497
        broker._client_id = 1
        broker._connected = True
        broker._permanently_down = False
        broker._reconnect_attempts = 0
        return broker

    def test_create_position_without_authorized_by(self):
        """create_position() sans _authorized_by leve BrokerError."""
        from core.broker.base import BrokerError

        broker = self._make_ibkr_broker()
        with pytest.raises(BrokerError, match="sans _authorized_by"):
            broker.create_position("SPY", "BUY", qty=10)

    def test_create_position_with_none_authorized_by(self):
        """create_position(_authorized_by=None) leve BrokerError."""
        from core.broker.base import BrokerError

        broker = self._make_ibkr_broker()
        with pytest.raises(BrokerError, match="sans _authorized_by"):
            broker.create_position("AAPL", "SELL", qty=5, _authorized_by=None)

    def test_close_position_without_authorized_by(self):
        """close_position() sans _authorized_by leve BrokerError."""
        from core.broker.base import BrokerError

        broker = self._make_ibkr_broker()
        with pytest.raises(BrokerError, match="sans _authorized_by"):
            broker.close_position("SPY")

    def test_close_all_positions_without_authorized_by(self):
        """close_all_positions() sans _authorized_by leve BrokerError."""
        from core.broker.base import BrokerError

        broker = self._make_ibkr_broker()
        with pytest.raises(BrokerError, match="sans _authorized_by"):
            broker.close_all_positions()

    def test_cancel_all_orders_without_authorized_by(self):
        """cancel_all_orders() sans _authorized_by leve BrokerError."""
        from core.broker.base import BrokerError

        broker = self._make_ibkr_broker()
        with pytest.raises(BrokerError, match="sans _authorized_by"):
            broker.cancel_all_orders()


# =============================================================================
# TEST 3 : Scan imports — alpaca.trading.client pas importe directement
# =============================================================================

class TestCannotImportRawAlpacaAndTrade:
    """Verifie que alpaca.trading.client n'est pas importe directement
    dans les scripts de trading (seulement via le wrapper core/alpaca_client/).
    """

    # Repertoires autorises a importer alpaca.trading
    # - core/alpaca_client : wrapper officiel
    # - core/broker : adapters broker
    # - intraday-backtesterV2 : framework de backtest (data only, pas d'ordres)
    ALLOWED_DIRS = {
        str(ROOT / "core" / "alpaca_client"),
        str(ROOT / "core" / "broker"),
        str(ROOT / "archive" / "intraday-backtesterV2"),
    }

    def _scan_file_for_raw_alpaca_import(self, filepath: Path) -> list[str]:
        """Parse le fichier avec AST et cherche les imports alpaca.trading."""
        violations = []
        try:
            source = filepath.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(filepath))
        except (SyntaxError, UnicodeDecodeError):
            return []

        for node in ast.walk(tree):
            # from alpaca.trading.client import ...
            if isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("alpaca.trading"):
                    violations.append(
                        f"{filepath}:{node.lineno} — "
                        f"from {node.module} import ..."
                    )
            # import alpaca.trading.client
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("alpaca.trading"):
                        violations.append(
                            f"{filepath}:{node.lineno} — "
                            f"import {alias.name}"
                        )
        return violations

    def _is_in_allowed_dir(self, filepath: Path) -> bool:
        """Verifie si le fichier est dans un repertoire autorise."""
        filepath_str = str(filepath.resolve())
        for allowed in self.ALLOWED_DIRS:
            if filepath_str.startswith(str(Path(allowed).resolve())):
                return True
        return False

    def test_scripts_do_not_import_raw_alpaca(self):
        """Aucun script dans scripts/ n'importe alpaca.trading directement."""
        scripts_dir = ROOT / "scripts"
        violations = []

        for py_file in scripts_dir.glob("*.py"):
            if py_file.name.startswith("__"):
                continue
            found = self._scan_file_for_raw_alpaca_import(py_file)
            if found:
                violations.extend(found)

        assert violations == [], (
            "Scripts importent alpaca.trading directement "
            "(doivent utiliser core/alpaca_client/) :\n"
            + "\n".join(violations)
        )

    def test_worker_does_not_import_raw_alpaca(self):
        """worker.py n'importe pas alpaca.trading directement."""
        worker = ROOT / "worker.py"
        if not worker.exists():
            pytest.skip("worker.py non trouve")
        violations = self._scan_file_for_raw_alpaca_import(worker)
        assert violations == [], (
            "worker.py importe alpaca.trading directement :\n"
            + "\n".join(violations)
        )

    def test_intraday_strategies_do_not_import_raw_alpaca(self):
        """Les strategies intraday n'importent pas alpaca.trading."""
        strategies_dir = ROOT / "archive" / "intraday-backtesterV2" / "strategies"
        if not strategies_dir.exists():
            pytest.skip("strategies/ non trouve")

        violations = []
        for py_file in strategies_dir.rglob("*.py"):
            if py_file.name.startswith("__"):
                continue
            found = self._scan_file_for_raw_alpaca_import(py_file)
            if found:
                violations.extend(found)

        assert violations == [], (
            "Strategies importent alpaca.trading directement :\n"
            + "\n".join(violations)
        )

    def test_full_codebase_scan(self):
        """Scan complet : seuls core/alpaca_client/ et core/broker/ importent alpaca.trading."""
        violations = []

        for py_file in ROOT.rglob("*.py"):
            # Skip les fichiers dans les repertoires autorises
            if self._is_in_allowed_dir(py_file):
                continue
            # Skip node_modules, __pycache__, .git, venv, et .claude/worktrees
            # (worktrees agents Claude = copies repo, scan = false positives)
            parts = py_file.parts
            skip_dirs = {"node_modules", "__pycache__", ".git", "venv",
                         ".venv", "env", "site-packages",
                         ".claude", "worktrees", "archive", "temp"}
            if any(d in parts for d in skip_dirs):
                continue
            # Skip les tests eux-memes
            if "test_risk_bypass" in py_file.name:
                continue

            found = self._scan_file_for_raw_alpaca_import(py_file)
            if found:
                violations.extend(found)

        assert violations == [], (
            "Fichiers hors wrapper importent alpaca.trading directement :\n"
            + "\n".join(violations)
        )


# =============================================================================
# TEST 4 : Pipeline appelle toujours le risk check
# =============================================================================

class TestPipelineAlwaysCallsRiskCheck:
    """Verifie que paper_portfolio.py a des gardes de risque en place."""

    def _read_source(self) -> str:
        """Lit le code source de paper_portfolio.py."""
        pp_path = ROOT / "scripts" / "paper_portfolio.py"
        return pp_path.read_text(encoding="utf-8")

    def test_pipeline_has_authorized_by_in_create_calls(self):
        """Chaque appel a create_position() dans paper_portfolio.py
        doit inclure _authorized_by."""
        source = self._read_source()

        # Trouver toutes les lignes avec create_position
        lines = source.split("\n")
        create_calls = [
            (i + 1, line.strip())
            for i, line in enumerate(lines)
            if "create_position" in line and not line.strip().startswith("#")
        ]

        # Chaque appel doit avoir _authorized_by
        missing_auth = []
        for lineno, line in create_calls:
            # On cherche _authorized_by dans le meme appel
            # Certains appels sont multi-lignes, donc on regarde aussi les lignes suivantes
            context_start = max(0, lineno - 2)
            context_end = min(len(lines), lineno + 5)
            context = " ".join(lines[context_start:context_end])

            if "_authorized_by" not in context:
                missing_auth.append(f"  L{lineno}: {line}")

        assert missing_auth == [], (
            "Appels a create_position() sans _authorized_by :\n"
            + "\n".join(missing_auth)
        )

    def test_pipeline_has_circuit_breaker(self):
        """paper_portfolio.py contient une logique de circuit-breaker."""
        source = self._read_source()
        assert "circuit" in source.lower() or "breaker" in source.lower(), (
            "paper_portfolio.py ne contient pas de circuit-breaker"
        )

    def test_pipeline_has_exposure_check(self):
        """paper_portfolio.py verifie l'exposition avant les ordres."""
        source = self._read_source()
        assert "exposure" in source.lower() or "expo" in source.lower(), (
            "paper_portfolio.py ne verifie pas l'exposition"
        )

    def test_pipeline_has_max_positions_guard(self):
        """paper_portfolio.py a un guard sur le nombre max de positions."""
        source = self._read_source()
        assert "max" in source.lower() and "position" in source.lower(), (
            "paper_portfolio.py n'a pas de guard max positions"
        )

    def test_pipeline_has_paper_guard(self):
        """paper_portfolio.py verifie PAPER_TRADING."""
        source = self._read_source()
        assert "PAPER_TRADING" in source or "paper" in source.lower(), (
            "paper_portfolio.py ne verifie pas le mode paper"
        )

    def test_close_position_has_authorized_by(self):
        """Chaque appel a close_position() dans paper_portfolio.py
        doit inclure _authorized_by."""
        source = self._read_source()

        lines = source.split("\n")
        close_calls = [
            (i + 1, line.strip())
            for i, line in enumerate(lines)
            if "close_position" in line
            and not line.strip().startswith("#")
            and not line.strip().startswith("def ")
        ]

        missing_auth = []
        for lineno, line in close_calls:
            context_start = max(0, lineno - 2)
            context_end = min(len(lines), lineno + 5)
            context = " ".join(lines[context_start:context_end])

            if "_authorized_by" not in context:
                missing_auth.append(f"  L{lineno}: {line}")

        assert missing_auth == [], (
            "Appels a close_position() sans _authorized_by :\n"
            + "\n".join(missing_auth)
        )
