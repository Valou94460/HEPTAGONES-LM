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
import socket
import ssl
import threading
import webbrowser
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse, urlsplit

from heptagoneslm import __version__
from heptagoneslm.config import AppConfig, ConfigError
from heptagoneslm.storage.db import Database
from heptagoneslm.web import access, api
from heptagoneslm.web.auth import COOKIE_NAME, Auth, AuthError, UserInfo

log = logging.getLogger(__name__)
audit = logging.getLogger("heptagoneslm.audit")
LOOPBACK = ("127.0.0.1", "localhost", "::1")

STATIC_DIR = Path(__file__).resolve().parent / "static"
# Types explicites : sous Windows, le registre associe parfois .js à text/plain.
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}
STATIC_FILES = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js",
                "/charts.js": "charts.js", "/style.css": "style.css", "/favicon.svg": "favicon.svg",
                "/login.html": "login.html", "/login.js": "login.js", "/theme.js": "theme.js",
                "/acces": "acces.html", "/acces.js": "acces.js"}
# Accessibles sans être connecté (page de connexion et ses ressources).
PUBLIC_PATHS = {"/login.html", "/login.js", "/style.css", "/favicon.svg", "/theme.js"}
MAX_BODY = 8192


def _host_name(value: str) -> str:
    """Nom d'hôte d'un en-tête Host, sans le port (gère [::1]:8765)."""
    value = value.strip().lower()
    if value.startswith("["):
        return value[1:value.find("]")] if "]" in value else value
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def allowed_host_names(config: AppConfig, bind_host: str) -> set[str] | None:
    """Noms acceptés dans l'en-tête Host ; None = pas de contrôle (allowed_hosts: ["*"])."""
    web = config.web
    if "*" in web.allowed_hosts:
        return None
    names = {"localhost", "127.0.0.1", "::1"} | set(web.allowed_hosts)
    if web.public_url:
        names.add((urlsplit(web.public_url).hostname or "").lower())
    if bind_host not in ("0.0.0.0", "::", ""):
        names.add(bind_host.lower())
    else:  # écoute réseau : nom court, nom complet et adresses de la machine
        try:
            hostname = socket.gethostname()
            fqdn = socket.getfqdn()
            names |= {hostname.lower(), fqdn.lower()}
            names |= set(socket.gethostbyname_ex(hostname)[2])
        except OSError:
            pass
    names.discard("")
    return names


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """Serveur multithread limité en nombre de requêtes simultanées (anti-épuisement)."""

    daemon_threads = True

    def __init__(self, *args, max_connections: int = 64, **kwargs):
        self._slots = threading.BoundedSemaphore(max_connections)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            log.warning("Trop de connexions simultanées : %s refusé", client_address[0])
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class HeptagonesLMHandler(BaseHTTPRequestHandler):
    # Pas de numéro de version (ni Python) dans l'en-tête Server.
    server_version = "HeptagonesLM"
    sys_version = ""
    config: AppConfig  # injectés par make_server
    auth: Auth
    tls: bool = False
    allowed_hosts: set[str] | None = None
    timeout = 30  # délai de lecture d'une requête (s) : coupe les connexions lentes

    def version_string(self) -> str:
        return self.server_version

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
            user = self.auth.user_from_header(self.headers, self.client_ip)
            if user:
                access.remember_method(user.username, self.headers.get(access.METHOD_HEADER))
            return user
        session = self.auth.sessions.get(self._token())
        return session.user if session else None

    def _access_info(self, user: UserInfo) -> dict:
        """Ce que HeptagonesLM voit de l'utilisateur (page de test d'accès /acces)."""
        trusted = self.auth.mode == "header" and self.client_ip in self.auth.cfg.trusted_proxies
        if self.auth.mode == "header":
            method = access.last_method(user.username) or "Windows (méthode non déterminée)"
        elif self.auth.mode == "ldap":
            method = "Identifiants Windows (formulaire)"
        else:
            method = "Aucune (poste local)"
        client = access.client_address(self.headers, self.client_ip, trusted)
        display_name, department = user.display_name, user.department
        if not display_name:                 # authentification IIS : nom complet du référentiel (import-users)
            try:
                with self._db() as db:
                    rows = db.query_dicts("SELECT display_name, department FROM app_user WHERE LOWER(username) = ?",
                                          (user.username.lower(),))
                if rows:
                    display_name, department = rows[0]["display_name"], department or rows[0]["department"]
            except Exception:            # base indisponible : le test d'accès reste possible
                log.warning("Référentiel utilisateurs indisponible pour le test d'accès", exc_info=True)
        return {"user": user.username, "display_name": display_name, "department": department,
                "auth": self.auth.mode, "method": method, "client": client,
                "workstation": access.workstation_of(client),
                "browser": access.browser_of(self.headers.get("User-Agent") or "")}

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

    def _host_ok(self) -> bool:
        """Refuse les requêtes dont l'en-tête Host n'est pas un nom attendu (DNS rebinding)."""
        if self.allowed_hosts is None:
            return True
        host = _host_name(self.headers.get("Host") or "")
        if host in self.allowed_hosts:
            return True
        log.warning("Requête refusée : en-tête Host inattendu %r depuis %s", host[:100], self.client_ip)
        self._json({"error": "Nom d'hôte non autorisé (web.allowed_hosts)"}, HTTPStatus.MISDIRECTED_REQUEST)
        return False

    def _internal_error(self, exc: Exception) -> None:
        # Détail uniquement dans le journal du serveur, jamais renvoyé au client.
        log.exception("Erreur sur %s", self.path)
        self._json({"error": "Erreur interne (détail dans le journal du serveur)"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise api.ParamError("Content-Length invalide") from None
        if not 0 <= length <= MAX_BODY:
            raise api.ParamError("Taille de requête invalide")
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            raise api.ParamError("JSON invalide") from None

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not self._host_ok():
            return
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
            if path == "/api/access-test":
                # En-tête personnalisé : interdit l'envoi depuis un autre site (pré-requête CORS refusée)
                if self.headers.get("X-HeptagonesLM") != "1":
                    return self._json({"error": "Requête refusée"}, HTTPStatus.FORBIDDEN)
                user = self.current_user()
                if user is None:
                    return self._deny(path)
                body = self._read_json()
                info = self._access_info(user)
                entry = access.make_entry(info["user"], info["display_name"], info["method"], info["client"],
                                          info["workstation"], info["browser"], body.get("prompted"))
                access.record(access.results_path(self.config.log_file), entry)
                audit.info("Test d'accès : %s depuis %s (%s, %s, mot de passe demandé : %s)", entry["user"],
                           entry["workstation"] or entry["client"], entry["browser"], entry["method"],
                           {True: "oui", False: "non"}.get(entry["prompted"], "?"))
                return self._json({"ok": True, "entry": entry})
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
            self._internal_error(exc)

    # -- routes ------------------------------------------------------------

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if not self._host_ok():
            return
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
            if url.path == "/api/access-test":
                return self._json(self._access_info(user))
            query = parse_qs(url.query, keep_blank_values=True)
            if url.path == "/api/meta":
                with self._db() as db:
                    return self._json({**api.meta(db), "version": __version__,
                                       "reports": {k: v[0] for k, v in api.REPORTS.items()}})
            if url.path == "/api/report":
                params = api.params_from_config(query, self.config)
                with self._db() as db:
                    return self._json(api.run_report(db, (query.get("type") or ["summary"])[0], params))
            if url.path == "/api/export":
                params = api.params_from_config(query, self.config)
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
            self._internal_error(exc)

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
    handler = type("BoundHeptagonesLMHandler", (HeptagonesLMHandler,), {
        "config": config, "auth": auth or Auth(config.web.auth), "tls": tls,
        "allowed_hosts": allowed_host_names(config, host), "timeout": config.web.request_timeout_s,
    })
    last_error: OSError | None = None
    for candidate in range(port, port + max(1, port_attempts)):
        try:
            httpd = BoundedThreadingHTTPServer((host, candidate), handler,
                                               max_connections=config.web.max_connections)
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
    print(f"Interface HeptagonesLM : {url}  ({auth_label} — Ctrl+C pour arrêter)")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt de l'interface.")
    finally:
        httpd.server_close()
