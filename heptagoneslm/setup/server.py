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
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PureWindowsPath
from urllib.parse import parse_qs, urlparse

from heptagoneslm import __version__
from heptagoneslm.setup import probes, recette, toolscan
from heptagoneslm.setup.answers import SECRET_FIELDS, Answers
from heptagoneslm.setup.checks import PROBES, apply_fix, blocking, run_checks
from heptagoneslm.setup.installer import AUTODESK_FIELDS, Installer, build_plan, schedule_label, _tasks, Context
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


class Job:
    """Traitement long exécuté en arrière-plan, dont l'interface suit l'avancement (vérifications…)."""

    def __init__(self, target, steps: dict[str, str] | None = None):
        self.lock = threading.Lock()
        self.status = "running"            # running | done | error
        self.started = time.monotonic()
        self.elapsed: float | None = None
        self.steps: dict[str, dict] = {}   # clé -> {label, state, seconds}
        self.order = list(steps or {})
        self.result: dict | None = None
        self.error: str | None = None
        threading.Thread(target=self._run, args=(target,), daemon=True).start()

    def step(self, key: str, label: str, state: str, seconds: float | None = None) -> None:
        with self.lock:
            if key not in self.order:
                self.order.append(key)
            self.steps[key] = {"key": key, "label": label, "state": state, "seconds": seconds}

    def _run(self, target) -> None:
        try:
            result = target(self)
            with self.lock:
                self.result, self.status = result, "done"
        except SetupError as exc:
            with self.lock:
                self.error, self.status = str(exc), "error"
        except Exception as exc:              # pragma: no cover - défaut inattendu, détaillé dans la console
            log.exception("Traitement en arrière-plan")
            with self.lock:
                self.error, self.status = f"Erreur interne de l'assistant ({type(exc).__name__})", "error"
        finally:
            self.elapsed = round(time.monotonic() - self.started, 1)

    @property
    def running(self) -> bool:
        return self.status == "running"

    def state(self) -> dict:
        with self.lock:
            out = {"status": self.status, "elapsed": self.elapsed if self.elapsed is not None
                   else round(time.monotonic() - self.started, 1),
                   "steps": [self.steps[k] for k in self.order if k in self.steps]}
            if self.result is not None:
                out.update(self.result)
            if self.error:
                out["error"] = self.error
            return out


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
        self.check_job: Job | None = None
        self.recette_job: Job | None = None
        self.autodesk_job: Job | None = None
        self.autodesk_url: str | None = None
        self.last_seen = time.monotonic()          # dernière requête du navigateur (arrêt automatique)
        self.recette_run: recette.RecetteRun | None = None
        self.recette_installed: recette.Installed | None = None

    # -- recette -------------------------------------------------------------------------------------

    def busy(self) -> bool:
        """Installation, mise à jour, vérification des prérequis, recette ou connexion Autodesk en cours."""
        jobs = [self.check_job, self.recette_job, self.autodesk_job]
        return bool(self.installer and self.installer.status == "running") or any(j and j.running for j in jobs)
    def installed_config(self) -> str | None:
        return recette.find_installed(self.system, self.answers.install_dir)

    def installed_version(self, config_path: str | None) -> str | None:
        """Version notée à l'installation ou à la dernière mise à jour (26.3.1 et suivantes), sinon « antérieure »."""
        if not config_path:
            return None
        from heptagoneslm.setup.installer import ANSWERS_FILE

        raw = self.system.read_text(str(PureWindowsPath(config_path).parent / ANSWERS_FILE))
        if not raw:
            return "antérieure à 26.3.1"
        try:
            return str(json.loads(raw).get("_version") or "") or "antérieure à 26.3.1"
        except ValueError:
            return "inconnue"

    def start_update(self, config_path: str | None = None) -> dict:
        """Mise à jour en place de l'installation existante (configuration, secrets et tâches conservés)."""
        path = config_path or self.installed_config()
        if not path:
            raise SetupError("Aucune installation de HeptagonesLM trouvée sur ce serveur")
        with self.lock:
            if self.installer and self.installer.status == "running":
                raise SetupError("Une installation est déjà en cours")
            installed = recette.load_installed(self.system, path)
            self.answers = installed.answers
            self.installer = Installer(self.system, installed.answers, update=True)
            self.verification = None
            self.installer.start()
        return {"started": True, "install_dir": installed.install_dir}

    def autodesk_context(self, config_path: str | None = None) -> dict:
        """Réponses de l'installation existante (sans secret) : base du formulaire Autodesk."""
        path = config_path or self.installed_config()
        if not path:
            raise SetupError("Aucune installation de HeptagonesLM trouvée sur ce serveur")
        installed = recette.load_installed(self.system, path)
        answers = {k: v for k, v in installed.answers.to_dict().items() if k not in SECRET_FIELDS}
        return {"answers": answers, "config_path": path}

    def start_autodesk_config(self, form: Answers, config_path: str | None = None) -> dict:
        """Licences Autodesk nommées ajoutées (ou modifiées) sur l'installation existante."""
        path = config_path or self.installed_config()
        if not path:
            raise SetupError("Aucune installation de HeptagonesLM trouvée sur ce serveur")
        with self.lock:
            if self.installer and self.installer.status == "running":
                raise SetupError("Une installation est déjà en cours")
            installed = recette.load_installed(self.system, path)
            a = installed.answers
            for field in AUTODESK_FIELDS:
                setattr(a, field, getattr(form, field))
            errors = a.validate("autodesk")
            if errors:
                raise SetupError("; ".join(e["message"] for e in errors))
            self.answers = a
            self.installer = Installer(self.system, a, mode="autodesk")
            self.verification = None
            self.installer.start()
        return {"started": True, "install_dir": installed.install_dir}

    def recette_context(self, config_path: str | None = None) -> dict:
        path = config_path or self.installed_config()
        if not path:
            return {"installed": None}
        self.recette_installed = recette.load_installed(self.system, path)
        return {"installed": self.recette_installed.summary()}

    def start_autodesk_login(self, a: Answers) -> dict:
        """Connexion Autodesk en arrière-plan : renvoie l'adresse de connexion à ouvrir dans le navigateur."""
        from heptagoneslm.autodesk_api import REPORTING_BENEFIT

        errors = a.validate("autodesk") if a.autodesk_api_on else [{"message": "API Autodesk désactivée"}]
        if errors:
            return {"status": "error", "error": "Compléter les champs avant la connexion : "
                    + "; ".join(e["message"] for e in errors)}
        url_ready = threading.Event()
        with self.lock:
            if self.autodesk_job and self.autodesk_job.running:
                return self.autodesk_state()            # connexion déjà en cours : même adresse
            self.autodesk_url = None

            def on_url(url: str) -> None:
                self.autodesk_url = url
                url_ready.set()

            def run(job: Job) -> dict:
                contexts = self.system.autodesk_login(a.autodesk_api_entry(clear=True), on_url)
                teams = [{"id": str(c.get("contextId")), "name": c.get("alias") or str(c.get("contextId")),
                          "reporting": REPORTING_BENEFIT in (c.get("reportingBenefits", [REPORTING_BENEFIT]) or [])}
                         for c in contexts]
                return {"teams": teams}

            self.autodesk_job = Job(run)
        url_ready.wait(5)
        return self.autodesk_state()

    def autodesk_state(self) -> dict:
        if not self.autodesk_job:
            return {"status": "idle"}
        return {**self.autodesk_job.state(), "url": self.autodesk_url}

    def start_recette(self, options: dict, config_path: str | None = None) -> Job:
        with self.lock:
            if self.recette_job and self.recette_job.running:
                return self.recette_job
            path = config_path or self.installed_config()
            if not path:
                raise SetupError("Aucune installation de HeptagonesLM trouvée sur ce serveur (heptagoneslm.yaml)")
            installed = recette.load_installed(self.system, path)
            opt = recette.Options.from_dict(options)

            def work(job: Job) -> dict:
                run = recette.RecetteRun(self.system, installed, opt, progress=job.step)
                self.recette_run = run
                return {"recette": run.run()}

            self.recette_job = Job(work)
            return self.recette_job

    def start_checks(self, a: Answers, final: bool) -> Job:
        """Vérification des prérequis en arrière-plan (une seule à la fois)."""
        with self.lock:
            if self.check_job and self.check_job.running:
                return self.check_job

            def work(job: Job) -> dict:
                timings: dict = {}
                checks = run_checks(self.system, a, final=final, progress=job.step, timings=timings)
                log.info("Vérification du serveur : %s", ", ".join(f"{k} {v} s" for k, v in timings.items()))
                return {"checks": [c.to_dict() for c in checks], "blocking": len(blocking(checks)),
                        "timings": timings}

            self.check_job = Job(work, PROBES)
            return self.check_job

    def info(self) -> dict:
        if self._info is None:
            s = self.system

            def safe(fn, default):
                try:
                    return fn()
                except SetupError:
                    return default

            # Lectures indépendantes en parallèle (chacune lance PowerShell sous Windows)
            with ThreadPoolExecutor(max_workers=5) as pool:
                f_os = pool.submit(safe, s.os_info, {})
                f_dom = pool.submit(safe, s.domain_info, {"domain": "", "netbios": "", "dcs": [], "base_dn": ""})
                f_certs = pool.submit(safe, s.certificates, [])
                f_drivers = pool.submit(s.odbc_drivers)
                f_lmutil = pool.submit(s.find_lmutil)
                os_info, dom, certs = f_os.result(), f_dom.result(), f_certs.result()
                drivers = [d for d in f_drivers.result() if d.startswith("ODBC Driver")]
                lmutil = f_lmutil.result()
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
            installed = recette.find_installed(s, defaults["install_dir"])
            if s.simulated:
                defaults.update(db_server="SQLSRV01", recipients=["dsi@entreprise.local"], smtp_host="smtp.entreprise.local")
            if certs:
                match = [c for c in certs if defaults["public_host"] and defaults["public_host"] in (c.get("dns") or "")]
                defaults["iis_cert_thumbprint"] = (match or certs)[0]["thumbprint"]
            self._info = {"version": __version__, "simulated": s.simulated, "system": s.name,
                          "admin": s.is_admin(), "os": os_info, "domain": dom, "certificates": certs,
                          "odbc_drivers": drivers, "lmutil_found": lmutil, "defaults": defaults,
                          "installed": installed, "installed_version": self.installed_version(installed)}
        else:   # installation faite depuis l'ouverture de l'assistant : état relu (lecture de fichier, immédiate)
            installed = recette.find_installed(self.system, self._info["defaults"]["install_dir"])
            self._info.update(installed=installed, installed_version=self.installed_version(installed))
        return self._info

    def plan(self, a: Answers) -> list[dict]:
        ctx = Context(self.system, a, lambda *x: None, lambda f: None)
        tasks = [{"name": t.name, "schedule": schedule_label(t)} for t in _tasks(ctx)]
        return {"steps": [s.label for s in build_plan(a)], "tasks": tasks}


CHECK_FR = {"ok": "OK", "warning": "avertissement", "error": "erreur", "info": "info"}


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
        if self._authorized():
            self.app.last_seen = time.monotonic()
        if url.path == "/api/ping":
            return self._json({"ok": self._authorized()})
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
        if url.path == "/api/recette/state":
            if not app.recette_job:
                return self._json({"status": "idle", "steps": []})
            st = app.recette_job.state()
            if app.recette_run and st.get("status") == "done":
                st["recette"] = app.recette_run.state()          # confirmations à jour
            return self._json(st)
        if url.path == "/api/recette/file":
            return self._recette_file((parse_qs(url.query).get("name") or [""])[0])
        if url.path == "/api/recette/preuves.zip":
            run = app.recette_run
            if not run:
                return self._json({"error": "Aucune recette"}, HTTPStatus.NOT_FOUND)
            return self._send(200, run.evidence_zip(), "application/zip",
                              {"Content-Disposition": f'attachment; filename="HeptagonesLM_recette_{run.stamp}.zip"'})
        if url.path == "/api/autodesk/state":
            return self._json(app.autodesk_state())
        if url.path == "/api/checks/state":
            if not app.check_job:
                return self._json({"status": "idle", "steps": []})
            return self._json(app.check_job.state())
        if url.path == "/api/progress":
            since = int((parse_qs(url.query).get("since") or ["0"])[0] or 0)
            if not app.installer:
                return self._json({"status": "idle", "percent": 0, "steps": [], "logs": [], "log_count": 0})
            return self._json(app.installer.state(since))
        if url.path == "/api/report":
            text = app.installer.report_text() if app.installer else "Aucune installation.\n"
            if app.verification:
                text += "\nVérification :\n" + "\n".join(
                    f"[{CHECK_FR.get(c['status'], c['status']):^13}] {c['category']} · {c['label']} — {c['detail']}"
                    for c in app.verification["checks"])
            return self._send(200, text.encode("utf-8"), "text/plain; charset=utf-8",
                              {"Content-Disposition": 'attachment; filename="HeptagonesLM_rapport_installation.txt"'})
        return self._json({"error": "Introuvable"}, HTTPStatus.NOT_FOUND)

    # -- POST --------------------------------------------------------------------------------
    def do_POST(self) -> None:
        if not self._host_ok():
            return self._json({"error": "Hôte non autorisé"}, HTTPStatus.MISDIRECTED_REQUEST)
        if not self._authorized() or self.headers.get("X-HeptagonesLM-Setup") != "1":
            return self._json({"error": "Session invalide"}, HTTPStatus.FORBIDDEN)
        self.app.last_seen = time.monotonic()
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
            if path == "/api/checks/start":
                return self._json(app.start_checks(a, bool(body.get("final"))).state())
            if path == "/api/recette/context":
                return self._json(app.recette_context(body.get("config_path") or None))
            if path == "/api/recette/start":
                return self._json(app.start_recette(body.get("options") or {}, body.get("config_path") or None).state())
            if path == "/api/recette/confirm":
                if not app.recette_run:
                    raise SetupError("Aucune recette en cours")
                value = body.get("confirmed")
                item = app.recette_run.confirm(str(body.get("id", "")), value if isinstance(value, bool) else None)
                return self._json({"item": item.to_dict(), "verdict": recette.verdict(app.recette_run.items)})
            if path == "/api/recette/pv":
                return self._json(self._recette_pv(body))
            if path == "/api/fix":
                message = apply_fix(app.system, str(body.get("fix", "")), a)
                checks = run_checks(app.system, a, final=bool(body.get("final")))
                return self._json({"message": message, "checks": [c.to_dict() for c in checks],
                                   "blocking": len(blocking(checks))})
            if path == "/api/update":
                return self._json(app.start_update(body.get("config_path") or None))
            if path == "/api/autodesk/context":
                return self._json(app.autodesk_context(body.get("config_path") or None))
            if path == "/api/autodesk/configure":
                return self._json(app.start_autodesk_config(a, body.get("config_path") or None))
            if path == "/api/autodesk/login":
                return self._json(app.start_autodesk_login(a))
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
                if app.busy():
                    return self._json({"error": "Opération en cours : attendre la fin avant de quitter l'assistant",
                                       "busy": True}, HTTPStatus.CONFLICT)
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return self._json({"stopped": True})
        except SetupError as exc:
            return self._json({"error": str(exc)}, HTTPStatus.UNPROCESSABLE_ENTITY)
        except Exception:
            log.exception("Erreur de l'assistant sur %s", path)
            return self._json({"error": "Erreur interne de l'assistant (détail dans la console)"},
                              HTTPStatus.INTERNAL_SERVER_ERROR)
        return self._json({"error": "Introuvable"}, HTTPStatus.NOT_FOUND)

    def _recette_pv(self, body: dict) -> dict:
        from heptagoneslm.setup.pv import build_pv

        run = self.app.recette_run
        if not run:
            raise SetupError("Lancer d'abord la recette")
        meta = {k: str(body.get(k) or "").strip()[:200] for k in ("client", "redacteur", "valideur")}
        meta["comment"] = str(body.get("comment") or "").strip()[:2000]
        name = f"PV_recette_HeptagonesLM_{run.stamp}.docx"
        run.save_bytes(name, build_pv(run.state(), meta))
        run.save_results()
        return {"file": name, "url": f"/api/recette/file?name={name}"}

    def _recette_file(self, name: str):
        run = self.app.recette_run
        if not run or name not in run.files or "/" in name or "\\" in name:
            return self._json({"error": "Fichier inconnu"}, HTTPStatus.NOT_FOUND)
        data = run.system.read_bytes(run.folder + "\\" + name)
        if data is None:
            return self._json({"error": "Fichier introuvable"}, HTTPStatus.NOT_FOUND)
        types = {".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                 ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                 ".json": "application/json; charset=utf-8", ".txt": "text/plain; charset=utf-8"}
        ctype = types.get(Path(name).suffix.lower(), "application/octet-stream")
        return self._send(200, data, ctype, {"Content-Disposition": f'attachment; filename="{name}"'})

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


IDLE_EXIT_S = 30 * 60      # sans onglet ouvert ni tâche en cours pendant 30 min : l'assistant s'arrête seul


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

    def idle_watch():      # fenêtre console masquée : ne pas laisser un assistant oublié tourner en arrière-plan
        while True:
            time.sleep(30)
            if time.monotonic() - app.last_seen > IDLE_EXIT_S and not app.busy():
                print("Assistant arrêté après 30 minutes sans activité.")
                httpd.shutdown()
                return
    threading.Thread(target=idle_watch, daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        print("Assistant fermé.")
