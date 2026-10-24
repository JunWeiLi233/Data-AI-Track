"""Serve the frontend and its JSON API with the standard library.

Run from the repository root: python -m backend.server [--port 8000]
"""
import argparse
import json
import re
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import api, copilot, cortex_explanations

MAX_BODY = 64 * 1024
# The React app is built into frontend/dist (cd frontend && npm install && npm run build).
FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend" / "dist"


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        if not url.path.startswith("/api/"):
            return super().do_GET()
        query = {k: v[-1] for k, v in parse_qs(url.query).items()}
        try:
            explanation_route = re.fullmatch(r"/api/tracts/([^/]+)/explanation", url.path)
            if explanation_route:
                geoid = explanation_route.group(1)
                try:
                    body = cortex_explanations.get_tract_explanation(geoid)
                except cortex_explanations.ExplanationError as error:
                    return self._json(error.status, {"geoid": geoid, "priority_score": None,
                                                     "ai_explanation": None, "error": str(error)})
            elif url.path == "/api/recommendations":
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

    def do_POST(self):
        if urlparse(self.path).path != "/api/chat":
            return self._json(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length <= MAX_BODY:
                raise ValueError(f"request body must be 1 to {MAX_BODY} bytes")
            request = json.loads(self.rfile.read(length))
            if not isinstance(request, dict):
                raise ValueError("request body must be a JSON object")
            body = copilot.ask(request.get("question"), request.get("recommendations"), request.get("history"))
        except ValueError as error:
            return self._json(400, {"error": str(error)})
        except Exception as error:  # e.g. Snowflake not configured or unreachable
            return self._json(503, {"error": f"Copilot unavailable: {type(error).__name__}: {str(error)[:200]}"})
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
    if not FRONTEND_DIR.is_dir():
        print(f"{FRONTEND_DIR} is missing; build it with: cd frontend && npm install && npm run build")
    server = ThreadingHTTPServer((args.host, args.port), partial(Handler, directory=str(FRONTEND_DIR)))
    print(f"Serving http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
