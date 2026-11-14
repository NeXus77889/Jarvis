"""Servidor local mínimo: sirve el núcleo y expone /state. Solo escucha en 127.0.0.1."""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from core import visual_state

PAGE = Path(__file__).with_name("core.html")


class _Handler(BaseHTTPRequestHandler):
    server_version = "JarvisCore"

    def log_message(self, format: str, *args: object) -> None:  # silencioso
        return

    def _allowed_host(self) -> bool:
        # Evita ataques de DNS rebinding: solo se acepta localhost.
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        return host in {"127.0.0.1", "localhost"}

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if not self._allowed_host():
            self._send(403, b"Forbidden", "text/plain")
            return
        path = self.path.split("?", 1)[0]
        if path in {"/", "/index.html", "/core.html"}:
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif path == "/state":
            body = json.dumps(visual_state.snapshot()).encode("utf-8")
            self._send(200, body, "application/json")
        else:
            self._send(404, b"Not found", "text/plain")


def start(port: int = 8765, open_browser: bool = True) -> tuple[ThreadingHTTPServer, str]:
    server: ThreadingHTTPServer | None = None
    for candidate in range(port, port + 10):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), _Handler)
            break
        except OSError:
            continue
    if server is None:
        raise RuntimeError("No se encontró un puerto libre para el núcleo visual.")
    threading.Thread(target=server.serve_forever, name="jarvis-core-http", daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    if open_browser:
        webbrowser.open(url)
    return server, url
