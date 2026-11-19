"""Tiny web server for the Soundtrack AI frontend (standard library only).

    python -m src.server                # http://localhost:8000
    python -m src.server --port 9000 --llm offline

Routes:
    GET  /                 -> web/index.html
    GET  /web/<file>       -> static assets
    GET  /api/status       -> which LLM is active + catalog size
    GET  /api/catalog      -> the song catalog
    POST /api/playlist     -> {"request": str, "discovery_level"?: str, "playlist_length"?: int}
"""

from __future__ import annotations

import argparse
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .dj_agent import PROJECT_ROOT, AgentError, SoundtrackAgent
from .llm_client import LLMError, make_llm

WEB_ROOT = PROJECT_ROOT / "web"
MAX_BODY = 10_000


def make_handler(agent: SoundtrackAgent):
    class Handler(BaseHTTPRequestHandler):
        server_version = "SoundtrackAI/1.0"

        def log_message(self, fmt, *args):  # quieter console
            print(f"  {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload) -> None:
            self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/api/status":
                return self._json(200, {"llm": agent.llm.name, "songs": len(agent.songs),
                                        "genres": agent.genres, "moods": agent.moods})
            if path == "/api/catalog":
                return self._json(200, agent.songs)
            if path in ("/", "/index.html"):
                path = "/web/index.html"
            if path.startswith("/web/"):
                target = (WEB_ROOT / path[len("/web/"):]).resolve()
                if WEB_ROOT.resolve() in target.parents and target.is_file():
                    ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                    return self._send(200, target.read_bytes(), ctype)
            self._json(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/api/playlist":
                return self._json(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._json(413, {"error": "request too large"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                length_value = body.get("playlist_length")
                result = agent.run(
                    str(body.get("request", "")),
                    discovery_level=body.get("discovery_level") or None,
                    playlist_length=int(length_value) if length_value else None,
                )
                self._json(200, result.to_dict())
            except (AgentError, ValueError, TypeError) as error:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            except Exception as error:  # keep the server alive, report the problem
                self._json(500, {"error": f"{type(error).__name__}: {error}"})

    return Handler


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Serve the Soundtrack AI web app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--llm", default="auto", choices=("auto", "gemini", "offline"))
    args = parser.parse_args(argv)
    try:
        agent = SoundtrackAgent(make_llm(args.llm))
    except LLMError as error:
        raise SystemExit(f"Error: {error}")
    server = ThreadingHTTPServer((args.host, args.port), make_handler(agent))
    print(f"🎧 Soundtrack AI running at http://{args.host}:{args.port}  (llm: {agent.llm.name})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye 👋")


if __name__ == "__main__":
    main()
