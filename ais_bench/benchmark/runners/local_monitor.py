"""Read-only HTTP service broadcasting TasksMonitor live task states.

The service mirrors ``harbor_monitor.HarborMonitorServer`` but its data
source is the in-process :class:`TasksMonitor` state, i.e. exactly what the
terminal state board renders. No extra file scanning is performed.

Endpoints::

    GET /api/health
    GET /api/tasks
    GET /api/tasks/{model}/{dataset}
"""

import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from ais_bench.benchmark.utils.logging.logger import AISLogger


class LocalMonitorServer:
    """Stdlib HTTP server exposing TasksMonitor live snapshots.

    Args:
        monitor: A :class:`TasksMonitor` instance.
        host (str): Bind address. Defaults to ``127.0.0.1``.
        port (int): Bind port. 0 disables the server.
    """

    def __init__(self, monitor, host: str = "127.0.0.1", port: int = 0) -> None:
        self.monitor = monitor
        self.host = host
        self.port = port
        self.logger = AISLogger()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> int | None:
        """Start serving in a daemon thread.

        Returns the bound port, or None when the port is 0 (server disabled)
        or the bind failed.
        """
        if not self.port:
            return None

        monitor = self.monitor

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                path = urlparse(self.path).path
                try:
                    if path == "/api/health":
                        self._json({"status": "ok"})
                    elif path == "/api/tasks":
                        self._json(
                            {
                                "tasks": monitor.get_task_states_snapshot(),
                                "work_dir": getattr(monitor, "output_path", None),
                                "updated_at": datetime.now().isoformat(),
                            }
                        )
                    elif path.startswith("/api/tasks/"):
                        self._handle_task(path)
                    else:
                        self._json({"error": "not found"}, status=404)
                except Exception:  # noqa: BLE001
                    self._json({"error": "internal error"}, status=500)

            def _handle_task(self, path: str) -> None:
                segments = [
                    segment
                    for segment in unquote(path[len("/api/tasks/") :]).split("/")
                    if segment
                ]
                if len(segments) < 2:
                    self._json({"error": "task not found"}, status=404)
                    return
                if len(segments) > 2:
                    self._json({"error": "bad path"}, status=404)
                    return
                task_name = f"{segments[0]}/{segments[1]}"
                for task in monitor.get_task_states_snapshot():
                    if task.get("task_name") == task_name:
                        self._json(task)
                        return
                self._json({"error": f"task '{task_name}' not found"}, status=404)

            def _json(self, data, status: int = 200) -> None:
                body = json.dumps(data, ensure_ascii=False, default=str).encode(
                    "utf-8"
                )
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:  # noqa: ARG002
                pass

        try:
            self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        except OSError as exc:
            self.logger.warning(
                "Failed to start local task monitor server on %s:%s: %s",
                self.host,
                self.port,
                exc,
            )
            return None
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
