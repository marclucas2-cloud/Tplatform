"""
Pre-flight Check V12 — verification au demarrage du worker.

Verifie TOUS les prerequis avant de trader :
  - Binance : auth, positions, cash spot > $0
  - IBKR live : connexion port 4002, equity > $5K
  - IBKR paper : connexion port 4003 (warning, pas bloquant)
  - Data FX : parquets < 48h
  - Data crypto : Binance API repond
  - Earn : USDC en Earn Flexible present
  - Margin : au moins 1 paire margin activee
  - Kill switch : pas actif par erreur
  - Disk : > 1GB libre
  - IB Gateway : port 4002 listen
  - Telegram : bot repond

Usage:
  python scripts/preflight_check.py          # Standalone
  from scripts.preflight_check import run_preflight  # In worker.py
"""
import json
import logging
import os
import shutil
import socket
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logger = logging.getLogger("preflight")


class PreflightResult:
    def __init__(self):
        self.checks: dict[str, dict] = {}
        self.blockers: list[str] = []
        self.warnings: list[str] = []

    def add(self, name: str, passed: bool, message: str, blocking: bool = True):
        self.checks[name] = {"passed": passed, "message": message}
        if not passed:
            if blocking:
                self.blockers.append(f"[BLOCKER] {name}: {message}")
            else:
                self.warnings.append(f"[WARNING] {name}: {message}")

    @property
    def all_passed(self) -> bool:
        return len(self.blockers) == 0

    def summary(self) -> str:
        passed = sum(1 for c in self.checks.values() if c["passed"])
        total = len(self.checks)
        lines = [f"PRE-FLIGHT: {passed}/{total} checks passed"]
        for name, c in self.checks.items():
            status = "PASS" if c["passed"] else "FAIL"
            lines.append(f"  [{status}] {name}: {c['message']}")
        if self.blockers:
            lines.append(f"\n{len(self.blockers)} BLOCKER(s) — WORKER CANNOT START:")
            lines.extend(f"  {b}" for b in self.blockers)
        if self.warnings:
            lines.append(f"\n{len(self.warnings)} warning(s):")
            lines.extend(f"  {w}" for w in self.warnings)
        return "\n".join(lines)


def run_preflight(block_on_failure: bool = True) -> PreflightResult:
    """Run all pre-flight checks. Returns PreflightResult."""
    result = PreflightResult()

    # 1. Binance auth + cash
    _check_binance(result)

    # 2. IBKR live (port 4002)
    _check_ibkr_live(result)

    # 3. IBKR paper (port 4003 — warning only)
    _check_ibkr_paper(result)

    # 4. Data FX parquets freshness
    _check_fx_data(result)

    # 5. Data crypto API
    _check_crypto_data(result)

    # 6. Earn USDC
    _check_earn(result)

    # 7. Margin
    _check_margin(result)

    # 8. Kill switch state
    _check_kill_switch(result)

    # 9. Disk space
    _check_disk(result)

    # 10. IB Gateway process
    _check_ibgateway(result)

    # 11. Telegram
    _check_telegram(result)

    # Log results
    logger.info(result.summary())

    # Persist result
    _persist_result(result)

    return result


def _check_binance(result: PreflightResult):
    """Binance: authenticate + get_positions + cash > $0."""
    if not os.getenv("BINANCE_API_KEY"):
        result.add("binance_auth", False, "BINANCE_API_KEY not set")
        return
    try:
        from core.broker.binance_broker import BinanceBroker
        broker = BinanceBroker()
        auth = broker.authenticate()
        result.add("binance_auth", True, f"OK (permissions: {auth.get('permissions', '?')})")

        acct = broker.get_account_info()
        cash = float(acct.get("cash", 0))
        equity = float(acct.get("equity", 0))
        result.add("binance_cash", cash > 0 or equity > 0,
                    f"cash=${cash:.0f}, equity=${equity:.0f}")

        positions = broker.get_positions()
        result.add("binance_positions", True, f"{len(positions)} position(s)")
    except Exception as e:
        result.add("binance_auth", False, f"ERREUR: {e}")


def _check_ibkr_live(result: PreflightResult):
    """IBKR live: connexion port 4002, equity > $5K."""
    host = os.getenv("IBKR_HOST", "127.0.0.1")
    port = int(os.getenv("IBKR_PORT", "4002"))
    try:
        with socket.create_connection((host, port), timeout=5):
            pass
        result.add("ibkr_live_connect", True, f"port {port} OK")
    except Exception as e:
        result.add("ibkr_live_connect", False, f"port {port} unreachable: {e}")
        return

    try:
        import random

        from core.broker.ibkr_adapter import IBKRBroker
        ibkr = IBKRBroker(client_id=random.randint(90, 99))  # Random clientId to avoid conflicts
        try:
            info = ibkr.get_account_info()
            equity = float(info.get("equity", 0))
            result.add("ibkr_live_equity", equity >= 5000,
                        f"equity=${equity:,.0f}" + (" (< $5K!)" if equity < 5000 else ""))
        finally:
            ibkr.disconnect()
    except Exception as e:
        result.add("ibkr_live_equity", False, f"ERREUR: {e}")


def _check_ibkr_paper(result: PreflightResult):
    """IBKR paper: port 4003 — warning only, not blocking."""
    host = os.getenv("IBKR_HOST", "127.0.0.1")
    paper_port = int(os.getenv("IBKR_PAPER_PORT", "4003"))
    try:
        with socket.create_connection((host, paper_port), timeout=3):
            pass
        result.add("ibkr_paper", True, f"port {paper_port} OK", blocking=False)
    except Exception:
        result.add("ibkr_paper", False, f"port {paper_port} unreachable", blocking=False)


def _check_fx_data(result: PreflightResult):
    """Data FX: each parquet < 72h old (tolere weekends, fermeture FX Ven 22h -> Lun 22h)."""
    data_dir = ROOT / "data" / "fx"
    if not data_dir.exists():
        result.add("fx_data", False, "data/fx/ n'existe pas")
        return

    stale = []
    now = time.time()
    for pair in ["AUDJPY", "USDJPY", "EURJPY", "NZDUSD"]:
        fpath = data_dir / f"{pair}_1D.parquet"
        if not fpath.exists():
            stale.append(f"{pair} (absent)")
        elif now - fpath.stat().st_mtime > 72 * 3600:
            hours = (now - fpath.stat().st_mtime) / 3600
            stale.append(f"{pair} ({hours:.0f}h)")

    if stale:
        result.add("fx_data", False, f"parquets stale: {', '.join(stale)}")
    else:
        result.add("fx_data", True, "4 parquets < 72h")


def _check_crypto_data(result: PreflightResult):
    """Crypto data: Binance API responds for BTCUSDC."""
    if not os.getenv("BINANCE_API_KEY"):
        result.add("crypto_data", False, "BINANCE_API_KEY not set", blocking=False)
        return
    try:
        from core.broker.binance_broker import BinanceBroker
        broker = BinanceBroker()
        prices = broker.get_prices("BTCUSDC", timeframe="4h", bars=5)
        bars = prices.get("bars", [])
        result.add("crypto_data", len(bars) > 0,
                    f"BTCUSDC: {len(bars)} bars" + (f", last close=${bars[-1]['c']:,.0f}" if bars else ""))
    except Exception as e:
        result.add("crypto_data", False, f"ERREUR: {e}")


def _check_earn(result: PreflightResult):
    """Earn: USDC en Earn Flexible present."""
    if not os.getenv("BINANCE_API_KEY"):
        result.add("earn_usdc", False, "BINANCE_API_KEY not set", blocking=False)
        return
    try:
        from core.broker.binance_broker import BinanceBroker
        broker = BinanceBroker()
        earn = broker.get_earn_positions()
        usdc = [e for e in earn if e.get("asset") == "USDC"]
        if usdc:
            amount = float(usdc[0].get("amount", 0))
            result.add("earn_usdc", amount > 0,
                        f"USDC Earn: ${amount:,.0f}", blocking=False)
        else:
            result.add("earn_usdc", False, "Pas de USDC en Earn Flexible", blocking=False)
    except Exception as e:
        result.add("earn_usdc", False, f"ERREUR: {e}", blocking=False)


def _check_margin(result: PreflightResult):
    """Margin: au moins BTCUSDC isolated margin enabled."""
    if not os.getenv("BINANCE_API_KEY"):
        result.add("margin", False, "BINANCE_API_KEY not set", blocking=False)
        return
    try:
        from core.broker.binance_broker import BinanceBroker
        broker = BinanceBroker()
        # Try to get margin account — if it works, margin is enabled
        resp = broker._request("GET", "/sapi/v1/margin/isolated/account", signed=True, weight=10)
        assets = resp.get("assets", [])
        enabled = [a["symbol"] for a in assets if a.get("enabled", False) or a.get("isolatedCreated", False)]
        btc_margin = "BTCUSDC" in enabled or "BTCUSDT" in enabled
        result.add("margin", btc_margin or len(enabled) > 0,
                    f"{len(enabled)} pair(s) margin: {', '.join(enabled[:5])}", blocking=False)
    except Exception as e:
        result.add("margin", False, f"ERREUR: {e}", blocking=False)


def _check_kill_switch(result: PreflightResult):
    """Kill switch: verify not active by mistake."""
    ks_path = ROOT / "data" / "crypto_kill_switch_state.json"
    if not ks_path.exists():
        result.add("kill_switch", True, "Pas de fichier state (inactif)")
        return
    try:
        data = json.loads(ks_path.read_text(encoding="utf-8"))
        active = data.get("active", False)
        reason = data.get("reason", "")
        if active:
            result.add("kill_switch", False,
                        f"ACTIF: {reason} — verifier si intentionnel", blocking=False)
        else:
            result.add("kill_switch", True, "Inactif (OK)")
    except Exception as e:
        result.add("kill_switch", False, f"Erreur lecture: {e}", blocking=False)


def _check_disk(result: PreflightResult):
    """Disk: > 1GB free."""
    try:
        usage = shutil.disk_usage(str(ROOT))
        free_gb = usage.free / (1024 ** 3)
        result.add("disk_space", free_gb > 1.0,
                    f"{free_gb:.1f} GB libre" + (" (< 1GB!)" if free_gb <= 1.0 else ""))
    except Exception as e:
        result.add("disk_space", False, f"ERREUR: {e}", blocking=False)


def _check_ibgateway(result: PreflightResult):
    """IB Gateway: port 4002 listens (already covered by ibkr_live, but explicit)."""
    host = os.getenv("IBKR_HOST", "127.0.0.1")
    port = int(os.getenv("IBKR_PORT", "4002"))
    try:
        with socket.create_connection((host, port), timeout=3):
            pass
        result.add("ibgateway", True, f"{host}:{port} OK")
    except Exception:
        result.add("ibgateway", False, f"{host}:{port} not listening")


def _check_telegram(result: PreflightResult):
    """Telegram: bot responds to a ping."""
    try:
        from core.telegram_alert import send_alert
        ok = send_alert("PREFLIGHT PING — bot alive check", level="info")
        result.add("telegram", ok, "Bot OK" if ok else "send_alert returned False",
                    blocking=False)
    except Exception as e:
        result.add("telegram", False, f"ERREUR: {e}", blocking=False)


def _persist_result(result: PreflightResult):
    """Save preflight result to data/monitoring/preflight.json."""
    out_path = ROOT / "data" / "monitoring" / "preflight.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        out_path.write_text(json.dumps({
            "timestamp": datetime.now(UTC).isoformat(),
            "all_passed": result.all_passed,
            "checks": result.checks,
            "blockers": result.blockers,
            "warnings": result.warnings,
        }, indent=2))
    except Exception:
        pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    result = run_preflight(block_on_failure=False)
    print("\n" + result.summary())

    if not result.all_passed:
        print(f"\n{len(result.blockers)} blocker(s) detected.")
        sys.exit(1)
    else:
        print("\nAll checks passed. Worker can start.")
