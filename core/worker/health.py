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
                "pid": os.getpid(),
            }
            # CRO M-1: Enrich health with system metrics
            try:
                import psutil
                proc = psutil.Process()
                health["memory_mb"] = round(proc.memory_info().rss / 1024 / 1024, 1)
                health["cpu_percent"] = psutil.cpu_percent()
                health["uptime_hours"] = round(
                    (datetime.now(UTC).timestamp() - proc.create_time()) / 3600, 1
                )
            except ImportError:
                pass
            # Cycle metrics if available
            try:
                from dashboard.api.routes.cycles import get_cycles_health
                cycles = get_cycles_health()
                health["cycles"] = {
                    k: v.get("health", "?")
                    for k, v in cycles.get("cycles", {}).items()
                }
            except Exception:
                pass
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
