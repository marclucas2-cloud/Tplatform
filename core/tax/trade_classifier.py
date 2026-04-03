"""
D9-01 — Trade Tax Classifier (France).

Classifies each trade for French tax reporting:

CRYPTO (PFU 30% sur PV nettes annuelles):
  - Fait generateur: cession crypto → fiat (EUR/USD)
  - PAS de fait generateur: crypto → crypto (BTCUSDC → ETHUSDC)
  - PAS de fait generateur: staking/earn rewards (tant que non converti en fiat)
  - Methode: PMP (prix moyen pondere d'acquisition)

VALEURS MOBILIERES (PFU 30% ou bareme progressif):
  - IBKR FX: PV/MV sur chaque trade cloture
  - IBKR EU: PV/MV + dividendes
  - Alpaca US: PV/MV + withholding tax US (15% convention FR-US)

Formulaires:
  - 2086: plus-values crypto
  - 2074: plus-values mobilieres
  - 3916-bis: comptes crypto a l'etranger (Binance, IBKR, Alpaca)

Flags:
  - crypto → EUR detected → WARNING (should use crypto→crypto)
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
LOG_PATH = ROOT / "data" / "tax" / "classified_trades.jsonl"
SUMMARY_PATH = ROOT / "data" / "tax" / "tax_summary.json"

# Stablecoins are crypto (not fiat) → crypto-to-stablecoin = not taxable
STABLECOINS = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "FDUSD"}
FIAT = {"EUR", "USD", "GBP", "CHF", "JPY"}


class TaxCategory:
    CRYPTO_PFU = "CRYPTO_PFU"              # PFU 30% on net gains
    CRYPTO_NOT_TAXABLE = "CRYPTO_NOT_TAX"  # crypto → crypto, no tax event
    CRYPTO_EARN = "CRYPTO_EARN"            # earn rewards, taxable at perception but not PV
    VM_PFU = "VM_PFU"                      # Valeurs mobilieres PFU 30%
    VM_US_WHT = "VM_US_WHT"               # US with 15% withholding tax
    FX_PFU = "FX_PFU"                      # FX gains PFU


@dataclass
class ClassifiedTrade:
    """A trade classified for tax purposes."""
    timestamp: str
    broker: str
    strategy: str
    ticker: str
    side: str
    quantity: float
    entry_price: float
    exit_price: float
    pnl: float
    category: str           # TaxCategory
    is_taxable_event: bool
    form: str               # 2086, 2074, or N/A
    notes: str = ""
    withholding_tax: float = 0.0


class TradeTaxClassifier:
    """Classifies trades for French tax reporting.

    Usage::

        classifier = TradeTaxClassifier()
        result = classifier.classify(
            broker="BINANCE",
            ticker="BTCUSDC",
            side="SELL",
            quantity=0.1,
            entry_price=67000,
            exit_price=68000,
            pnl=100,
            strategy="crypto_dual_momentum",
        )
    """

    def __init__(self, alert_callback=None):
        self._alert = alert_callback
        self._running_pv: dict[str, float] = {
            TaxCategory.CRYPTO_PFU: 0.0,
            TaxCategory.VM_PFU: 0.0,
            TaxCategory.FX_PFU: 0.0,
        }
        self._trade_count = 0

    def classify(
        self,
        broker: str,
        ticker: str,
        side: str,
        quantity: float,
        entry_price: float,
        exit_price: float,
        pnl: float,
        strategy: str = "",
        timestamp: str | None = None,
    ) -> ClassifiedTrade:
        """Classify a single trade."""
        ts = timestamp or datetime.now(UTC).isoformat()
        broker_upper = broker.upper()

        # Determine category
        if broker_upper == "BINANCE":
            result = self._classify_crypto(
                ticker, side, quantity, entry_price, exit_price, pnl, strategy, ts,
            )
        elif broker_upper == "IBKR":
            result = self._classify_ibkr(
                ticker, side, quantity, entry_price, exit_price, pnl, strategy, ts,
            )
        elif broker_upper == "ALPACA":
            result = self._classify_alpaca(
                ticker, side, quantity, entry_price, exit_price, pnl, strategy, ts,
            )
        else:
            result = ClassifiedTrade(
                timestamp=ts, broker=broker_upper, strategy=strategy,
                ticker=ticker, side=side, quantity=quantity,
                entry_price=entry_price, exit_price=exit_price, pnl=pnl,
                category=TaxCategory.VM_PFU, is_taxable_event=True,
                form="2074", notes="Unknown broker, default VM",
            )

        result.broker = broker_upper
        self._trade_count += 1

        # Update running PV
        if result.is_taxable_event and result.category in self._running_pv:
            self._running_pv[result.category] += pnl

        self._save(result)
        return result

    def _classify_crypto(
        self, ticker, side, qty, entry, exit, pnl, strategy, ts,
    ) -> ClassifiedTrade:
        """Classify Binance crypto trade."""
        # Extract base and quote from ticker
        quote = ""
        base = ticker
        for s in STABLECOINS | FIAT:
            if ticker.endswith(s):
                quote = s
                base = ticker[:-len(s)]
                break

        # Check if it's a fiat cession (taxable)
        is_fiat_cession = quote in FIAT

        # Crypto → crypto (including stablecoin) = NOT taxable
        if not is_fiat_cession:
            return ClassifiedTrade(
                timestamp=ts, broker="BINANCE", strategy=strategy,
                ticker=ticker, side=side, quantity=qty,
                entry_price=entry, exit_price=exit, pnl=pnl,
                category=TaxCategory.CRYPTO_NOT_TAXABLE,
                is_taxable_event=False, form="N/A",
                notes="Crypto-to-crypto/stablecoin: not a taxable event (FR)",
            )

        # Crypto → fiat = TAXABLE (PFU 30%)
        # FLAG: this should be avoided
        if self._alert:
            self._alert(
                f"CRYPTO → EUR DETECTED: {ticker} — should use crypto→USDC instead",
                level="warning",
            )

        return ClassifiedTrade(
            timestamp=ts, broker="BINANCE", strategy=strategy,
            ticker=ticker, side=side, quantity=qty,
            entry_price=entry, exit_price=exit, pnl=pnl,
            category=TaxCategory.CRYPTO_PFU,
            is_taxable_event=True, form="2086",
            notes="WARNING: crypto-to-fiat cession, generates tax event",
        )

    def _classify_ibkr(
        self, ticker, side, qty, entry, exit, pnl, strategy, ts,
    ) -> ClassifiedTrade:
        """Classify IBKR trade (FX or EU equities)."""
        # FX pairs
        is_fx = (
            "." in ticker
            or (any(ticker.startswith(c) for c in ["EUR", "GBP", "AUD", "USD", "NZD", "CHF", "JPY"])
            and len(ticker) <= 7)
        )

        if is_fx or "carry" in strategy.lower() or "fx" in strategy.lower():
            return ClassifiedTrade(
                timestamp=ts, broker="IBKR", strategy=strategy,
                ticker=ticker, side=side, quantity=qty,
                entry_price=entry, exit_price=exit, pnl=pnl,
                category=TaxCategory.FX_PFU,
                is_taxable_event=True, form="2074",
                notes="FX gain/loss (PFU 30%)",
            )

        # EU equities
        return ClassifiedTrade(
            timestamp=ts, broker="IBKR", strategy=strategy,
            ticker=ticker, side=side, quantity=qty,
            entry_price=entry, exit_price=exit, pnl=pnl,
            category=TaxCategory.VM_PFU,
            is_taxable_event=True, form="2074",
            notes="EU equity gain/loss (PFU 30%)",
        )

    def _classify_alpaca(
        self, ticker, side, qty, entry, exit, pnl, strategy, ts,
    ) -> ClassifiedTrade:
        """Classify Alpaca US equity trade."""
        # US equities: 15% withholding tax on dividends (convention FR-US)
        wht = 0.0  # Only on dividends, not on capital gains
        return ClassifiedTrade(
            timestamp=ts, broker="ALPACA", strategy=strategy,
            ticker=ticker, side=side, quantity=qty,
            entry_price=entry, exit_price=exit, pnl=pnl,
            category=TaxCategory.VM_US_WHT,
            is_taxable_event=True, form="2074",
            notes="US equity (PFU 30%, 15% WHT on dividends via convention)",
            withholding_tax=wht,
        )

    def get_summary(self) -> dict:
        """Annual tax summary."""
        summary = {
            "timestamp": datetime.now(UTC).isoformat(),
            "total_trades_classified": self._trade_count,
            "running_pv_by_category": {
                k: round(v, 2) for k, v in self._running_pv.items()
            },
            "forms_needed": ["3916-bis (comptes etrangers: Binance, IBKR, Alpaca)"],
            "estimated_tax": {},
        }

        for cat, pv in self._running_pv.items():
            if pv > 0:
                tax = pv * 0.30  # PFU 30%
                summary["estimated_tax"][cat] = round(tax, 2)
                if cat == TaxCategory.CRYPTO_PFU:
                    summary["forms_needed"].append("2086 (PV crypto)")
                elif cat in (TaxCategory.VM_PFU, TaxCategory.FX_PFU):
                    summary["forms_needed"].append("2074 (PV mobilieres)")

        # Save
        try:
            SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(SUMMARY_PATH, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2, default=str)
        except Exception as e:
            logger.error("Tax summary save failed: %s", e)

        return summary

    def _save(self, trade: ClassifiedTrade) -> None:
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(trade), default=str) + "\n")
        except Exception as e:
            logger.error("Tax classification save failed: %s", e)
