"""
Module futures IBKR — gestion des contrats, front month, roll, margin.

Supporte les micro-contrats (MES, MNQ, MCL, MGC) et full-size (ES, NQ, CL, GC).
Securite : micro uniquement pour capital < $100K.

Utilise ib_insync pour la resolution des contrats et les ordres futures.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Charger les specs depuis le YAML
_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "futures_contracts.yaml"


def _load_config() -> dict:
    """Charge la config futures depuis le YAML."""
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


# Mapping mois CME → numero de mois
MONTH_CODES = {
    "F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6,
    "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12,
}
MONTH_CODE_REVERSE = {v: k for k, v in MONTH_CODES.items()}

# Tous les mois (pour CL/MCL)
ALL_MONTHS = "FGHJKMNQUVXZ"


class FuturesContractManager:
    """Gere les contrats futures : resolution, expiry, roll, margin.

    Usage:
        mgr = FuturesContractManager()
        front = mgr.get_front_month("MES")
        # → {"symbol": "MES", "expiry": "2026-06-19", "month_code": "M",
        #    "exchange": "CME", "multiplier": 5}
    """

    def __init__(self, config: dict | None = None):
        if config is None:
            config = _load_config()
        self._config = config

        # Fusionner micro + full-size dans un dict unique
        self._contracts: dict[str, dict] = {}
        for sym, spec in config.get("micro_contracts", {}).items():
            self._contracts[sym] = {**spec, "is_micro": True}
        for sym, spec in config.get("full_size_contracts", {}).items():
            self._contracts[sym] = {**spec, "is_micro": False}

        self._risk = config.get("futures_risk", {})
        self._expiry_calendar = config.get("expiry_calendar_2026", {})
        self._monthly_expiry = config.get("monthly_expiry_2026", {})

    @property
    def supported_symbols(self) -> list[str]:
        """Retourne tous les symboles futures supportes."""
        return list(self._contracts.keys())

    def get_contract_spec(self, symbol: str) -> dict:
        """Retourne les specifications du contrat.

        Args:
            symbol: ex. "MES", "NQ", "MCL"

        Returns:
            Dict complet des specs (exchange, multiplier, margin, etc.)

        Raises:
            ValueError: si le symbole n'est pas supporte
        """
        if symbol not in self._contracts:
            raise ValueError(
                f"Contrat futures inconnu: {symbol}. "
                f"Supportes: {self.supported_symbols}"
            )
        return self._contracts[symbol].copy()

    def get_front_month(self, symbol: str, ref_date: date | None = None) -> dict:
        """Retourne le front month (contrat le plus proche non expire).

        Args:
            symbol: symbole futures (ex. "MES")
            ref_date: date de reference (defaut: aujourd'hui)

        Returns:
            {symbol, expiry, month_code, exchange, multiplier, local_symbol}
        """
        if ref_date is None:
            ref_date = date.today()

        spec = self.get_contract_spec(symbol)
        expiry, month_code = self._find_nearest_expiry(symbol, spec, ref_date)

        year = expiry.year
        local_symbol = f"{symbol}{month_code}{year % 100:02d}"

        return {
            "symbol": symbol,
            "expiry": expiry.isoformat(),
            "month_code": month_code,
            "exchange": spec["exchange"],
            "multiplier": spec["multiplier"],
            "local_symbol": local_symbol,
        }

    def get_next_month(self, symbol: str, ref_date: date | None = None) -> dict:
        """Retourne le next month (contrat suivant le front month).

        Args:
            symbol: symbole futures
            ref_date: date de reference

        Returns:
            Meme format que get_front_month
        """
        if ref_date is None:
            ref_date = date.today()

        spec = self.get_contract_spec(symbol)
        # Trouver le front month d'abord
        front_expiry, _ = self._find_nearest_expiry(symbol, spec, ref_date)
        # Ensuite chercher le suivant en commencant apres l'expiry du front
        next_expiry, month_code = self._find_nearest_expiry(
            symbol, spec, front_expiry + timedelta(days=1)
        )

        year = next_expiry.year
        local_symbol = f"{symbol}{month_code}{year % 100:02d}"

        return {
            "symbol": symbol,
            "expiry": next_expiry.isoformat(),
            "month_code": month_code,
            "exchange": spec["exchange"],
            "multiplier": spec["multiplier"],
            "local_symbol": local_symbol,
        }

    def should_roll(
        self, symbol: str, days_before_expiry: int = 5, ref_date: date | None = None
    ) -> bool:
        """Determine si un roll est necessaire.

        Un roll est declenche quand on est a <= days_before_expiry jours
        de l'expiration du front month.

        Args:
            symbol: symbole futures
            days_before_expiry: nombre de jours avant expiry pour roller (defaut 5)
            ref_date: date de reference

        Returns:
            True si le roll doit etre execute
        """
        if ref_date is None:
            ref_date = date.today()

        front = self.get_front_month(symbol, ref_date)
        expiry = date.fromisoformat(front["expiry"])
        days_to_expiry = (expiry - ref_date).days

        should = days_to_expiry <= days_before_expiry
        if should:
            logger.info(
                f"ROLL necessaire pour {symbol}: {days_to_expiry}j avant expiry "
                f"({front['expiry']}) — seuil={days_before_expiry}j"
            )
        return should

    def get_margin_requirement(self, symbol: str) -> dict:
        """Retourne les marges initiale et de maintenance.

        Returns:
            {initial: float, maintenance: float}
        """
        spec = self.get_contract_spec(symbol)
        return {
            "initial": float(spec.get("margin_initial", 0)),
            "maintenance": float(spec.get("margin_maintenance", 0)),
        }

    def points_to_dollars(self, symbol: str, points: float) -> float:
        """Convertit des points en dollars pour un symbole.

        Args:
            symbol: symbole futures
            points: nombre de points (ex. 10.5 points S&P)

        Returns:
            Valeur en dollars (ex. 10.5 * 5 = $52.50 pour MES)
        """
        spec = self.get_contract_spec(symbol)
        return points * spec["point_value"]

    def dollars_to_points(self, symbol: str, dollars: float) -> float:
        """Convertit des dollars en points pour un symbole.

        Args:
            symbol: symbole futures
            dollars: montant en dollars

        Returns:
            Nombre de points
        """
        spec = self.get_contract_spec(symbol)
        pv = spec["point_value"]
        if pv == 0:
            raise ValueError(f"point_value = 0 pour {symbol}")
        return dollars / pv

    def is_micro(self, symbol: str) -> bool:
        """Verifie si un symbole est un micro-contrat."""
        spec = self.get_contract_spec(symbol)
        return spec.get("is_micro", False)

    def validate_capital(self, symbol: str, total_capital: float) -> tuple[bool, str]:
        """Verifie qu'on a le capital suffisant pour trader ce contrat.

        Regles:
          - Micro uniquement si capital < $100K
          - Full-size interdit si capital < $100K
          - Marge initiale doit etre < 10% du capital

        Returns:
            (ok: bool, message: str)
        """
        spec = self.get_contract_spec(symbol)
        max_micro_only = self._risk.get("max_capital_for_micro_only", 100_000)
        max_margin_pct = self._risk.get("max_margin_per_position", 0.10)

        # Verifier micro vs full-size
        if not spec.get("is_micro") and total_capital < max_micro_only:
            return False, (
                f"REFUSE: {symbol} est un full-size contract. "
                f"Capital ${total_capital:,.0f} < ${max_micro_only:,.0f} — "
                f"utilisez le micro equivalent."
            )

        # Verifier marge initiale vs capital
        margin_initial = spec.get("margin_initial", 0)
        max_margin = total_capital * max_margin_pct
        if margin_initial > max_margin:
            return False, (
                f"REFUSE: marge initiale ${margin_initial:,.0f} pour {symbol} "
                f"depasse {max_margin_pct:.0%} du capital (${max_margin:,.0f})."
            )

        return True, "OK"

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _find_nearest_expiry(
        self, symbol: str, spec: dict, ref_date: date
    ) -> tuple[date, str]:
        """Trouve la prochaine date d'expiration >= ref_date.

        Pour les contrats quarterly (HMUZ), utilise le calendrier d'expiry.
        Pour les contrats mensuels (all), genere les dates d'expiry.

        Returns:
            (expiry_date, month_code)
        """
        months_str = spec.get("months", "HMUZ")

        if months_str == "all":
            month_codes = list(ALL_MONTHS)
        else:
            month_codes = list(months_str)

        # Generer les candidats sur 2 ans
        candidates = []
        for year in range(ref_date.year, ref_date.year + 2):
            for mc in month_codes:
                month_num = MONTH_CODES[mc]
                expiry = self._get_expiry_date(mc, year, spec)
                if expiry and expiry >= ref_date:
                    candidates.append((expiry, mc))

        if not candidates:
            raise ValueError(
                f"Aucune date d'expiration trouvee pour {symbol} apres {ref_date}"
            )

        candidates.sort(key=lambda x: x[0])
        return candidates[0]

    def _get_expiry_date(self, month_code: str, year: int, spec: dict) -> date | None:
        """Calcule la date d'expiry pour un mois/annee donnes.

        Utilise le calendrier pre-calcule si disponible,
        sinon approxime au 3eme vendredi du mois.
        """
        # Verifier le calendrier pre-calcule
        if month_code in self._expiry_calendar:
            cal_date = date.fromisoformat(self._expiry_calendar[month_code])
            if cal_date.year == year:
                return cal_date

        if month_code in self._monthly_expiry:
            cal_date = date.fromisoformat(self._monthly_expiry[month_code])
            # Les monthly expiry sont pour le contrat du mois suivant
            # Mais on les utilise directement si l'annee correspond
            if cal_date.year == year or (
                cal_date.year == year - 1 and month_code == "F"
            ):
                return cal_date

        # Fallback : 3eme vendredi du mois d'expiration
        month_num = MONTH_CODES[month_code]
        return self._third_friday(year, month_num)

    @staticmethod
    def _third_friday(year: int, month: int) -> date:
        """Retourne le 3eme vendredi d'un mois donne."""
        # Premier jour du mois
        first = date(year, month, 1)
        # Jour de la semaine (0=lundi, 4=vendredi)
        # Premier vendredi
        days_until_friday = (4 - first.weekday()) % 7
        first_friday = first + timedelta(days=days_until_friday)
        # 3eme vendredi = premier + 14 jours
        third_friday = first_friday + timedelta(days=14)
        return third_friday


class IBKRFuturesClient:
    """Client IBKR specifique aux futures — wrapper autour de IBKRBroker.

    Ajoute les methodes specifiques aux futures :
      - Resolution de contrats futures (vs Stock dans l'adapter de base)
      - Bracket orders sur futures
      - Donnees historiques futures

    Necessite une connexion IBKR active (via IBKRBroker._ib).
    """

    def __init__(self, ibkr_broker):
        """
        Args:
            ibkr_broker: instance de IBKRBroker connectee
        """
        self._broker = ibkr_broker
        self._contract_mgr = FuturesContractManager()

    def _resolve_ibkr_contract(self, symbol: str):
        """Cree et qualifie un contrat futures IBKR.

        Returns:
            ib_insync.Future contract qualifie
        """
        from ib_insync import Future

        front = self._contract_mgr.get_front_month(symbol)
        spec = self._contract_mgr.get_contract_spec(symbol)

        contract = Future(
            symbol=symbol,
            exchange=spec["exchange"],
            currency=spec["currency"],
            lastTradeDateOrContractMonth=front["expiry"].replace("-", ""),
        )

        self._broker._ensure_connected()
        self._broker._ib.qualifyContracts(contract)
        return contract

    def create_futures_position(
        self,
        symbol: str,
        direction: str,
        qty: int,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        _authorized_by: str | None = None,
    ) -> dict:
        """Ouvre une position futures via IBKR.

        Args:
            symbol: symbole futures micro (MES, MNQ, MCL, MGC)
            direction: "BUY" ou "SELL"
            qty: nombre de contrats (entier obligatoire)
            stop_loss: prix de stop loss
            take_profit: prix de take profit
            _authorized_by: identifiant du pipeline (obligatoire)

        Returns:
            {orderId, symbol, side, status, qty, stop_loss, take_profit,
             bracket, paper, authorized_by, contract_type}
        """
        from core.broker.base import BrokerError

        if _authorized_by is None:
            raise BrokerError(
                f"Ordre futures REFUSE pour {symbol}: _authorized_by manquant."
            )

        if not self._broker.is_paper:
            raise BrokerError("Futures LIVE trading bloque. IBKR_PAPER=true requis.")

        if not isinstance(qty, int) or qty <= 0:
            raise BrokerError(
                f"Futures: qty doit etre un entier positif, recu: {qty}"
            )

        from ib_insync import LimitOrder, MarketOrder, StopOrder

        contract = self._resolve_ibkr_contract(symbol)
        action = "BUY" if direction.upper() == "BUY" else "SELL"

        # Ordre principal
        parent_order = MarketOrder(action, qty)
        parent_order.transmit = not (stop_loss or take_profit)

        self._broker._ensure_connected()
        trade = self._broker._ib.placeOrder(contract, parent_order)
        self._broker._ib.sleep(1)

        # Bracket SL
        if stop_loss and stop_loss > 0:
            sl_action = "SELL" if action == "BUY" else "BUY"
            sl_order = StopOrder(sl_action, qty, round(stop_loss, 2))
            sl_order.parentId = parent_order.orderId
            sl_order.transmit = take_profit is None
            self._broker._ib.placeOrder(contract, sl_order)

        # Bracket TP
        if take_profit and take_profit > 0:
            tp_action = "SELL" if action == "BUY" else "BUY"
            tp_order = LimitOrder(tp_action, qty, round(take_profit, 2))
            tp_order.parentId = parent_order.orderId
            tp_order.transmit = True
            self._broker._ib.placeOrder(contract, tp_order)

        bracket_info = ""
        if stop_loss:
            bracket_info += f" SL=${stop_loss:.2f}"
        if take_profit:
            bracket_info += f" TP=${take_profit:.2f}"

        logger.info(
            f"IBKR FUTURES ordre soumis: {direction} {symbol} x{qty}{bracket_info} "
            f"— orderId={parent_order.orderId}"
        )

        fill = trade.orderStatus
        return {
            "orderId": str(parent_order.orderId),
            "symbol": symbol,
            "side": action,
            "status": fill.status if fill else "Submitted",
            "qty": qty,
            "filled_qty": float(fill.filled) if fill else 0,
            "filled_price": float(fill.avgFillPrice)
                if fill and fill.avgFillPrice else None,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "bracket": bool(stop_loss or take_profit),
            "paper": self._broker.is_paper,
            "authorized_by": _authorized_by,
            "contract_type": "futures",
        }

    def close_futures_position(
        self, symbol: str, _authorized_by: str | None = None
    ) -> dict:
        """Ferme une position futures."""
        from core.broker.base import BrokerError

        if _authorized_by is None:
            raise BrokerError(
                f"close_futures_position({symbol}) sans _authorized_by."
            )

        self._broker._ensure_connected()
        from ib_insync import MarketOrder

        # Trouver la position futures
        positions = self._broker._ib.positions()
        pos = next(
            (p for p in positions if p.contract.symbol == symbol),
            None,
        )
        if not pos:
            raise BrokerError(f"IBKR: pas de position futures ouverte sur {symbol}")

        contract = self._resolve_ibkr_contract(symbol)
        action = "SELL" if pos.position > 0 else "BUY"
        qty = abs(int(pos.position))
        order = MarketOrder(action, qty)
        self._broker._ib.placeOrder(contract, order)

        logger.info(f"IBKR FUTURES: position {symbol} fermee ({action} x{qty})")
        return {
            "orderId": str(order.orderId),
            "symbol": symbol,
            "status": "Submitted",
        }

    def get_futures_prices(
        self,
        symbol: str,
        timeframe: str = "1D",
        bars: int = 500,
    ) -> dict:
        """Recupere les donnees historiques futures via IBKR.

        Returns:
            {bars: [{t, o, h, l, c, v}], symbol, timeframe}
        """
        contract = self._resolve_ibkr_contract(symbol)

        tf_map = {
            "1M": "1 min", "5M": "5 mins", "15M": "15 mins",
            "30M": "30 mins", "1H": "1 hour", "4H": "4 hours",
            "1D": "1 day", "1W": "1 week",
        }
        bar_size = tf_map.get(timeframe, "1 day")

        if timeframe in ("1M", "5M", "15M", "30M"):
            duration = f"{min(bars // 60 + 5, 30)} D"
        elif timeframe in ("1H", "4H"):
            duration = f"{min(bars // 6 + 5, 365)} D"
        else:
            duration = f"{min(bars + 30, 365)} D"

        self._broker._ensure_connected()
        ibkr_bars = self._broker._ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=False,  # Futures = inclure les heures etendues
        )

        result = []
        for bar in ibkr_bars[-bars:]:
            result.append({
                "t": bar.date.isoformat() if hasattr(bar.date, "isoformat")
                     else str(bar.date),
                "o": float(bar.open),
                "h": float(bar.high),
                "l": float(bar.low),
                "c": float(bar.close),
                "v": float(bar.volume),
            })

        logger.info(f"IBKR FUTURES: {len(result)} barres {timeframe} pour {symbol}")
        return {"bars": result, "symbol": symbol, "timeframe": timeframe}
