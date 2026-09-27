"""Serveur HTTP de l'interface web (bibliothèque standard uniquement).

* ``GET /``                         interface (HTML/JS/CSS statiques, sans CDN)
* ``GET /api/meta``                 listes des filtres, période couverte, état des collectes
* ``GET /api/report?type=...``      rapport à la demande (JSON)
* ``GET /api/export?type=...&format=xlsx|csv|json``   téléchargement ; ``type=full`` = classeur complet

L'interface est en lecture seule : aucune route n'écrit en base. Par défaut le
serveur n'écoute que sur ``127.0.0.1`` (poste local) ; ``--host 0.0.0.0`` le
rend accessible sur le réseau, **sans authentification** : à réserver à un
réseau de confiance ou à placer derrière un reverse proxy authentifiant.
"""

from __future__ import annotations

import json
import logging
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from freelm import __version__
from freelm.config import AppConfig
from freelm.storage.db import Database
from freelm.web import api

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
# Types explicites : sous Windows, le registre associe parfois .js à text/plain.
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}
STATIC_FILES = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js",
                "/charts.js": "charts.js", "/style.css": "style.css", "/favicon.svg": "favicon.svg"}


class FreeLMHandler(BaseHTTPRequestHandler):
    server_version = f"FreeLM/{__version__}"
    config: AppConfig  # injecté par make_server

    # -- utilitaires -----------------------------------------------------

    def log_message(self, fmt: str, *args) -> None:  # journalisation via logging
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", {"Cache-Control": "no-store"})

    def _db(self) -> Database:
        return Database(self.config.database.connection_string)

    # -- routes ------------------------------------------------------------

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        url = urlparse(self.path)
        try:
            if url.path in STATIC_FILES:
                return self._static(STATIC_FILES[url.path])
            query = parse_qs(url.query, keep_blank_values=True)
            if url.path == "/api/meta":
                with self._db() as db:
                    return self._json({**api.meta(db), "version": __version__,
                                       "reports": {k: v[0] for k, v in api.REPORTS.items()}})
            if url.path == "/api/report":
                params = api.parse_params(query, business_hours=self.config.reporting.business_hours)
                with self._db() as db:
                    return self._json(api.run_report(db, (query.get("type") or ["summary"])[0], params))
            if url.path == "/api/export":
                params = api.parse_params(query, business_hours=self.config.reporting.business_hours)
                fmt = (query.get("format") or ["xlsx"])[0]
                with self._db() as db:
                    content, filename, ctype = api.export(db, (query.get("type") or ["summary"])[0], params, fmt)
                return self._send(200, content, ctype, {
                    "Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}",
                    "Cache-Control": "no-store",
                })
            self._json({"error": "Introuvable"}, HTTPStatus.NOT_FOUND)
        except api.ParamError as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except ImportError as exc:
            self._json({"error": f"Module manquant : {exc.name} (pip install {exc.name})"},
                       HTTPStatus.INTERNAL_SERVER_ERROR)
        except Exception as exc:  # pragma: no cover - diagnostic
            log.exception("Erreur sur %s", self.path)
            self._json({"error": f"Erreur interne : {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _static(self, name: str) -> None:
        path = STATIC_DIR / name
        if not path.is_file():
            return self._json({"error": "Introuvable"}, HTTPStatus.NOT_FOUND)
        ctype = CONTENT_TYPES.get(path.suffix, "application/octet-stream")
        self._send(200, path.read_bytes(), ctype, {"Cache-Control": "no-cache"})


def make_server(config: AppConfig, host: str = "127.0.0.1", port: int = 8765, port_attempts: int = 10) -> ThreadingHTTPServer:
    """Crée le serveur ; si le port est occupé, essaie les suivants."""
    handler = type("BoundFreeLMHandler", (FreeLMHandler,), {"config": config})
    last_error: OSError | None = None
    for candidate in range(port, port + max(1, port_attempts)):
        try:
            httpd = ThreadingHTTPServer((host, candidate), handler)
            httpd.daemon_threads = True
            return httpd
        except OSError as exc:
            last_error = exc
            if port == 0:
                break
    raise OSError(f"Impossible d'ouvrir le port {port} (ni les suivants) : {last_error}")


def serve(config: AppConfig, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> None:
    httpd = make_server(config, host, port)
    real_port = httpd.server_address[1]
    shown_host = "localhost" if host in ("127.0.0.1", "0.0.0.0", "::") else host
    url = f"http://{shown_host}:{real_port}/"
    print(f"Interface FreeLM : {url}  (Ctrl+C pour arrêter)")
    if host not in ("127.0.0.1", "localhost", "::1"):
        log.warning("Interface accessible depuis le réseau (%s) sans authentification", host)
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt de l'interface.")
    finally:
        httpd.server_close()
