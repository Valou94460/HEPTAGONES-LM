"""Serveur local de l'assistant d'installation.

Sécurité : écoute sur 127.0.0.1 uniquement, jeton aléatoire à usage de la session
(transmis dans l'adresse ouverte au lancement puis conservé en cookie ``HttpOnly``,
``SameSite=Strict``), contrôle de l'en-tête ``Host`` et en-tête obligatoire sur les
requêtes POST (protection contre les requêtes intersites). Les mots de passe saisis
ne sont conservés qu'en mémoire, le temps de l'installation.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from heptagoneslm import __version__
from heptagoneslm.setup import probes, toolscan
from heptagoneslm.setup.answers import Answers
from heptagoneslm.setup.checks import apply_fix, blocking, run_checks
from heptagoneslm.setup.installer import Installer, build_plan, schedule_label, _tasks, Context
from heptagoneslm.setup.system import SetupError, System
from heptagoneslm.setup.verify import verify

log = logging.getLogger(__name__)

STATIC = Path(__file__).resolve().parent / "static"
WEB_STATIC = Path(__file__).resolve().parent.parent / "web" / "static"
FILES = {"/setup.js": (STATIC, "setup.js"), "/setup.css": (STATIC, "setup.css"),
         "/style.css": (WEB_STATIC, "style.css"), "/favicon.svg": (WEB_STATIC, "favicon.svg"),
         "/theme.js": (WEB_STATIC, "theme.js")}
TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".svg": "image/svg+xml", ".html": "text/html; charset=utf-8"}
MAX_BODY = 256 * 1024
COOKIE = "heptagoneslm_setup"
SIM_SERVERS = [{"name": "AUTODESK", "port_at_host": "27000@licsrv-adsk", "collectors": ["lmstat"]},
               {"name": "ANSYS", "port_at_host": "1055@licsrv-ansys", "collectors": ["lmstat"]},
               {"name": "MATLAB", "port_at_host": "27000@licsrv-mlm", "collectors": ["lmstat"]}]


class SetupApp:
    def __init__(self, system: System, token: str | None = None):
        self.system = system
        self.tools_found: list[dict] = []
        self.token = token or secrets.token_urlsafe(32)
        self.answers = Answers()
        self.installer: Installer | None = None
        self.verification: dict | None = None
        self.lock = threading.Lock()
        self._info: dict | None = None

    def info(self) -> dict:
        if self._info is None:
            s = self.system
            try:
                os_info = s.os_info()
            except SetupError:
                os_info = {}
            try:
                dom = s.domain_info()
            except SetupError:
                dom = {"domain": "", "netbios": "", "dcs": [], "base_dn": ""}
            try:
                certs = s.certificates()
            except SetupError:
                certs = []
            drivers = [d for d in s.odbc_drivers() if d.startswith("ODBC Driver")]
            lmutil = s.find_lmutil()
            domain, netbios = dom.get("domain") or "", dom.get("netbios") or os_info.get("hostname") or ""
            defaults = {
                "install_dir": r"C:\HeptagonesLM",
                "service_account": f"{netbios}\\svc_heptagoneslm" if netbios else "",
                "db_driver": "ODBC Driver 18 for SQL Server" if "ODBC Driver 18 for SQL Server" in drivers
                else (drivers[-1] if drivers else "ODBC Driver 18 for SQL Server"),
                "lmutil_path": lmutil[0] if lmutil else "",
                "public_host": f"heptagoneslm.{domain}" if domain else "",
                "access_group": f"{netbios}\\GG_HeptagonesLM_Lecteurs" if netbios else "",
                "ldap_servers": [f"ldaps://{dc}" for dc in (dom.get("dcs") or [])[:2]],
                "base_dn": dom.get("base_dn") or "",
                "upn_suffix": domain,
                "sender": f"HeptagonesLM <heptagoneslm@{domain}>" if domain else "",
                "servers": SIM_SERVERS if s.simulated else [{"name": "", "port_at_host": "", "collectors": ["lmstat"]}],
            }
            if s.simulated:
                defaults.update(db_server="SQLSRV01", recipients=["dsi@entreprise.local"], smtp_host="smtp.entreprise.local")
            if certs:
                match = [c for c in certs if defaults["public_host"] and defaults["public_host"] in (c.get("dns") or "")]
                defaults["iis_cert_thumbprint"] = (match or certs)[0]["thumbprint"]
            self._info = {"version": __version__, "simulated": s.simulated, "system": s.name,
                          "admin": s.is_admin(), "os": os_info, "domain": dom, "certificates": certs,
                          "odbc_drivers": drivers, "lmutil_found": lmutil, "defaults": defaults}
        return self._info

    def plan(self, a: Answers) -> list[dict]:
        ctx = Context(self.system, a, lambda *x: None, lambda f: None)
        tasks = [{"name": t.name, "schedule": schedule_label(t)} for t in _tasks(ctx)]
        return {"steps": [s.label for s in build_plan(a)], "tasks": tasks}


class SetupHandler(BaseHTTPRequestHandler):
    app: SetupApp
    server_version = "HeptagonesLM-Setup"
    sys_version = ""

    def log_message(self, fmt, *args):
        log.debug("%s - %s", self.address_string(), fmt % args)

    # -- réponses ----------------------------------------------------------------------------
    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status: int = 200) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    # -- sécurité ---------------------------------------------------------------------------
    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        return host in ("127.0.0.1", "localhost", "::1")

    def _cookie_token(self) -> str | None:
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE:
                return value
        return None

    def _authorized(self) -> bool:
        token = self._cookie_token()
        return bool(token) and secrets.compare_digest(token, self.app.token)

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            raise ValueError("Taille de requête invalide")
        data = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("Objet JSON attendu")
        return data

    # -- GET ---------------------------------------------------------------------------------
    def do_GET(self) -> None:
        if not self._host_ok():
            return self._json({"error": "Hôte non autorisé"}, HTTPStatus.MISDIRECTED_REQUEST)
        url = urlparse(self.path)
        if url.path == "/":
            token = (parse_qs(url.query).get("token") or [""])[0]
            if token and secrets.compare_digest(token, self.app.token):
                # jeton déplacé de l'adresse vers un cookie HttpOnly, puis adresse nettoyée
                return self._send(HTTPStatus.SEE_OTHER, b"", "text/plain", {
                    "Location": "/", "Set-Cookie": f"{COOKIE}={self.app.token}; HttpOnly; SameSite=Strict; Path=/"})
            if not self._authorized():
                return self._send(HTTPStatus.FORBIDDEN, "Ouvrir l'adresse complète affichée par HeptagonesLM-Setup "
                                  "(elle contient un jeton de session).".encode(), "text/plain; charset=utf-8")
            return self._send(200, (STATIC / "setup.html").read_bytes(), TYPES[".html"])
        if url.path in FILES:
            folder, name = FILES[url.path]
            return self._send(200, (folder / name).read_bytes(), TYPES[Path(name).suffix])
        if not self._authorized():
            return self._json({"error": "Session invalide"}, HTTPStatus.FORBIDDEN)
        app = self.app
        if url.path == "/api/info":
            return self._json(app.info())
        if url.path == "/api/progress":
            since = int((parse_qs(url.query).get("since") or ["0"])[0] or 0)
            if not app.installer:
                return self._json({"status": "idle", "percent": 0, "steps": [], "logs": [], "log_count": 0})
            return self._json(app.installer.state(since))
        if url.path == "/api/report":
            text = app.installer.report_text() if app.installer else "Aucune installation.\n"
            if app.verification:
                text += "\nVérification :\n" + "\n".join(
                    f"[{c['status']:^7}] {c['category']} · {c['label']} — {c['detail']}" for c in app.verification["checks"])
            return self._send(200, text.encode("utf-8"), "text/plain; charset=utf-8",
                              {"Content-Disposition": 'attachment; filename="HeptagonesLM_rapport_installation.txt"'})
        return self._json({"error": "Introuvable"}, HTTPStatus.NOT_FOUND)

    # -- POST --------------------------------------------------------------------------------
    def do_POST(self) -> None:
        if not self._host_ok():
            return self._json({"error": "Hôte non autorisé"}, HTTPStatus.MISDIRECTED_REQUEST)
        if not self._authorized() or self.headers.get("X-HeptagonesLM-Setup") != "1":
            return self._json({"error": "Session invalide"}, HTTPStatus.FORBIDDEN)
        try:
            body = self._body()
        except ValueError as exc:
            return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        app = self.app
        path = urlparse(self.path).path
        try:
            a = Answers.from_dict(body.get("answers")) if "answers" in body else app.answers
            if "answers" in body and not (app.installer and app.installer.status == "running"):
                app.answers = a
            if path == "/api/validate":
                return self._json({"errors": a.validate(body.get("step"))})
            if path == "/api/checks":
                checks = run_checks(app.system, a, final=bool(body.get("final")))
                return self._json({"checks": [c.to_dict() for c in checks], "blocking": len(blocking(checks))})
            if path == "/api/fix":
                message = apply_fix(app.system, str(body.get("fix", "")), a)
                checks = run_checks(app.system, a, final=bool(body.get("final")))
                return self._json({"message": message, "checks": [c.to_dict() for c in checks],
                                   "blocking": len(blocking(checks))})
            if path == "/api/test":
                return self._json(self._test(a, body))
            if path == "/api/tools/find":
                hosts = toolscan.hosts_of(a.servers)
                result = app.system.find_tools(hosts)
                app.tools_found = result["found"]
                return self._json({"hosts": hosts, **result})
            if path == "/api/tools/use":
                return self._json(self._use_tool(a, str(body.get("path", ""))))
            if path == "/api/preview":
                errors = a.validate()
                return self._json({"errors": errors, "yaml": a.yaml_text() if not errors else "",
                                   "config_error": None if errors else a.check_config(),
                                   "plan": app.plan(a), "answers": a.to_dict(), "url": a.public_url})
            if path == "/api/install":
                with app.lock:
                    if body.get("resume") and app.installer and app.installer.status == "failed":
                        app.installer.start(resume=True)
                    else:
                        errors = a.validate()
                        if errors:
                            return self._json({"error": "Configuration incomplète", "errors": errors}, HTTPStatus.BAD_REQUEST)
                        checks = blocking(run_checks(app.system, a, final=True))
                        if checks:
                            return self._json({"error": "Prérequis bloquants : " + ", ".join(c.label for c in checks)},
                                              HTTPStatus.CONFLICT)
                        app.installer = Installer(app.system, a)
                        app.verification = None
                        app.installer.start()
                return self._json({"started": True})
            if path == "/api/verify":
                app.verification = verify(app.system, app.installer.a if app.installer else a)
                return self._json(app.verification)
            if path == "/api/shutdown":
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return self._json({"stopped": True})
        except SetupError as exc:
            return self._json({"error": str(exc)}, HTTPStatus.UNPROCESSABLE_ENTITY)
        except Exception:
            log.exception("Erreur de l'assistant sur %s", path)
            return self._json({"error": "Erreur interne de l'assistant (détail dans la console)"},
                              HTTPStatus.INTERNAL_SERVER_ERROR)
        return self._json({"error": "Introuvable"}, HTTPStatus.NOT_FOUND)

    def _use_tool(self, a: Answers, path: str) -> dict:
        """Retient un outil trouvé par la recherche ; copié dans <installation>\\bin s'il est distant."""
        row = next((r for r in self.app.tools_found if r["path"].lower() == path.lower()), None)
        if row is None:
            raise SetupError("Outil inconnu : relancer la recherche")
        field = f"{row['tool']}_path"
        if row["local"]:
            target = row["path"]
        else:
            target = a.path("bin", toolscan.TOOLS[row["tool"]])
            try:
                self.app.system.makedirs(a.path("bin"))
                self.app.system.copy_file(row["path"], target)
            except OSError as exc:
                raise SetupError(f"Copie impossible depuis {row['host']} : {exc.strerror or exc}") from exc
        setattr(self.app.answers, field, target)
        return {"field": field, "path": target, "copied": not row["local"], "host": row["host"]}

    def _test(self, a: Answers, body: dict) -> dict:
        kind, s = body.get("kind"), self.app.system
        if kind == "account":
            return probes.test_account(s, a)
        if kind == "database":
            return probes.test_database(s, a)
        if kind == "lmstat":
            if body.get("license_type") == "rlm":
                return probes.test_lmstat(s, a.rlmutil_path, str(body.get("port_at_host", "")), "rlm")
            return probes.test_lmstat(s, a.lmutil_path, str(body.get("port_at_host", "")))
        if kind == "certificate":
            return probes.test_certificate(s, a)
        if kind == "group":
            return probes.test_group(s, a)
        if kind == "ldap":
            return probes.test_ldap(s, a, str(body.get("username", "")), str(body.get("password", "")))
        if kind == "smtp":
            return probes.test_smtp(s, a, body.get("to"))
        if kind == "itsm":
            return probes.test_itsm(s, a)
        raise SetupError(f"Test inconnu : {kind}")


def make_server(system: System, port: int = 0, token: str | None = None) -> tuple[ThreadingHTTPServer, SetupApp]:
    app = SetupApp(system, token)
    handler = type("BoundSetupHandler", (SetupHandler,), {"app": app})
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    httpd.daemon_threads = True
    return httpd, app


def serve(system: System, port: int = 0, open_browser: bool = True, token: str | None = None) -> None:
    httpd, app = make_server(system, port, token)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/?token={app.token}"
    mode = " (SIMULATION : aucune modification du système)" if system.simulated else ""
    print(f"Assistant d'installation HeptagonesLM {__version__}{mode}")
    print(f"Adresse : {url}")
    print("Laisser cette fenêtre ouverte pendant l'installation (Ctrl+C pour quitter).")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        print("Assistant fermé.")
