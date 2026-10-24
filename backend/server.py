"""Serve the frontend and its JSON API with the standard library.

Run from the repository root: python -m backend.server [--port 8000]
"""
import argparse
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import api

FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        if not url.path.startswith("/api/"):
            return super().do_GET()
        query = {k: v[-1] for k, v in parse_qs(url.query).items()}
        try:
            if url.path == "/api/recommendations":
                body = api.recommendations(**{k: query[k] for k in ("goal", "n", "along", "spacing", "budget")
                                              if k in query})
            elif url.path == "/api/summary":
                body = api.data_summary()
            elif url.path == "/api/stations":
                body = api.existing_stations()
            elif url.path == "/api/tracts":
                body = api.tract_density()
            elif url.path == "/api/lines":
                body = api.existing_lines()
            elif url.path == "/api/deserts":
                body = api.transit_deserts()
            elif url.path == "/api/clusters":
                body = api.tract_clusters()
            else:
                return self._json(404, {"error": "not found"})
        except ValueError as error:
            return self._json(400, {"error": str(error)})
        except Exception as error:  # e.g. missing data files or dependencies
            return self._json(500, {"error": f"{type(error).__name__}: {error}"})
        self._json(200, body)

    def _json(self, status, body):
        payload = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), partial(Handler, directory=str(FRONTEND_DIR)))
    print(f"Serving http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
