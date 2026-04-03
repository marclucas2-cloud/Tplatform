"""
ROC-001 Cash Sweep Earn — Place automatiquement le cash idle en Binance Earn Flexible.

Recuperation en < 1 minute quand un signal arrive.

Regles :
  - Garder MIN_CASH_BUFFER ($500) toujours liquide en spot
  - Ne pas sweeper moins de MIN_SWEEP_AMOUNT ($100)
  - Verifier toutes les CHECK_INTERVAL secondes (3600 = 1h)
  - Avant de passer un ordre, appeler on_signal_pre_order() pour liberer le cash

Interface broker attendue (duck typing) :
  - broker.get_account_info() -> dict avec "cash" ou "spot_usdt"
  - broker.get_earn_positions() -> list[dict] avec "asset", "amount", "product_id"
  - broker.subscribe_earn(product_id, amount) -> dict
  - broker.redeem_earn(product_id, amount) -> dict
"""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# Product ID par defaut pour USDT flexible sur Binance Simple Earn
_DEFAULT_USDT_PRODUCT_ID = "USDT001"


class CashSweepManager:
    """Place automatiquement le cash idle en Binance Earn Flexible.

    Recuperation en < 1 minute quand un signal arrive.
    """

    def __init__(
        self,
        broker,
        min_cash_buffer: float = 500.0,
        min_sweep_amount: float = 100.0,
        check_interval: int = 3600,
        usdt_product_id: str = _DEFAULT_USDT_PRODUCT_ID,
    ):
        self._broker = broker
        self._min_cash_buffer = min_cash_buffer
        self._min_sweep_amount = min_sweep_amount
        self._check_interval = check_interval
        self._usdt_product_id = usdt_product_id

        # Stats internes
        self._total_swept: float = 0.0
        self._total_redeemed: float = 0.0
        self._sweep_count: int = 0
        self._redeem_count: int = 0
        self._last_sweep_at: str | None = None
        self._last_redeem_at: str | None = None
        # Estimation APY (mise a jour depuis les positions Earn)
        self._estimated_apy: float = 0.0

    # ------------------------------------------------------------------
    # Methodes publiques
    # ------------------------------------------------------------------

    def sweep(self) -> dict:
        """Verifie le cash disponible et envoie l'excedent en Earn Flexible.

        Returns:
            dict avec "swept" (bool), "amount", "spot_before", "spot_after", etc.
        """
        try:
            spot_cash = self._get_spot_cash()
        except Exception as e:
            logger.error(f"CashSweep: impossible de lire le solde spot — {e}")
            return {"swept": False, "error": str(e)}

        excess = spot_cash - self._min_cash_buffer

        # Pas assez d'excedent pour sweeper
        if excess < self._min_sweep_amount:
            logger.debug(
                f"CashSweep: pas d'excedent a sweeper "
                f"(spot={spot_cash:.2f}, buffer={self._min_cash_buffer:.2f}, "
                f"excess={excess:.2f} < min_sweep={self._min_sweep_amount:.2f})"
            )
            return {
                "swept": False,
                "reason": "insufficient_excess",
                "spot_cash": round(spot_cash, 2),
                "excess": round(max(excess, 0), 2),
                "min_sweep": self._min_sweep_amount,
            }

        # Envoyer l'excedent en Earn
        try:
            result = self._broker.subscribe_earn(self._usdt_product_id, excess)
        except Exception as e:
            logger.error(f"CashSweep: subscribe_earn a echoue — {e}")
            return {"swept": False, "error": str(e), "amount": round(excess, 2)}

        # Mettre a jour les stats
        self._total_swept += excess
        self._sweep_count += 1
        self._last_sweep_at = datetime.now(UTC).isoformat()

        logger.info(
            f"CashSweep: ${excess:.2f} envoye en Earn Flexible "
            f"(spot avant={spot_cash:.2f}, apres={spot_cash - excess:.2f})"
        )

        return {
            "swept": True,
            "amount": round(excess, 2),
            "spot_before": round(spot_cash, 2),
            "spot_after": round(spot_cash - excess, 2),
            "product_id": self._usdt_product_id,
            "timestamp": self._last_sweep_at,
        }

    def on_signal_pre_order(self, required_cash: float) -> bool:
        """Avant un ordre, verifie si assez de cash. Redeem depuis Earn si besoin.

        Args:
            required_cash: montant necessaire en spot pour l'ordre

        Returns:
            True si le cash est disponible (ou a ete libere), False sinon.
        """
        try:
            spot_cash = self._get_spot_cash()
        except Exception as e:
            logger.error(f"CashSweep pre-order: impossible de lire le solde — {e}")
            return False

        # Assez de cash, pas besoin de redeem
        if spot_cash >= required_cash:
            logger.debug(
                f"CashSweep pre-order: cash suffisant "
                f"(spot={spot_cash:.2f} >= required={required_cash:.2f})"
            )
            return True

        # Calculer le montant a recuperer depuis Earn
        shortfall = required_cash - spot_cash
        earn_available = self._get_earn_usdt_balance()

        if earn_available < shortfall:
            logger.warning(
                f"CashSweep pre-order: cash insuffisant meme apres redeem "
                f"(spot={spot_cash:.2f} + earn={earn_available:.2f} "
                f"< required={required_cash:.2f})"
            )
            # Redeem tout ce qu'on peut quand meme
            if earn_available > 0:
                shortfall = earn_available
            else:
                return False

        # Redeem depuis Earn
        try:
            self._broker.redeem_earn(self._usdt_product_id, shortfall)
        except Exception as e:
            logger.error(f"CashSweep pre-order: redeem_earn a echoue — {e}")
            return False

        # Attendre que le redeem soit effectif (Binance Earn Flexible < 1 min)
        time.sleep(3)

        # Mettre a jour les stats
        self._total_redeemed += shortfall
        self._redeem_count += 1
        self._last_redeem_at = datetime.now(UTC).isoformat()

        logger.info(
            f"CashSweep pre-order: ${shortfall:.2f} recupere depuis Earn "
            f"(spot avant={spot_cash:.2f}, attendu apres={spot_cash + shortfall:.2f})"
        )

        return True

    def get_total_available(self) -> float:
        """Retourne le cash total disponible : spot + earn flexible USDT.

        Returns:
            Total en USD (USDT equivalent)
        """
        try:
            spot = self._get_spot_cash()
            earn = self._get_earn_usdt_balance()
            return round(spot + earn, 2)
        except Exception as e:
            logger.error(f"CashSweep get_total_available: erreur — {e}")
            return 0.0

    def get_sweep_stats(self) -> dict:
        """Retourne les statistiques du cash sweep.

        Returns:
            dict avec total_swept, total_redeemed, estimated_apy_earned, etc.
        """
        # Estimer les gains APY
        # Approximation : montant moyen en earn * APY * temps
        self._update_estimated_apy()
        estimated_apy_earned = self._estimate_apy_earned()

        return {
            "total_swept": round(self._total_swept, 2),
            "total_redeemed": round(self._total_redeemed, 2),
            "sweep_count": self._sweep_count,
            "redeem_count": self._redeem_count,
            "net_in_earn": round(self._total_swept - self._total_redeemed, 2),
            "estimated_apy": round(self._estimated_apy * 100, 2),
            "estimated_apy_earned": round(estimated_apy_earned, 2),
            "last_sweep_at": self._last_sweep_at,
            "last_redeem_at": self._last_redeem_at,
            "min_cash_buffer": self._min_cash_buffer,
            "min_sweep_amount": self._min_sweep_amount,
            "check_interval": self._check_interval,
        }

    # ------------------------------------------------------------------
    # Methodes internes
    # ------------------------------------------------------------------

    def _get_spot_cash(self) -> float:
        """Recupere le solde USDT spot depuis le broker."""
        info = self._broker.get_account_info()
        # Compatibilite : "spot_usdt" ou "cash"
        return float(info.get("spot_usdt", info.get("cash", 0)))

    def _get_earn_usdt_balance(self) -> float:
        """Recupere le solde USDT en Earn Flexible."""
        try:
            positions = self._broker.get_earn_positions()
            for p in positions:
                if p.get("asset") == "USDT":
                    return float(p.get("amount", 0))
        except Exception as e:
            logger.warning(f"CashSweep: impossible de lire les positions Earn — {e}")
        return 0.0

    def _update_estimated_apy(self) -> None:
        """Met a jour l'APY estimee depuis les positions Earn."""
        try:
            positions = self._broker.get_earn_positions()
            for p in positions:
                if p.get("asset") == "USDT":
                    self._estimated_apy = float(p.get("apy", 0))
                    return
        except Exception:
            pass

    def _estimate_apy_earned(self) -> float:
        """Estime les gains APY accumules.

        Approximation simplifiee : net_in_earn * APY / 365 * jours_actifs.
        Comme on n'a pas le tracking exact, on estime ~1 jour par sweep.
        """
        if self._estimated_apy <= 0 or self._sweep_count == 0:
            return 0.0
        net_in_earn = max(self._total_swept - self._total_redeemed, 0)
        # Approximation : 1 jour de rendement par sweep en moyenne
        estimated_days = max(self._sweep_count, 1)
        daily_rate = self._estimated_apy / 365
        return net_in_earn * daily_rate * estimated_days
