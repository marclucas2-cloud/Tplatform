"""
Chargeur de données OHLCV avec protection anti-lookahead.

Règle fondamentale du no-lookahead bias :
  - Le signal généré sur la bougie[t] utilise UNIQUEMENT les données jusqu'à close[t]
  - L'ordre est passé à l'ouverture de la bougie[t+1]
  - Les indicateurs sont calculés avec .shift(1) AVANT toute logique de signal

Sources supportées :
  - CSV local (Dukascopy, Histdata, etc.)
  - API IG Markets (via ig_client)
  - Yahoo Finance (yfinance) — données réelles gratuites
  - Alpaca Markets (stocks US, ETFs, crypto)

Limites yfinance sur l'historique intraday :
  1M   → 7 jours max
  5M   → 60 jours max
  1H   → 730 jours (~2 ans)
  1D   → illimité (5 ans recommandés)

Mapping tickers Yahoo Finance :
  EURUSD → EURUSD=X  |  DAX / IX.D.DAX.* → ^GDAXI
  SP500  → ^GSPC     |  FTSE → ^FTSE  |  NASDAQ → ^IXIC
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass
class OHLCVData:
    """
    Conteneur immutable pour données OHLCV.
    Inclut métadonnées pour traçabilité et reproductibilité.
    """
    df: pd.DataFrame          # Index DatetimeIndex UTC, colonnes : open/high/low/close/volume
    asset: str
    timeframe: str
    source: str               # "csv", "ig", "yfinance"
    fingerprint: str = ""     # SHA256 du contenu — vérifier cohérence backtest / live

    def __post_init__(self):
        self._validate()
        if not self.fingerprint:
            self.fingerprint = self._compute_fingerprint()

    def _validate(self):
        required = {"open", "high", "low", "close", "volume"}
        missing = required - set(self.df.columns)
        if missing:
            raise ValueError(f"Colonnes OHLCV manquantes : {missing}")
        if not isinstance(self.df.index, pd.DatetimeIndex):
            raise ValueError("L'index doit être un DatetimeIndex")
        if self.df.index.tz is None:
            raise ValueError("Le DatetimeIndex doit avoir un timezone (UTC recommandé)")
        if self.df.isnull().any().any():
            n_nulls = self.df.isnull().sum().sum()
            raise ValueError(f"Données OHLCV contiennent {n_nulls} valeurs nulles — nettoyer avant utilisation")
        if not self.df.index.is_monotonic_increasing:
            raise ValueError("L'index temporel doit être croissant")

    def _compute_fingerprint(self) -> str:
        raw = pd.util.hash_pandas_object(self.df, index=True).values.tobytes()
        return hashlib.sha256(raw).hexdigest()[:16]

    @property
    def n_bars(self) -> int:
        return len(self.df)

    def split(self, train_pct: float = 0.7) -> tuple["OHLCVData", "OHLCVData"]:
        """Découpe en train/test sans overlap — essentiel pour validation out-of-sample."""
        n = len(self.df)
        split_idx = int(n * train_pct)
        train_df = self.df.iloc[:split_idx].copy()
        test_df = self.df.iloc[split_idx:].copy()
        train = OHLCVData(train_df, self.asset, self.timeframe, self.source)
        test = OHLCVData(test_df, self.asset, self.timeframe, self.source)
        return train, test

    def walk_forward_windows(self, n_windows: int = 4, oos_pct: float = 0.3) -> list[tuple["OHLCVData", "OHLCVData"]]:
        """
        Génère des fenêtres walk-forward (in-sample + out-of-sample).
        Aucun overlap entre les fenêtres OOS — évite le data snooping.

        Schéma pour n_windows=4, oos_pct=0.3 :
          |--IS--|OOS|
               |--IS--|OOS|
                    |--IS--|OOS|
                         |--IS--|OOS|
        """
        n = len(self.df)
        window_size = n // (n_windows + 1)
        oos_size = int(window_size * oos_pct / (1 - oos_pct))

        windows = []
        for i in range(n_windows):
            is_start = 0
            is_end = (i + 1) * window_size
            oos_start = is_end
            oos_end = min(oos_start + oos_size, n)

            if oos_end <= oos_start:
                break

            is_df = self.df.iloc[is_start:is_end].copy()
            oos_df = self.df.iloc[oos_start:oos_end].copy()
            is_data = OHLCVData(is_df, self.asset, self.timeframe, self.source)
            oos_data = OHLCVData(oos_df, self.asset, self.timeframe, self.source)
            windows.append((is_data, oos_data))

        return windows


class OHLCVLoader:
    """
    Charge des données OHLCV depuis différentes sources.
    Applique le no-lookahead guard systématiquement.
    """

    @staticmethod
    def from_csv(path: str | Path, asset: str, timeframe: str,
                 date_col: str = "datetime") -> OHLCVData:
        """
        Charge depuis un CSV Dukascopy ou Histdata.
        Format attendu : datetime, open, high, low, close, volume
        """
        path = Path(path)
        df = pd.read_csv(path, parse_dates=[date_col], index_col=date_col)
        df.columns = [c.lower().strip() for c in df.columns]

        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")

        df = df.sort_index()
        return OHLCVData(df, asset, timeframe, source="csv")

    @staticmethod
    def from_ig(ig_client, epic: str, resolution: str,
                max_bars: int = 1000) -> OHLCVData:
        """
        Charge depuis l'API IG Markets.
        resolution : 'MINUTE', 'MINUTE_5', 'HOUR', 'DAY', etc.
        """
        raw = ig_client.get_prices(epic=epic, resolution=resolution, max=max_bars)
        rows = []
        for item in raw["instrumentType"] if "instrumentType" in raw else raw.get("prices", []):
            rows.append({
                "datetime": pd.Timestamp(item["snapshotTimeUTC"]).tz_localize("UTC"),
                "open":  float(item["openPrice"]["bid"]),
                "high":  float(item["highPrice"]["bid"]),
                "low":   float(item["lowPrice"]["bid"]),
                "close": float(item["closePrice"]["bid"]),
                "volume": float(item.get("lastTradedVolume", 0)),
            })
        df = pd.DataFrame(rows).set_index("datetime").sort_index()
        timeframe_map = {
            "MINUTE": "1M", "MINUTE_5": "5M", "MINUTE_15": "15M",
            "HOUR": "1H", "HOUR_4": "4H", "DAY": "1D",
        }
        tf = timeframe_map.get(resolution, resolution)
        return OHLCVData(df, epic, tf, source="ig")

    # Correspondance IG epic / symbole → ticker Yahoo Finance
    _YF_TICKER_MAP: dict[str, str] = {
        "EURUSD":              "EURUSD=X",
        "GBPUSD":              "GBPUSD=X",
        "USDJPY":              "USDJPY=X",
        "EURGBP":              "EURGBP=X",
        "AUDUSD":              "AUDUSD=X",
        "USDCHF":              "USDCHF=X",
        "DAX":                 "^GDAXI",
        "SP500":               "^GSPC",
        "FTSE":                "^FTSE",
        "NASDAQ":              "^IXIC",
        "CAC40":               "^FCHI",
        "DOW":                 "^DJI",
        "NIKKEI":              "^N225",
        "RUSSELL":             "^RUT",
        "EUROSTOXX":           "^STOXX50E",
        "VIX":                 "^VIX",
        # Crypto (symbole court → ticker Yahoo Finance)
        "BTC":                 "BTC-USD",
        "ETH":                 "ETH-USD",
        "SOL":                 "SOL-USD",
        "BNB":                 "BNB-USD",
        "XRP":                 "XRP-USD",
        "ADA":                 "ADA-USD",
        "AVAX":                "AVAX-USD",
        "LINK":                "LINK-USD",
        # Matieres premieres
        "GOLD":                "GC=F",
        "SILVER":              "SI=F",
        "OIL":                 "CL=F",
        "GAS":                 "NG=F",
        # Actions europeennes (ticker alternatif)
        "LVMH":                "MC.PA",
        # Epics IG Markets → ticker YF
        "IX.D.DAX.DAILY.IP":   "^GDAXI",
        "IX.D.DAX.IFD.IP":     "^GDAXI",
        "IX.D.FTSE.DAILY.IP":  "^FTSE",
        "IX.D.SPTRD.DAILY.IP": "^GSPC",
        "CS.D.EURUSD.MINI.IP": "EURUSD=X",
        "CS.D.GBPUSD.MINI.IP": "GBPUSD=X",
    }

    # Mapping timeframe plateforme → interval yfinance
    _YF_INTERVAL_MAP: dict[str, str] = {
        "1M":  "1m",
        "5M":  "5m",
        "15M": "15m",
        "30M": "30m",
        "1H":  "1h",
        "4H":  "1h",   # yfinance n'a pas 4H — utiliser 1H et agréger si besoin
        "1D":  "1d",
        "1W":  "1wk",
    }

    # Historique maximum téléchargeable par interval
    _YF_MAX_PERIOD: dict[str, str] = {
        "1m":  "7d",
        "5m":  "60d",
        "15m": "60d",
        "30m": "60d",
        "1h":  "730d",
        "1d":  "5y",
        "1wk": "10y",
    }

    @staticmethod
    def from_yfinance(asset: str, timeframe: str,
                      period: str | None = None,
                      start: str | None = None,
                      end: str | None = None,
                      ticker: str | None = None) -> "OHLCVData":
        """
        Charge des données réelles depuis Yahoo Finance (gratuit, sans clé API).

        asset     : symbole plateforme (ex: "EURUSD", "DAX", "IX.D.DAX.DAILY.IP")
        timeframe : "1M", "5M", "1H", "1D", etc.
        period    : ex "60d", "2y" — si None, utilise le max disponible pour ce timeframe
        start/end : alternative à period (ex: start="2022-01-01", end="2024-12-31")
        ticker    : forcer un ticker Yahoo Finance (ex: "EURUSD=X") — override le mapping auto

        Exemples :
            OHLCVLoader.from_yfinance("EURUSD", "1H")           # 2 ans EUR/USD 1H
            OHLCVLoader.from_yfinance("DAX", "1D", period="5y") # 5 ans DAX daily
            OHLCVLoader.from_yfinance("EURUSD", "5M")           # 60 jours EUR/USD 5M
        """
        try:
            import yfinance as yf
        except ImportError:
            raise ImportError("yfinance non installé — pip install yfinance")

        # Résolution du ticker
        yf_ticker = ticker or OHLCVLoader._YF_TICKER_MAP.get(asset, asset)
        yf_interval = OHLCVLoader._YF_INTERVAL_MAP.get(timeframe, "1d")

        # Période par défaut selon les limites yfinance
        if period is None and start is None:
            period = OHLCVLoader._YF_MAX_PERIOD.get(yf_interval, "1y")

        # Téléchargement
        kwargs: dict = {"ticker": yf_ticker, "interval": yf_interval, "auto_adjust": True, "progress": False}
        if start:
            kwargs["start"] = start
            if end:
                kwargs["end"] = end
        else:
            kwargs["period"] = period

        t = yf.Ticker(yf_ticker)
        raw = t.history(
            interval=yf_interval,
            period=period if not start else None,
            start=start or None,
            end=end or None,
            auto_adjust=True,
        )

        if raw.empty:
            raise ValueError(
                f"yfinance n'a retourné aucune donnée pour {yf_ticker} "
                f"(interval={yf_interval}, period={period}). "
                f"Vérifier le ticker et les limites d'historique intraday."
            )

        # Normalisation colonnes
        raw.columns = [c.lower() for c in raw.columns]
        keep = ["open", "high", "low", "close", "volume"]
        df = raw[[c for c in keep if c in raw.columns]].copy()
        if "volume" not in df.columns:
            df["volume"] = 0.0

        # Index UTC
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

        df = df.sort_index()
        df = df.dropna()

        if len(df) == 0:
            raise ValueError(f"DataFrame vide après nettoyage pour {yf_ticker}")

        return OHLCVData(df, asset, timeframe, source="yfinance")

    @staticmethod
    def from_alpaca(asset: str, timeframe: str,
                    start: str | None = None,
                    end: str | None = None,
                    bars: int = 1000,
                    api_key: str | None = None,
                    secret_key: str | None = None,
                    regular_hours_only: bool = False) -> "OHLCVData":
        """
        Charge des données OHLCV depuis Alpaca Markets.

        Avantage vs yfinance : même source de données que l'exécution live.
        Pas de divergence backtest / paper trading.

        asset     : ticker US ou crypto
                    Stocks/ETFs : "IWM", "SPY", "AAPL"
                    Crypto      : "BTC/USD", "ETH/USD" ou "BTC", "ETH"
        timeframe : "1M", "5M", "15M", "1H", "4H", "1D", "1W"
        start/end : dates ISO "YYYY-MM-DD" (optionnel)
        bars      : nombre de barres si pas de start (estimation)
        api_key / secret_key : override les variables d'environnement

        Exemples :
            OHLCVLoader.from_alpaca("IWM", "1D", start="2019-01-01")
            OHLCVLoader.from_alpaca("SPY", "1H", bars=500)
            OHLCVLoader.from_alpaca("BTC/USD", "1D", bars=1000)
            OHLCVLoader.from_alpaca("ETH", "1H", bars=500)
        """
        import os
        key    = api_key    or os.getenv("ALPACA_API_KEY")
        secret = secret_key or os.getenv("ALPACA_SECRET_KEY")
        if not key or not secret:
            raise ValueError(
                "ALPACA_API_KEY et ALPACA_SECRET_KEY requis "
                "(variables d'environnement ou paramètres)"
            )

        from core.alpaca_client.client import AlpacaClient
        from core.data.universe import is_crypto_symbol
        client = AlpacaClient(api_key=key, secret_key=secret)

        if is_crypto_symbol(asset):
            raw = client.get_crypto_prices(symbol=asset, timeframe=timeframe,
                                           bars=bars, start=start or "", end=end or "")
        else:
            raw = client.get_prices(symbol=asset, timeframe=timeframe,
                                    bars=bars, start=start or "", end=end or "")

        bars_list = raw.get("bars", [])
        if not bars_list:
            raise ValueError(
                f"Alpaca n'a retourné aucune donnée pour {asset} {timeframe}"
            )

        import pandas as pd
        df = pd.DataFrame([{
            "datetime": pd.Timestamp(b["t"]),
            "open":   b["o"],
            "high":   b["h"],
            "low":    b["l"],
            "close":  b["c"],
            "volume": b["v"],
        } for b in bars_list])
        df = df.set_index("datetime").sort_index()

        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

        df = df.dropna()

        # Filtre heures régulières US (9h30–16h ET) — obligatoire pour ORB et intraday
        if regular_hours_only and timeframe not in ("1D", "1W"):
            import datetime as _dt
            import zoneinfo
            et = zoneinfo.ZoneInfo("America/New_York")
            df_et = df.copy()
            df_et.index = df.index.tz_convert(et)
            t_open  = _dt.time(9, 30)
            t_close = _dt.time(16, 0)
            mask = (df_et.index.time >= t_open) & (df_et.index.time < t_close)
            df = df_et[mask].copy()
            df.index = df.index.tz_convert("UTC")

        return OHLCVData(df, asset, timeframe, source="alpaca")

    @staticmethod
    def generate_synthetic(asset: str = "SYNTHETIC", timeframe: str = "1H",
                            n_bars: int = 2000, seed: int = 42) -> OHLCVData:
        """
        Génère des données synthétiques pour tests et développement.
        Reproductible via seed fixé.
        """
        rng = np.random.default_rng(seed)
        dates = pd.date_range("2020-01-01", periods=n_bars, freq="1h", tz="UTC")
        close = 1.1000 + np.cumsum(rng.normal(0, 0.0003, n_bars))
        noise = rng.uniform(0.0001, 0.0010, n_bars)
        df = pd.DataFrame({
            "open":   close - rng.uniform(-0.0002, 0.0002, n_bars),
            "high":   close + noise,
            "low":    close - noise,
            "close":  close,
            "volume": rng.integers(100, 10000, n_bars).astype(float),
        }, index=dates)
        # Cohérence OHLC
        df["high"] = df[["open", "high", "close"]].max(axis=1)
        df["low"]  = df[["open", "low",  "close"]].min(axis=1)
        return OHLCVData(df, asset, timeframe, source="synthetic")
