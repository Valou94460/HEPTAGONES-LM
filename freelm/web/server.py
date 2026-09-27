"""Serveur HTTP de l'interface web (bibliothèque standard uniquement).

* ``GET /``                         interface (HTML/JS/CSS statiques, sans CDN)
* ``GET /api/meta``                 listes des filtres, période couverte, état des collectes
* ``GET /api/report?type=...``      rapport à la demande (JSON)
* ``GET /api/export?type=...&format=xlsx|csv|json``   téléchargement ; ``type=full`` = classeur complet

* ``POST /api/login`` · ``POST /api/logout`` · ``GET /api/me``   authentification (mode ldap)

L'interface est en lecture seule : aucune route n'écrit en base. Par défaut le
serveur n'écoute que sur ``127.0.0.1`` (poste local). Pour un usage partagé
sur le réseau, activer l'authentification Windows/AD (``web.auth``) et HTTPS
(``web.tls_cert`` / ``web.tls_key``) : voir docs/AUTHENTIFICATION.md.
"""

from __future__ import annotations

import json
import logging
import ssl
import threading
import webbrowser
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from freelm import __version__
from freelm.config import AppConfig, ConfigError
from freelm.storage.db import Database
from freelm.web import api
from freelm.web.auth import COOKIE_NAME, Auth, AuthError, UserInfo

log = logging.getLogger(__name__)
audit = logging.getLogger("freelm.audit")
LOOPBACK = ("127.0.0.1", "localhost", "::1")

STATIC_DIR = Path(__file__).resolve().parent / "static"
# Types explicites : sous Windows, le registre associe parfois .js à text/plain.
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}
STATIC_FILES = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js",
                "/charts.js": "charts.js", "/style.css": "style.css", "/favicon.svg": "favicon.svg",
                "/login.html": "login.html", "/login.js": "login.js"}
# Accessibles sans être connecté (page de connexion et ses ressources).
PUBLIC_PATHS = {"/login.html", "/login.js", "/style.css", "/favicon.svg"}
MAX_BODY = 8192


class FreeLMHandler(BaseHTTPRequestHandler):
    server_version = f"FreeLM/{__version__}"
    config: AppConfig  # injectés par make_server
    auth: Auth
    tls: bool = False

    # -- utilitaires -----------------------------------------------------

    def log_message(self, fmt: str, *args) -> None:  # journalisation via logging
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        if self.tls:
            self.send_header("Strict-Transport-Security", "max-age=31536000")
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

    # -- authentification -----------------------------------------------------

    @property
    def client_ip(self) -> str:
        return self.client_address[0]

    def _token(self) -> str | None:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie") or "")
        except Exception:
            return None
        morsel = cookie.get(COOKIE_NAME)
        return morsel.value if morsel else None

    def current_user(self) -> UserInfo | None:
        """Utilisateur authentifié, ou None. Sans authentification : utilisateur local."""
        if not self.auth.enabled:
            return UserInfo(username="local")
        if self.auth.mode == "header":
            return self.auth.user_from_header(self.headers, self.client_ip)
        session = self.auth.sessions.get(self._token())
        return session.user if session else None

    def _cookie(self, value: str, max_age: int) -> str:
        secure = "; Secure" if self.tls else ""
        return f"{COOKIE_NAME}={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}{secure}"

    def _deny(self, path: str) -> None:
        if path.startswith("/api/"):
            return self._json({"error": "Authentification requise", "login": self.auth.mode == "ldap"},
                              HTTPStatus.UNAUTHORIZED)
        if self.auth.mode == "ldap":
            return self._send(HTTPStatus.FOUND, b"", "text/plain", {"Location": "login.html"})
        body = "Accès refusé : utilisateur non authentifié ou non autorisé.".encode("utf-8")
        return self._send(HTTPStatus.FORBIDDEN, body, "text/plain; charset=utf-8")

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise api.ParamError("Requête trop volumineuse")
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            raise api.ParamError("JSON invalide") from None

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/login":
                if self.auth.mode != "ldap":
                    return self._json({"error": "Connexion par formulaire non activée"}, HTTPStatus.NOT_FOUND)
                body = self._read_json()
                try:
                    session = self.auth.login(str(body.get("username", "")), str(body.get("password", "")),
                                              self.client_ip)
                except AuthError as exc:
                    return self._json({"error": str(exc)}, HTTPStatus.UNAUTHORIZED)
                payload = json.dumps({"ok": True, "user": session.user.username,
                                      "display_name": session.user.display_name}).encode("utf-8")
                return self._send(200, payload, "application/json; charset=utf-8", {
                    "Set-Cookie": self._cookie(session.token, int(self.auth.cfg.session_hours * 3600)),
                    "Cache-Control": "no-store",
                })
            if path == "/api/logout":
                token = self._token()
                session = self.auth.sessions.get(token)
                if session:
                    audit.info("Déconnexion : %s", session.user.username)
                self.auth.sessions.delete(token)
                return self._send(200, b'{"ok": true}', "application/json; charset=utf-8",
                                  {"Set-Cookie": self._cookie("", 0), "Cache-Control": "no-store"})
            self._json({"error": "Introuvable"}, HTTPStatus.NOT_FOUND)
        except api.ParamError as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:  # pragma: no cover - diagnostic
            log.exception("Erreur sur %s", self.path)
            self._json({"error": f"Erreur interne : {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    # -- routes ------------------------------------------------------------

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        url = urlparse(self.path)
        try:
            if url.path in PUBLIC_PATHS:
                return self._static(STATIC_FILES[url.path])
            user = self.current_user()
            if user is None:
                return self._deny(url.path)
            if url.path in STATIC_FILES:
                return self._static(STATIC_FILES[url.path])
            if url.path == "/api/me":
                return self._json({"auth": self.auth.mode, "user": user.username,
                                   "display_name": user.display_name, "department": user.department})
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
                rtype = (query.get("type") or ["summary"])[0]
                with self._db() as db:
                    content, filename, ctype = api.export(db, rtype, params, fmt)
                audit.info("Export %s (%s) par %s depuis %s — %s", rtype, fmt, user.username, self.client_ip,
                           params.describe())
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


def check_security(config: AppConfig, host: str) -> None:
    """Refuse les configurations qui exposeraient des mots de passe en clair."""
    auth = config.web.auth
    tls = bool(config.web.tls_cert)
    remote = host not in LOOPBACK
    if auth.mode == "ldap" and remote and not tls and not config.web.allow_insecure_http:
        raise ConfigError(
            "Connexion AD sur le réseau sans HTTPS : les mots de passe circuleraient en clair. "
            "Renseigner web.tls_cert / web.tls_key (ou web.allow_insecure_http: true, déconseillé)."
        )
    if auth.mode == "none" and remote:
        log.warning("Interface accessible depuis le réseau (%s) SANS authentification", host)
    if auth.mode == "header" and remote:
        log.warning("Mode header : l'en-tête %s n'est accepté que depuis %s ; "
                    "réserver l'accès direct au reverse proxy", auth.header_name, ", ".join(auth.trusted_proxies))


def _ssl_context(config: AppConfig) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(config.web.tls_cert, config.web.tls_key)
    return ctx


def make_server(config: AppConfig, host: str = "127.0.0.1", port: int = 8765, port_attempts: int = 10,
                auth: Auth | None = None) -> ThreadingHTTPServer:
    """Crée le serveur (HTTPS si un certificat est configuré) ; si le port est occupé, essaie les suivants."""
    check_security(config, host)
    tls = bool(config.web.tls_cert)
    handler = type("BoundFreeLMHandler", (FreeLMHandler,),
                   {"config": config, "auth": auth or Auth(config.web.auth), "tls": tls})
    last_error: OSError | None = None
    for candidate in range(port, port + max(1, port_attempts)):
        try:
            httpd = ThreadingHTTPServer((host, candidate), handler)
            httpd.daemon_threads = True
            if tls:
                # Poignée de main TLS différée au thread de la requête (un client lent ne bloque pas les autres).
                httpd.socket = _ssl_context(config).wrap_socket(
                    httpd.socket, server_side=True, do_handshake_on_connect=False)
            return httpd
        except OSError as exc:
            last_error = exc
            if port == 0:
                break
    raise OSError(f"Impossible d'ouvrir le port {port} (ni les suivants) : {last_error}")


def serve(config: AppConfig, host: str | None = None, port: int | None = None, open_browser: bool = False) -> None:
    host = host or config.web.host
    port = port or config.web.port
    httpd = make_server(config, host, port)
    real_port = httpd.server_address[1]
    shown_host = "localhost" if host in ("127.0.0.1", "0.0.0.0", "::") else host
    scheme = "https" if config.web.tls_cert else "http"
    url = f"{scheme}://{shown_host}:{real_port}/"
    auth_label = {"none": "sans authentification", "ldap": "connexion Windows/AD",
                  "header": "authentification unique (reverse proxy)"}[config.web.auth.mode]
    print(f"Interface FreeLM : {url}  ({auth_label} — Ctrl+C pour arrêter)")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt de l'interface.")
    finally:
        httpd.server_close()
