"""
BinanceWebSocketManager — Real-time data streams from Binance.

Handles:
  - Mark price updates (futures)
  - Kline/candlestick streams
  - Order book depth
  - User data stream (order fills, account updates)
  - Liquidation events
  - Automatic reconnection with exponential backoff

Usage:
    ws = BinanceWebSocketManager(testnet=True)
    ws.subscribe_mark_price("BTCUSDT", callback=on_price)
    ws.subscribe_kline("BTCUSDT", "1h", callback=on_kline)
    ws.subscribe_liquidations(callback=on_liquidation)
    ws.start()
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)

WS_FUTURES_BASE = "wss://fstream.binance.com/ws"
WS_FUTURES_TESTNET = "wss://stream.binancefuture.com/ws"
WS_SPOT_BASE = "wss://stream.binance.com:9443/ws"
WS_SPOT_TESTNET = "wss://testnet.binance.vision/ws"


class ReconnectHandler:
    """Exponential backoff reconnection logic."""

    def __init__(self, max_retries: int = 10, backoff_base: float = 1, backoff_max: float = 30):
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self._retry_count = 0

    def get_delay(self) -> float:
        delay = min(self.backoff_base * (2 ** self._retry_count), self.backoff_max)
        self._retry_count += 1
        return delay

    def reset(self):
        self._retry_count = 0

    @property
    def exhausted(self) -> bool:
        return self._retry_count >= self.max_retries


class BinanceWebSocketManager:
    """Manage multiple Binance WebSocket streams."""

    def __init__(self, testnet: bool = True):
        self._testnet = testnet
        self._futures_ws_url = WS_FUTURES_TESTNET if testnet else WS_FUTURES_BASE
        self._spot_ws_url = WS_SPOT_TESTNET if testnet else WS_SPOT_BASE
        self._subscriptions: dict[str, Callable] = {}
        self._running = False
        self._thread: threading.Thread | None = None
        self._reconnect = ReconnectHandler()
        self._ws = None
        self._last_heartbeat = 0.0
        self._callbacks: dict[str, list[Callable]] = {}

    def subscribe_mark_price(self, symbol: str, callback: Callable):
        """Subscribe to mark price updates (every 1s)."""
        stream = f"{symbol.lower()}@markPrice@1s"
        self._add_callback(stream, callback)

    def subscribe_kline(self, symbol: str, interval: str, callback: Callable):
        """Subscribe to kline/candlestick updates."""
        stream = f"{symbol.lower()}@kline_{interval}"
        self._add_callback(stream, callback)

    def subscribe_depth(self, symbol: str, callback: Callable, levels: int = 5):
        """Subscribe to order book depth updates."""
        stream = f"{symbol.lower()}@depth{levels}@100ms"
        self._add_callback(stream, callback)

    def subscribe_liquidations(self, callback: Callable):
        """Subscribe to all liquidation events."""
        stream = "!forceOrder@arr"
        self._add_callback(stream, callback)

    def subscribe_agg_trade(self, symbol: str, callback: Callable):
        """Subscribe to aggregated trade stream."""
        stream = f"{symbol.lower()}@aggTrade"
        self._add_callback(stream, callback)

    def _add_callback(self, stream: str, callback: Callable):
        if stream not in self._callbacks:
            self._callbacks[stream] = []
        self._callbacks[stream].append(callback)

    def start(self):
        """Start WebSocket connections in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="binance-ws"
        )
        self._thread.start()
        logger.info(
            f"BinanceWS started ({'testnet' if self._testnet else 'live'}), "
            f"{len(self._callbacks)} streams"
        )

    def stop(self):
        """Stop all WebSocket connections."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("BinanceWS stopped")

    def _run_loop(self):
        """Main WebSocket event loop with reconnection."""
        try:
            import websockets
            import websockets.sync.client as ws_client
        except ImportError:
            logger.error("websockets package not installed — pip install websockets")
            self._running = False
            return

        streams = "/".join(self._callbacks.keys())
        url = f"{self._futures_ws_url}/{streams}" if streams else self._futures_ws_url

        while self._running:
            try:
                with ws_client.connect(url, close_timeout=5) as ws:
                    self._ws = ws
                    self._reconnect.reset()
                    self._last_heartbeat = time.time()
                    logger.info("BinanceWS connected")

                    while self._running:
                        try:
                            msg = ws.recv(timeout=35)
                        except TimeoutError:
                            # Send pong as heartbeat
                            try:
                                ws.pong()
                                self._last_heartbeat = time.time()
                            except Exception:
                                break
                            continue

                        self._last_heartbeat = time.time()
                        self._dispatch(msg)

            except Exception as e:
                if not self._running:
                    break
                if self._reconnect.exhausted:
                    logger.error(f"BinanceWS reconnect exhausted: {e}")
                    break
                delay = self._reconnect.get_delay()
                logger.warning(f"BinanceWS disconnected: {e} — reconnecting in {delay:.1f}s")
                time.sleep(delay)

        self._running = False

    def _dispatch(self, raw_msg: str):
        """Parse and dispatch a WebSocket message to callbacks."""
        try:
            data = json.loads(raw_msg)
        except json.JSONDecodeError:
            return

        # Combined stream format: {"stream": "...", "data": {...}}
        if "stream" in data:
            stream = data["stream"]
            payload = data.get("data", data)
        else:
            # Single stream format
            event_type = data.get("e", "")
            stream = self._guess_stream(event_type, data)
            payload = data

        callbacks = self._callbacks.get(stream, [])
        for cb in callbacks:
            try:
                cb(payload)
            except Exception as e:
                logger.error(f"WS callback error [{stream}]: {e}")

    def _guess_stream(self, event_type: str, data: dict) -> str:
        """Guess stream name from event data when not in combined format."""
        symbol = data.get("s", "").lower()
        if event_type == "markPriceUpdate":
            return f"{symbol}@markPrice@1s"
        elif event_type == "kline":
            interval = data.get("k", {}).get("i", "1h")
            return f"{symbol}@kline_{interval}"
        elif event_type == "forceOrder":
            return "!forceOrder@arr"
        elif event_type == "aggTrade":
            return f"{symbol}@aggTrade"
        return ""

    @property
    def is_connected(self) -> bool:
        return self._running and (time.time() - self._last_heartbeat < 60)

    @property
    def seconds_since_heartbeat(self) -> float:
        if self._last_heartbeat == 0:
            return float("inf")
        return time.time() - self._last_heartbeat
