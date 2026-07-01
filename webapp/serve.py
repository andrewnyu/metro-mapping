#!/usr/bin/env python3
"""Metro-Mapping web server.

Serves the static web app AND exposes a small build endpoint so you can add a
new city straight from the browser:

    GET /api/build?place=<name>[&synthetic=1]

streams Server-Sent Events with build progress, e.g.
    data: {"frac": 0.4, "msg": "Downloading road network…"}
    data: {"done": true, "city": {...manifest entry...}}
    data: {"error": "..."}

On success the city's GeoJSON files are written into webapp/data/ and the city
is upserted into manifest.json, so the front-end can just reload the manifest.

Run:  python3 serve.py [port]   (default 8010)
"""
from __future__ import annotations

import json
import sys
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent      # webapp/
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import export_webapp  # noqa: E402  (build/export helpers)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(HERE), **k)

    def log_message(self, *a):  # keep the console focused on build progress
        pass

    def do_GET(self):
        if self.path.startswith("/api/build"):
            return self._build()
        return super().do_GET()

    def _build(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        place = (params.get("place") or [""])[0].strip()
        synthetic = (params.get("synthetic") or ["0"])[0] in ("1", "true")
        if not place:
            self.send_error(400, "missing 'place'")
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def emit(obj):
            self.wfile.write(f"data: {json.dumps(obj)}\n\n".encode())
            self.wfile.flush()

        try:
            emit({"frac": 0.01, "msg": f"Starting {place}…"})
            city = export_webapp.export_and_register(
                place, synthetic=synthetic,
                progress=lambda f, m: emit({"frac": round(float(f), 3), "msg": m}),
            )
            print(f"[build] {place}: {city['n_land']} land cells, source={city['source']}")
            emit({"done": True, "city": city})
        except (BrokenPipeError, ConnectionResetError):
            print(f"[build] client disconnected during {place}")
        except Exception as e:  # noqa: BLE001 — report any failure to the browser
            try:
                emit({"error": str(e)})
            except Exception:
                pass
            print(f"[build] ERROR {place}: {type(e).__name__}: {e}")


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8010
    print(f"Metro-Mapping → http://localhost:{port}  (Ctrl-C to stop)")
    ThreadingHTTPServer(("", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
