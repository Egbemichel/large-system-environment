"""Tiny CPU-burning API used as the workload for the autoscaling experiments.

Endpoints
  GET /health          liveness/readiness probe
  GET /work?ms=100     consumes about `ms` milliseconds of CPU time
  GET /metrics         Prometheus text format (request count, in-flight, latency sum)

Only the standard library is used so the image stays small and builds fast.
"""
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("PORT", "8080"))
MAX_MS = int(os.environ.get("MAX_MS", "2000"))

_lock = threading.Lock()
_stats = {"requests_total": 0, "in_flight": 0, "latency_sum": 0.0}


def burn_cpu(ms: int) -> int:
    """Busy loop that consumes `ms` milliseconds of CPU time.

    The loop is timed with the per-thread CPU clock, not the wall clock. With a wall
    clock, concurrent requests would share the same 100 ms of wall time and the work
    done per request would shrink as load grows, so the service would never saturate.
    Timing by CPU time keeps the cost of a request constant, which is what makes
    capacity (and therefore overload) well defined.
    """
    end = time.thread_time() + ms / 1000.0
    n = 0
    while time.thread_time() < end:
        n += 1
        _ = n * n % 7
    return n


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep logs quiet under load
        pass

    def _send(self, code: int, body: str, ctype: str = "text/plain"):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/health":
            self._send(200, "ok")
        elif url.path == "/work":
            q = parse_qs(url.query)
            try:
                ms = max(1, min(int(q.get("ms", ["100"])[0]), MAX_MS))
            except ValueError:
                self._send(400, "bad ms")
                return
            start = time.perf_counter()
            with _lock:
                _stats["in_flight"] += 1
            try:
                burn_cpu(ms)
            finally:
                with _lock:
                    _stats["in_flight"] -= 1
                    _stats["requests_total"] += 1
                    _stats["latency_sum"] += time.perf_counter() - start
            self._send(200, f"done {ms}ms from {os.environ.get('HOSTNAME', 'local')}\n")
        elif url.path == "/metrics":
            with _lock:
                s = dict(_stats)
            body = (
                "# TYPE app_requests_total counter\n"
                f"app_requests_total {s['requests_total']}\n"
                "# TYPE app_in_flight gauge\n"
                f"app_in_flight {s['in_flight']}\n"
                "# TYPE app_latency_seconds_sum counter\n"
                f"app_latency_seconds_sum {s['latency_sum']:.6f}\n"
            )
            self._send(200, body)
        else:
            self._send(404, "not found")


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"listening on :{PORT}", flush=True)
    server.serve_forever()
