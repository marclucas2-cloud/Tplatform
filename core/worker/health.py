"""Health check HTTP endpoint for external monitoring."""
import json
import logging
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

logger = logging.getLogger("worker")


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            health = {
                "status": "ok",
                "timestamp": datetime.now(UTC).isoformat(),
                "worker": "running",
            }
            self.wfile.write(json.dumps(health).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def start_health_server(port=8080):
    """Start a minimal HTTP health server for external monitoring."""
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(f"Health server started on port {port}")
