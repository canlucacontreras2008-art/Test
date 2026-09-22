"""Serve the mobile-friendly web UI (web/) as static files.

Usage: python examples/run_web_ui.py [host] [port]

The page itself connects to the broker directly over WebSocket from the
browser - this just serves index.html/app.js/style.css so a phone (or any
browser) can load the page in the first place.
"""

import functools
import http.server
import sys
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "localhost"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8080
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(WEB_DIR))
    with http.server.ThreadingHTTPServer((host, port), handler) as httpd:
        print(f"web UI serving {WEB_DIR} on http://{host}:{port}")
        httpd.serve_forever()
