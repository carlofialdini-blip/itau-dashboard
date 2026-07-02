#!/usr/bin/env python3
"""
server.py — Portfolio Intelligence local server

Serves dashboard.html and exposes two API endpoints:
  POST /refresh  → runs all scrapers + generator in the background
  GET  /status   → returns current refresh progress (polled by the page)

Usage:
    python3 server.py          # starts on http://localhost:8080
    python3 server.py 9000     # custom port
"""

import json
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
ROOT = Path(__file__).resolve().parent.parent

# ── Main refresh state ────────────────────────────────────────────────────────
_lock   = threading.Lock()
_status = {
    "running":     False,
    "step":        0,
    "total_steps": 5,
    "message":     "Idle",
    "started_at":  None,
    "finished_at": None,
    "error":       None,
}

SCRIPTS = [
    (ROOT / "scrapers" / "scraper.py",            "Portfolio news"),
    (ROOT / "scrapers" / "china_scraper.py",      "China news"),
    (ROOT / "scrapers" / "brazil_scraper.py",     "Brazil news"),
    (ROOT / "scrapers" / "credit_scraper.py",     "Credit news"),
    (ROOT / "core"     / "generate_dashboard.py", "Building dashboard"),
]


def _run_refresh():
    global _status
    _status.update({
        "running":     True,
        "step":        0,
        "message":     "Starting…",
        "started_at":  datetime.now().isoformat(),
        "finished_at": None,
        "error":       None,
    })

    for i, (script, label) in enumerate(SCRIPTS, start=1):
        _status["step"]    = i
        _status["message"] = f"{label} ({i}/{len(SCRIPTS)})"
        try:
            result = subprocess.run(
                [sys.executable, str(script)],
                capture_output=True, text=True, timeout=180, encoding="utf-8",
            )
            if result.returncode != 0:
                _status["error"]   = f"{script} exited with code {result.returncode}"
                _status["message"] = f"Error in {script}"
                break
        except subprocess.TimeoutExpired:
            _status["error"]   = f"{script} timed out after 180 s"
            _status["message"] = "Timed out"
            break
        except Exception as e:
            _status["error"]   = str(e)
            _status["message"] = "Unexpected error"
            break

    _status["running"]     = False
    _status["finished_at"] = datetime.now().isoformat()
    if not _status["error"]:
        _status["message"] = "Done"




# ── HTTP handler ──────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # silence per-request logging

    # ── GET ────────────────────────────────────────────────────────────────────
    def do_GET(self):
        if self.path in ("/", "/dashboard.html"):
            self._send_file(str(ROOT / "dashboard.html"), "text/html")
        elif self.path == "/status":
            self._send_json(_status)
        elif self.path == "/favicon.ico":
            self.send_error(404)
        else:
            self.send_error(404)

    # ── POST ───────────────────────────────────────────────────────────────────
    def do_POST(self):
        if self.path == "/refresh":
            with _lock:
                if _status["running"]:
                    self._send_json({"ok": False, "message": "Already running"})
                    return
                t = threading.Thread(target=_run_refresh, daemon=True)
                t.start()
            self._send_json({"ok": True, "message": "Refresh started"})
        else:
            self.send_error(404)

    # ── Helpers ────────────────────────────────────────────────────────────────
    def _send_file(self, path, content_type):
        try:
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_error(404)

    def _send_json(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)


# ── Entry point ───────────────────────────────────────────────────────────────
def main():

    server = HTTPServer(("localhost", PORT), Handler)
    url    = f"http://localhost:{PORT}"

    print()
    print("=" * 50)
    print("  Portfolio Intelligence — local server")
    print(f"  {url}")
    print("  Press Ctrl+C to stop")
    print("=" * 50)
    print()

    try:
        webbrowser.open(url)
    except Exception:
        pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")


if __name__ == "__main__":
    main()
