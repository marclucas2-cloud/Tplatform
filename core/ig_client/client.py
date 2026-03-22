"""
Client API IG Markets.

Couvre :
  - Authentification (session + renouvellement automatique)
  - Récupération de prix historiques (OHLCV)
  - Prix en temps réel (polling — Lightstreamer en Sprint 2)
  - Création / fermeture de positions (CFD OTC)
  - Consultation du compte (solde, positions ouvertes)

Documentation IG API : https://labs.ig.com/rest-trading-api-reference

Headers obligatoires pour chaque requête authentifiée :
  X-IG-API-KEY     : clé API
  X-SECURITY-TOKEN : token de session (valide 6h)
  CST              : client session token
  Content-Type     : application/json; charset=UTF-8
  Version          : version de l'endpoint (1, 2 ou 3 selon l'endpoint)
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

# Durée de vie d'une session IG (6h en prod)
SESSION_TTL_SECONDS = 6 * 3600 - 60  # -60s de marge


class IGAuthError(Exception):
    """Erreur d'authentification IG."""
    pass


class IGAPIError(Exception):
    """Erreur API IG (status code != 2xx)."""
    def __init__(self, status_code: int, message: str, error_code: str = ""):
        super().__init__(f"IG API {status_code}: {message} (code={error_code})")
        self.status_code = status_code
        self.error_code = error_code


class IGClient:
    """
    Client HTTP synchrone pour l'API IG Markets.

    Usage :
        client = IGClient.from_env()
        client.authenticate()
        prices = client.get_prices("CS.D.EURUSD.MINI.IP", "HOUR", max=200)
        pos = client.create_position("CS.D.EURUSD.MINI.IP", "BUY", size=1)
    """

    def __init__(self, api_key: str, username: str, password: str,
                 acc_type: str = "DEMO", base_url: str = "https://demo-api.ig.com/gateway/deal"):
        self._api_key = api_key
        self._username = username
        self._password = password
        self._acc_type = acc_type  # "DEMO" ou "LIVE"
        self._base_url = base_url.rstrip("/")
        self._session_token: str | None = None
        self._cst: str | None = None
        self._acc_number: str | None = None
        self._auth_time: float = 0.0
        self._http = requests.Session()

    @classmethod
    def from_env(cls) -> "IGClient":
        """Construit le client depuis les variables d'environnement."""
        required = ["IG_API_KEY", "IG_USERNAME", "IG_PASSWORD"]
        missing = [k for k in required if not os.getenv(k)]
        if missing:
            raise IGAuthError(f"Variables d'environnement IG manquantes : {missing}")

        return cls(
            api_key=os.environ["IG_API_KEY"],
            username=os.environ["IG_USERNAME"],
            password=os.environ["IG_PASSWORD"],
            acc_type=os.getenv("IG_ACC_TYPE", "DEMO"),
            base_url=os.getenv("IG_BASE_URL", "https://demo-api.ig.com/gateway/deal"),
        )

    # ─── Authentification ────────────────────────────────────────────────────

    def authenticate(self) -> dict:
        """
        Crée une session IG et stocke les tokens.
        À appeler avant toute autre méthode.
        Retourne les infos du compte.
        """
        url = f"{self._base_url}/session"
        payload = {
            "identifier": self._username,
            "password": self._password,
            "encryptedPassword": False,
        }
        headers = self._base_headers(version="2")

        resp = self._http.post(url, json=payload, headers=headers)
        if resp.status_code not in (200, 201):
            raise IGAuthError(f"Authentification IG échouée : {resp.status_code} — {resp.text}")

        self._session_token = resp.headers.get("X-SECURITY-TOKEN")
        self._cst = resp.headers.get("CST")
        self._auth_time = time.time()

        data = resp.json()
        self._acc_number = data.get("accountType", "")

        logger.info(
            f"IG authentifié ({self._acc_type}) — "
            f"compte : {data.get('clientId', 'N/A')}"
        )
        return data

    def _ensure_authenticated(self):
        """Renouvelle la session si expirée."""
        if not self._session_token or time.time() - self._auth_time > SESSION_TTL_SECONDS:
            logger.info("Session IG expirée — renouvellement")
            self.authenticate()

    def _base_headers(self, version: str = "1") -> dict:
        """Headers de base pour toutes les requêtes."""
        headers = {
            "X-IG-API-KEY": self._api_key,
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json; charset=UTF-8",
            "Version": version,
        }
        if self._session_token:
            headers["X-SECURITY-TOKEN"] = self._session_token
        if self._cst:
            headers["CST"] = self._cst
        return headers

    def _get(self, endpoint: str, params: dict | None = None, version: str = "1") -> dict:
        self._ensure_authenticated()
        url = f"{self._base_url}/{endpoint.lstrip('/')}"
        resp = self._http.get(url, params=params, headers=self._base_headers(version))
        return self._handle_response(resp)

    def _post(self, endpoint: str, body: dict, version: str = "1") -> dict:
        self._ensure_authenticated()
        url = f"{self._base_url}/{endpoint.lstrip('/')}"
        resp = self._http.post(url, json=body, headers=self._base_headers(version))
        return self._handle_response(resp)

    def _delete(self, endpoint: str, body: dict | None = None, version: str = "1") -> dict:
        self._ensure_authenticated()
        url = f"{self._base_url}/{endpoint.lstrip('/')}"
        # IG utilise POST avec _method=DELETE pour les fermetures
        headers = self._base_headers(version)
        headers["_method"] = "DELETE"
        resp = self._http.post(url, json=body or {}, headers=headers)
        return self._handle_response(resp)

    @staticmethod
    def _handle_response(resp: requests.Response) -> dict:
        """Parse la réponse et lève IGAPIError si nécessaire."""
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text}

        if resp.status_code not in (200, 201):
            error_code = data.get("errorCode", "") if isinstance(data, dict) else ""
            raise IGAPIError(resp.status_code, resp.text[:200], error_code)

        return data

    # ─── Compte ──────────────────────────────────────────────────────────────

    def get_account_info(self) -> dict:
        """Retourne le solde et les infos du compte actif."""
        return self._get("accounts", version="1")

    def get_positions(self) -> list[dict]:
        """Retourne toutes les positions ouvertes."""
        data = self._get("positions/otc", version="2")
        return data.get("positions", [])

    # ─── Prix ────────────────────────────────────────────────────────────────

    def get_prices(self, epic: str, resolution: str = "HOUR",
                   max: int = 200, start_date: str = "", end_date: str = "") -> dict:
        """
        Récupère les prix historiques OHLCV.

        epic       : identifiant IG (ex: "CS.D.EURUSD.MINI.IP")
        resolution : MINUTE, MINUTE_5, MINUTE_15, MINUTE_30, HOUR, HOUR_2,
                     HOUR_3, HOUR_4, DAY, WEEK, MONTH
        max        : nombre max de points (1000 max par requête IG)
        """
        params = {"resolution": resolution, "max": min(max, 1000)}
        if start_date:
            params["startdate"] = start_date
        if end_date:
            params["enddate"] = end_date

        data = self._get(f"prices/{epic}", params=params, version="3")
        logger.debug(f"IG prices {epic}/{resolution}: {len(data.get('prices', []))} bougies")
        return data

    def get_current_price(self, epic: str) -> dict:
        """Retourne le bid/ask actuel pour un instrument."""
        data = self._get(f"markets/{epic}", version="1")
        snapshot = data.get("snapshot", {})
        return {
            "epic": epic,
            "bid": snapshot.get("bid"),
            "offer": snapshot.get("offer"),
            "mid": (snapshot.get("bid", 0) + snapshot.get("offer", 0)) / 2,
            "update_time": snapshot.get("updateTime"),
        }

    def search_markets(self, term: str) -> list[dict]:
        """Recherche des instruments par terme."""
        data = self._get("markets", params={"searchTerm": term}, version="1")
        return data.get("markets", [])

    # ─── Ordres ──────────────────────────────────────────────────────────────

    def create_position(self, epic: str, direction: str, size: float,
                        order_type: str = "MARKET",
                        stop_distance: float | None = None,
                        limit_distance: float | None = None,
                        currency_code: str = "EUR") -> dict:
        """
        Ouvre une position CFD OTC.

        direction    : "BUY" ou "SELL"
        size         : taille en lots (ex: 1.0)
        order_type   : "MARKET", "LIMIT", "STOP"
        stop_distance: stop loss en pips (None = pas de stop)
        limit_distance: take profit en pips (None = pas de TP)
        """
        body = {
            "epic": epic,
            "expiry": "-",
            "direction": direction.upper(),
            "size": str(size),
            "orderType": order_type,
            "timeInForce": "FILL_OR_KILL" if order_type == "MARKET" else "GOOD_TILL_CANCELLED",
            "guaranteedStop": False,
            "forceOpen": True,
            "currencyCode": currency_code,
        }
        if stop_distance is not None:
            body["stopDistance"] = str(stop_distance)
        if limit_distance is not None:
            body["limitDistance"] = str(limit_distance)

        logger.info(f"IG CREATE {direction} {epic} size={size}")
        return self._post("positions/otc", body, version="2")

    def close_position(self, deal_id: str, direction: str,
                       size: float, order_type: str = "MARKET") -> dict:
        """
        Ferme une position existante.
        direction : direction INVERSE de la position (BUY pour fermer un SELL)
        """
        body = {
            "dealId": deal_id,
            "direction": direction.upper(),
            "size": str(size),
            "orderType": order_type,
        }
        logger.info(f"IG CLOSE deal_id={deal_id}")
        return self._delete("positions/otc", body, version="1")

    def get_transaction_history(self, transaction_type: str = "ALL",
                                max_span_seconds: int = 86400) -> list[dict]:
        """Historique des transactions (pour audit)."""
        params = {"type": transaction_type, "maxSpanSeconds": max_span_seconds}
        data = self._get("history/transactions", params=params, version="2")
        return data.get("transactions", [])
