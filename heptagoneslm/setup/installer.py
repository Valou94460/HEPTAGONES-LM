"""Plan d'installation et exécution avec progression.

Chaque étape est idempotente : une installation interrompue peut être relancée
depuis l'étape en échec (« Réessayer ») ou rejouée entièrement sans dommage.
"""

from __future__ import annotations

import copy
import json
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import PureWindowsPath
from typing import Callable

import yaml

from heptagoneslm import __version__
from heptagoneslm.setup import iis
from heptagoneslm.setup.answers import SECRET_FIELDS, Answers
from heptagoneslm.setup.system import TASK_FOLDER, SetupError, System, TaskSpec

WEB_TASK = "HeptagonesLM - Interface web"
WATCHDOG_TASK = "HeptagonesLM - Surveillance de l'interface"
SHORTCUT_NAME = "HeptagonesLM.url"
ANSWERS_FILE = "setup-reponses.json"      # réponses de l'assistant sans les secrets (recette, mise à jour)


@dataclass
class Step:
    id: str
    label: str
    weight: float
    run: Callable[["Context"], str | None]
    status: str = "pending"          # pending | running | done | failed | skipped
    message: str = ""
    started: float | None = None
    ended: float | None = None

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "status": self.status, "message": self.message,
                "duration": round(self.ended - self.started, 1) if self.started and self.ended else None}


class Context:
    def __init__(self, system: System, a: Answers, log: Callable[[str, str], None],
                 progress: Callable[[float], None]):
        self.system, self.a, self._log, self.progress = system, a, log, progress
        self.exe, self.prefix = system.runtime_command(a)
        self.env = a.machine_secrets()

    def log(self, message: str, level: str = "info") -> None:
        self._log(message, level)

    def heptagoneslm(self, *args: str, timeout: int = 300):
        cmd = [self.exe, *self.prefix, "-c", self.a.config_path, *args]
        self.log("$ " + " ".join(("HeptagonesLM.exe" if c == self.exe and not self.prefix else c) for c in cmd), "cmd")
        return self.system.run(cmd, env=self.env, timeout=timeout)

    def task_command(self, *args: str) -> tuple[str, str]:
        """Programme et ligne d'arguments d'une tâche planifiée (chemin de configuration toujours entre guillemets)."""
        parts = [*self.prefix, "-c", f'"{self.a.config_path}"', *(f'"{x}"' if " " in x else x for x in args)]
        return self.exe, " ".join(parts)


# ------------------------------------------------------------------------------ étapes


def step_prepare(ctx: Context) -> str:
    a = ctx.a
    data = a.db_mode == "sqlite" or a.autodesk_api_on
    folders = ["", "logs", "reports"] + (["data"] if data else []) + (["iis"] if a.web_mode == "iis" else [])
    if a.autodesk_enabled:
        folders.append(a.autodesk_dir)
    for i, sub in enumerate(folders):
        path = a.install_dir if not sub else sub if ":" in sub else a.path(sub)
        ctx.system.makedirs(path)
        ctx.log(f"Dossier {path}")
        ctx.progress((i + 1) / len(folders))
    return f"{len(folders)} dossiers prêts"


def step_binaries(ctx: Context) -> str:
    a, system = ctx.a, ctx.system
    source = system.runtime_source()
    if source or system.simulated:
        target = a.path("HeptagonesLM.exe")
        if system.exists(target):          # réinstallation / mise à jour : libérer l'exécutable
            stopped = system.stop_heptagoneslm(target)
            if stopped:
                ctx.log(f"HeptagonesLM en cours d'exécution arrêté ({stopped} processus) ; relancé en fin d'installation")
        system.copy_file(source or "HeptagonesLM.exe", target)
        ctx.log(f"HeptagonesLM {__version__} copié dans {target}")
        return f"HeptagonesLM.exe {__version__} installé"
    ctx.log(f"Installation Python : les tâches lanceront « {ctx.exe} -m heptagoneslm »", "warning")
    return "Environnement Python conservé (pas d'exécutable à copier)"


def step_config(ctx: Context) -> str:
    a, system = ctx.a, ctx.system
    error = a.check_config()
    if error:
        raise SetupError(f"Configuration invalide : {error}")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    previous = system.read_text(a.config_path)
    if previous is not None:
        system.write_text(a.config_path + f".{stamp}.bak", previous)
        ctx.log(f"Ancienne configuration sauvegardée : heptagoneslm.yaml.{stamp}.bak")
    text = a.yaml_text(datetime.now().strftime("%d/%m/%Y %H:%M"))
    kept: list[str] = []
    if previous is not None:
        try:
            old = yaml.safe_load(previous) or {}
        except yaml.YAMLError:
            old = {}
        if isinstance(old, dict):
            merged, kept = merge_user_settings(a.config_dict(), old)
            if kept:
                from heptagoneslm.config import ConfigError, parse_config

                try:
                    parse_config(merged)
                    text = a.header_text(datetime.now().strftime("%d/%m/%Y %H:%M")) + a.dump_config(merged)
                except ConfigError as exc:
                    ctx.log(f"Réglages personnalisés non repris (invalides : {exc}) ; voir la sauvegarde .bak", "warning")
                    kept = []
    system.write_text(a.config_path, text)
    ctx.log(f"Configuration écrite : {a.config_path}")
    if kept:
        ctx.log("Réglages personnalisés conservés : " + ", ".join(kept))
    # Réponses de l'assistant sans les secrets : reprises par la recette et les mises à jour
    saved = {k: v for k, v in a.to_dict().items() if k not in SECRET_FIELDS}
    saved["_version"], saved["_written"] = __version__, datetime.now().isoformat(timespec="seconds")
    system.write_text(a.path(ANSWERS_FILE), json.dumps(saved, ensure_ascii=False, indent=2))
    return "heptagoneslm.yaml écrit"


# Réglages saisis à la main dans heptagoneslm.yaml, que l'assistant ne gère pas : conservés quand il réécrit
# la configuration (réinstallation, changement de réponses).
KEEP_TOP = ("products", "sites", "retention")
KEEP_NESTED = (("reporting", ("business_hours", "currency", "target_coverage", "top_n_chart")),
               ("autodesk", ("annual_costs",)), ("web", ("roles", "session_hours")),
               ("email", ("subscriptions",)), ("alerts", None))


def merge_user_settings(new: dict, old: dict) -> tuple[dict, list[str]]:
    """Configuration de l'assistant complétée des réglages personnalisés de l'ancienne : (fusion, clés gardées)."""
    out, kept = copy.deepcopy(new), []
    for key in KEEP_TOP:
        if old.get(key) and old.get(key) != out.get(key):
            out[key] = copy.deepcopy(old[key])
            kept.append(key)
    for section, keys in KEEP_NESTED:
        before = old.get(section)
        if not isinstance(before, dict) or section not in out:
            continue
        target = out[section]
        for k, v in before.items():
            if keys is None and k in ("recipients", "itsm"):
                continue                 # destinataires et connecteurs : ceux de l'assistant
            if (keys is None or k in keys) and v not in (None, {}, []) and target.get(k) != v:
                if section == "email" and k == "subscriptions" and len(v) <= len(target.get(k) or []):
                    continue             # abonnement unique de l'assistant : ses destinataires priment
                target[k] = copy.deepcopy(v)
                kept.append(f"{section}.{k}")
    return out, kept


def step_backup(ctx: Context) -> str:
    """Mise à jour : copie de la configuration, des réponses, du site IIS et de la base SQLite."""
    a, system = ctx.a, ctx.system
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = a.path("backup", stamp)
    system.makedirs(folder)
    saved = []
    for rel in ("heptagoneslm.yaml", ANSWERS_FILE, "iis\\web.config"):
        text = system.read_text(a.path(*rel.split("\\")))
        if text is not None:
            system.write_text(str(PureWindowsPath(folder) / PureWindowsPath(rel).name), text)
            saved.append(PureWindowsPath(rel).name)
    if a.db_mode == "sqlite":
        db = a.path("data", "heptagoneslm.db")
        if system.exists(db):
            system.stop_task(WEB_TASK)               # base non verrouillée par l'interface pendant la copie
            system.copy_file(db, str(PureWindowsPath(folder) / "heptagoneslm.db"))
            saved.append("heptagoneslm.db")
    else:
        ctx.log("Base SQL Server : sauvegarde à faire par le DBA avant la mise à jour (le schéma n'évolue que "
                "par ajouts)", "warning")
    ctx.log(f"Sauvegarde : {folder} ({', '.join(saved) or 'rien à copier'})")
    return f"Sauvegarde dans backup\\{stamp}"


def step_secrets(ctx: Context) -> str:
    a = ctx.a
    names = list(a.machine_secrets())
    for name in names:
        ctx.system.set_machine_env(name, a.machine_secrets()[name])
        ctx.log(f"Variable d'environnement machine {name} définie (valeur masquée)")
    files = a.secret_files()
    if files:
        ctx.system.restrict_secrets_dir(a.secrets_dir, a.service_account)
        ctx.log(f"Dossier {a.secrets_dir} réservé à SYSTEM, aux administrateurs et à {a.service_account} (lecture)")
        for path, value in files.items():
            ctx.system.write_secret(path, value)
            ctx.log(f"Secret chiffré par Windows (DPAPI) : {path}")
        names += [PureWindowsPath(p).name for p in files]
    return ", ".join(names) if names else "Aucun secret à stocker"


def step_acl(ctx: Context) -> str:
    a = ctx.a
    ctx.system.grant(a.install_dir, a.service_account, "RX")
    ctx.log(f"{a.service_account} : lecture et exécution sur {a.install_dir}")
    # data : base SQLite et jeton Autodesk renouvelé à chaque synchronisation
    writable = ["logs", "reports"] + (["data"] if a.db_mode == "sqlite" or a.autodesk_api_on else [])
    for sub in writable:
        ctx.system.grant(a.path(sub), a.service_account, "M")
        ctx.log(f"{a.service_account} : modification sur {a.path(sub)}")
    if a.autodesk_enabled and not a.autodesk_dir.lower().startswith(a.install_dir.lower().rstrip("\\") + "\\"):
        ctx.system.grant(a.autodesk_dir, a.service_account, "RX")
        ctx.log(f"{a.service_account} : lecture sur {a.autodesk_dir} (exports Autodesk)")
    return "Droits accordés au compte de service"


def step_database(ctx: Context) -> str:
    a = ctx.a
    if a.db_mode == "sqlite":
        return "Base SQLite : créée à l'initialisation du schéma"
    if not a.db_create:
        ctx.log("Création non demandée : base supposée existante (script DBA 00_create_database.sql)")
        return "Base existante utilisée"
    for line in ctx.system.db_create(a):
        ctx.log(line)
    return f"Base {a.db_name} prête"


def step_schema(ctx: Context) -> str:
    r = ctx.heptagoneslm("init-db")
    for line in (r.out or "").strip().splitlines()[-5:]:
        ctx.log(line)
    if not r.ok:
        raise SetupError(f"Initialisation du schéma : {r.message}")
    from heptagoneslm.storage.schema import SCHEMA_VERSION

    return f"Schéma v{SCHEMA_VERSION} initialisé"


def step_collect(ctx: Context) -> str:
    r = ctx.heptagoneslm("collect", timeout=600)
    for line in ((r.out or "") + "\n" + (r.err or "")).strip().splitlines()[-8:]:
        ctx.log(line)
    if r.code == 2:
        ctx.log("Au moins un serveur de licences n'a pas répondu (voir ci-dessus)", "warning")
        return "Collecte partielle : un serveur au moins en échec"
    if not r.ok:
        raise SetupError(f"Première collecte : {r.message}")
    return "Première collecte réussie"


def step_iis_prereq(ctx: Context) -> str:
    system = ctx.system
    state = system.iis_state() or {}
    features = state.get("features") or {}
    names = [f[0] for f in iis.FEATURES if not features.get(f[0])]
    done = []
    if names:
        ctx.log("Installation des rôles IIS : " + ", ".join(names) + " (quelques minutes)")
        res = system.install_iis_features(names)
        done.append("rôles IIS")
        if res.get("restart"):
            ctx.log("Un redémarrage du serveur sera nécessaire", "warning")
    ctx.progress(0.5)
    modules = (system.iis_state() or {}).get("modules") or {}
    for key, mod in iis.MODULES.items():
        if not modules.get(key):
            ctx.log(f"Installation de {mod['label']}")
            system.install_module(key)
            done.append(mod["label"])
    return ("Installé : " + ", ".join(done)) if done else "Prérequis IIS déjà présents"


def step_iis_site(ctx: Context) -> str:
    a, system = ctx.a, ctx.system
    physical = a.path("iis")
    system.write_text(a.path("iis", "web.config"), iis.web_config(a))
    system.write_text(a.path("iis", "App_Code", f"{iis.MODULE_NAME}.cs"), iis.USER_MODULE_CS)
    ctx.log(f"web.config écrit (relais vers http://127.0.0.1:{a.web_port}, utilisateur transmis "
            f"par le module {iis.MODULE_NAME})")
    res = system.configure_iis_site(a, physical)
    ctx.log(f"Site « {a.iis_site} » : https://{a.public_host}:{a.iis_port} ({res.get('state', '?')})")
    if a.access_group:
        ctx.log(f"Accès limité au groupe {a.access_group}")
    return f"Site IIS {res.get('state', 'configuré')}"


def _tasks(ctx: Context) -> list[TaskSpec]:
    a = ctx.a
    specs = []

    def add(name, args, trigger, description, **kw):
        exe, arguments = ctx.task_command(*args)
        specs.append(TaskSpec(name=name, execute=exe, arguments=arguments, workdir=a.install_dir,
                              trigger=trigger, description=description, **kw))
    add("HeptagonesLM - Collecte", ["collect"], "repeat", "Sondage lmstat / lecture des logs FlexLM",
        minutes=a.collect_interval)
    add("HeptagonesLM - Rapport mensuel", ["report", "--period", "last-month", "--skip-existing"], "daily",
        "Rapport Excel du mois précédent (généré une seule fois par mois)", at="06:00")
    if a.email_enabled:
        add("HeptagonesLM - Envoi des rapports", ["send-report", "--skip-sent"], "daily",
            "Rapports mensuels par e-mail (une fois par période)", at="07:00")
    add("HeptagonesLM - Purge", ["purge"], "weekly", "Purge selon la politique de rétention", at="03:00")
    add(WEB_TASK, ["web"], "startup", "Interface web HeptagonesLM", permanent=True)
    specs.append(watchdog_spec(a))
    return specs


def watchdog_spec(a: Answers) -> TaskSpec:
    """Relance l'interface web si elle ne tourne pas : démarrage du serveur raté (domaine pas encore joignable,
    base pas prête), arrêt après les relances automatiques. Sous SYSTEM : aucun mot de passe, donc aussi
    ajoutée par la mise à jour en place. Une tâche désactivée volontairement n'est pas relancée."""
    folder = TASK_FOLDER.replace("'", "''")
    web = WEB_TASK.replace("'", "''")
    command = (f"$t = Get-ScheduledTask -TaskPath '{folder}' -TaskName '{web}' -ErrorAction SilentlyContinue; "
               "if ($t -and $t.State -ne 'Running' -and $t.State -ne 'Disabled') { Start-ScheduledTask -InputObject $t }")
    return TaskSpec(name=WATCHDOG_TASK, execute="powershell.exe",
                    arguments=f'-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "{command}"',
                    workdir=a.install_dir, trigger="repeat", minutes=5, system=True,
                    description="Relance l'interface web HeptagonesLM si elle est arrêtée")


def schedule_label(spec: TaskSpec) -> str:
    return {"repeat": f"toutes les {spec.minutes} min", "daily": f"chaque jour à {spec.at}",
            "weekly": f"chaque dimanche à {spec.at}", "startup": "au démarrage du serveur"}[spec.trigger]


def step_tasks(ctx: Context) -> str:
    specs = _tasks(ctx)
    if ctx.system.grant_batch_logon(ctx.a.service_account):
        ctx.log(f"{ctx.a.service_account} : droit « Ouvrir une session en tant que tâche » accordé")
    for i, spec in enumerate(specs):
        ctx.system.register_task(spec, ctx.a.service_account, ctx.a.service_password)
        ctx.log(f"Tâche « {spec.name} » enregistrée ({schedule_label(spec)})")
        ctx.progress((i + 1) / len(specs))
    return f"{len(specs)} tâches planifiées sous \\HeptagonesLM\\"


def firewall_rule_name(a: Answers) -> str:
    return f"HeptagonesLM (HTTPS {a.iis_port if a.web_mode == 'iis' else a.web_port})"


def step_firewall(ctx: Context) -> str:
    a = ctx.a
    port = a.iis_port if a.web_mode == "iis" else a.web_port
    ctx.system.firewall_rule(firewall_rule_name(a), port)
    ctx.log(f"Règle entrante TCP {port} (profils Domaine et Privé)")
    return f"Port {port} ouvert"


def backend_url(a: Answers) -> tuple[str, str | None]:
    scheme = "https" if a.web_mode == "https" else "http"
    return f"{scheme}://127.0.0.1:{a.web_port}/favicon.svg", (a.public_host if a.web_mode == "https" else None)


def step_start(ctx: Context) -> str:
    system = ctx.system
    system.stop_task(WEB_TASK)
    system.start_task(WEB_TASK)
    ctx.log("Tâche « HeptagonesLM - Interface web » démarrée ; attente de la réponse de l'interface")
    url, sni = backend_url(ctx.a)
    deadline = time.time() + (5 if system.simulated else 90)
    while time.time() < deadline:
        code = system.http_status(url, sni_host=sni)
        if code == 200:
            ctx.log(f"Interface opérationnelle ({url.rsplit('/', 1)[0]})")
            return "Interface démarrée"
        ctx.progress(min(0.95, 1 - (deadline - time.time()) / 90))
        time.sleep(1)
    raise SetupError("L'interface ne répond pas : consulter logs\\heptagoneslm.log (certificat, port déjà utilisé ?)")


def step_watchdog(ctx: Context) -> str:
    spec = watchdog_spec(ctx.a)
    ctx.system.register_task(spec, ctx.a.service_account, "")
    ctx.log(f"Tâche « {spec.name} » enregistrée ({schedule_label(spec)}, compte SYSTEM)")
    return "Interface relancée automatiquement si elle s'arrête"


def shortcut_text(a: Answers) -> str | None:
    url = a.public_url
    if not url:
        return None
    return (f"[InternetShortcut]\r\nURL={url}/\r\nIconFile={a.path('HeptagonesLM.exe')}\r\nIconIndex=0\r\n")


def step_shortcut(ctx: Context) -> str:
    """Icône « HeptagonesLM » sur le bureau de tous les utilisateurs et dans le menu Démarrer : ouvre le tableau de bord."""
    text = shortcut_text(ctx.a)
    if not text:
        ctx.log("Adresse publique inconnue : raccourci non créé")
        return "Raccourci non créé (adresse inconnue)"
    for folder in ctx.system.shortcut_dirs():
        path = str(PureWindowsPath(folder, SHORTCUT_NAME))
        ctx.system.write_text(path, text)
        ctx.log(f"Raccourci {path} → {ctx.a.public_url}")
    return "Icône HeptagonesLM sur le bureau"


def build_plan(a: Answers) -> list[Step]:
    steps = [
        Step("prepare", "Préparation des dossiers", 1, step_prepare),
        Step("binaries", "Copie de HeptagonesLM", 2, step_binaries),
        Step("config", "Écriture de la configuration", 1, step_config),
    ]
    if a.machine_secrets() or a.secret_files():
        steps.append(Step("secrets", "Stockage des secrets", 1, step_secrets))
    steps += [
        Step("acl", "Droits du compte de service", 1, step_acl),
        Step("database", "Base de données", 2, step_database),
        Step("schema", "Initialisation du schéma", 2, step_schema),
        Step("collect", "Première collecte des licences", 3, step_collect),
    ]
    if a.web_mode == "iis":
        steps += [Step("iis_prereq", "Rôles et modules IIS", 6, step_iis_prereq),
                  Step("iis_site", "Site IIS et authentification Windows", 2, step_iis_site)]
    steps.append(Step("tasks", "Tâches planifiées", 2, step_tasks))
    if a.open_firewall and a.web_mode != "local":
        steps.append(Step("firewall", "Pare-feu Windows", 1, step_firewall))
    steps.append(Step("start", "Démarrage de l'interface web", 2, step_start))
    steps.append(Step("shortcut", "Raccourci sur le bureau", 1, step_shortcut))
    return steps


def build_update_plan(a: Answers) -> list[Step]:
    """Mise à jour en place : configuration, secrets, tâches et site IIS conservés, aucun mot de passe demandé."""
    return [
        Step("backup", "Sauvegarde", 1, step_backup),
        Step("binaries", "Remplacement de HeptagonesLM", 2, step_binaries),
        Step("schema", "Mise à jour du schéma", 2, step_schema),
        Step("watchdog", "Relance automatique de l'interface", 1, step_watchdog),
        Step("start", "Redémarrage de l'interface web", 2, step_start),
        Step("collect", "Collecte de contrôle", 2, step_collect),
        Step("shortcut", "Raccourci sur le bureau", 1, step_shortcut),
    ]


# ------------------------------------------------------------------------------ exécution


class Installer:
    def __init__(self, system: System, a: Answers, update: bool = False):
        self.system, self.a, self.update = system, a, update
        self.steps = build_update_plan(a) if update else build_plan(a)
        self.status = "idle"               # idle | running | done | failed
        self.current = -1
        self.fraction = 0.0                # avancement dans l'étape courante
        self.logs: list[dict] = []
        self.started_at: str | None = None
        self.ended_at: str | None = None
        self.error: str | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # -- journal -----------------------------------------------------------------------
    def log(self, message: str, level: str = "info") -> None:
        with self._lock:
            self.logs.append({"t": datetime.now().strftime("%H:%M:%S"), "level": level, "msg": str(message)})

    def _progress(self, fraction: float) -> None:
        self.fraction = max(0.0, min(1.0, fraction))

    @property
    def percent(self) -> float:
        total = sum(s.weight for s in self.steps) or 1
        done = sum(s.weight for s in self.steps if s.status in ("done", "skipped"))
        if 0 <= self.current < len(self.steps) and self.steps[self.current].status == "running":
            done += self.steps[self.current].weight * self.fraction
        return round(100 * done / total, 1)

    def state(self, since: int = 0) -> dict:
        with self._lock:
            logs = self.logs[since:]
        running = self.steps[self.current].label if 0 <= self.current < len(self.steps) and self.status == "running" else None
        return {"status": self.status, "percent": self.percent if self.status != "done" else 100.0,
                "current": running, "steps": [s.to_dict() for s in self.steps], "logs": logs,
                "log_count": since + len(logs), "error": self.error,
                "started_at": self.started_at, "ended_at": self.ended_at}

    # -- exécution ---------------------------------------------------------------------
    def start(self, resume: bool = False) -> None:
        if self.status == "running":
            raise SetupError("Installation déjà en cours")
        first = 0
        if resume:
            first = next((i for i, s in enumerate(self.steps) if s.status == "failed"), 0)
            self.log(f"Reprise à l'étape « {self.steps[first].label} »", "info")
        else:
            for s in self.steps:
                s.status, s.message, s.started, s.ended = "pending", "", None, None
            self.logs.clear()
            self.started_at = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            verb = "Mise à jour vers" if self.update else "Installation de"
            self.log(f"{verb} HeptagonesLM {__version__} dans {self.a.install_dir} ({self.system.name})")
        self.status, self.error, self.ended_at = "running", None, None
        self._thread = threading.Thread(target=self._run, args=(first,), daemon=True, name="heptagoneslm-setup")
        self._thread.start()

    def wait(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _run(self, first: int) -> None:
        ctx = Context(self.system, self.a, self.log, self._progress)
        for i in range(first, len(self.steps)):
            step = self.steps[i]
            self.current, self.fraction = i, 0.0
            step.status, step.started, step.message = "running", time.time(), ""
            self.log(f"— {step.label}", "step")
            try:
                step.message = step.run(ctx) or ""
                step.status = "done"
                self.log(step.message or "Terminé", "ok")
            except SetupError as exc:
                self._fail(step, str(exc))
                return
            except Exception as exc:  # erreur inattendue : détail complet dans le journal
                self.log(traceback.format_exc(), "debug")
                self._fail(step, f"Erreur inattendue : {exc}")
                return
            finally:
                step.ended = time.time()
        self.status = "done"
        self.ended_at = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        self.log("Mise à jour terminée" if self.update else "Installation terminée", "ok")
        if self.update:
            self._record_version()
        self._save_log()

    def _record_version(self) -> None:
        """Version installée notée dans le fichier des réponses (détection des mises à jour suivantes)."""
        try:
            raw = self.system.read_text(self.a.path(ANSWERS_FILE))
            data = json.loads(raw) if raw else {k: v for k, v in self.a.to_dict().items() if k not in SECRET_FIELDS}
            data["_version"], data["_updated"] = __version__, datetime.now().isoformat(timespec="seconds")
            self.system.write_text(self.a.path(ANSWERS_FILE), json.dumps(data, ensure_ascii=False, indent=2))
        except Exception as exc:                     # non bloquant : la mise à jour est faite
            self.log(f"Version non enregistrée : {exc}", "warning")

    def _fail(self, step: Step, message: str) -> None:
        step.status, step.message = "failed", message
        self.status, self.error = "failed", f"{step.label} : {message}"
        self.ended_at = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        self.log(message, "error")
        self._save_log()

    def report_text(self) -> str:
        lines = [f"HeptagonesLM {__version__} — rapport d'installation", f"Serveur : {self.system.name}",
                 f"Début : {self.started_at}  Fin : {self.ended_at}  Statut : {self.status}", ""]
        lines += [f"[{s.status:^8}] {s.label}" + (f" — {s.message}" if s.message else "") for s in self.steps]
        lines += ["", "Journal :"] + [f"{x['t']} {x['level']:<7} {x['msg']}" for x in self.logs]
        return "\n".join(lines) + "\n"

    def _save_log(self) -> None:
        try:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            self.system.write_text(self.a.path("logs", f"setup-{stamp}.log"), self.report_text())
        except Exception:
            pass    # le journal d'installation ne doit jamais masquer le résultat
