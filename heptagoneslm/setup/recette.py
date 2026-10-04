"""Recette sur le site client : HeptagonesLM confronté aux serveurs de licences réels.

Sur le serveur installé, à partir de la configuration en place (aucune ressaisie) :

* **Comparaison en direct** : une collecte planifiée est déclenchée (tâche du compte de service),
  puis chaque serveur de licences est interrogé en direct (``lmstat`` / ``rlmstat``). Les totaux
  sont lus directement dans la sortie brute, par une lecture indépendante de l'analyseur de
  HeptagonesLM, et comparés feature par feature à ce que HeptagonesLM vient d'enregistrer (jetons
  émis, jetons utilisés, sessions ouvertes, utilisateurs connectés).
* **Collecte planifiée** : nombre de collectes, échecs et interruptions sur les N dernières heures.
* **Installation** : la vérification de bout en bout de l'assistant (tâches, IIS, HTTPS, base…).
* **Accès** : tests de connexion depuis les postes (page /acces), refus d'un compte hors du
  groupe autorisé (et accès d'un membre, pour écarter un faux positif).
* **Notifications et rapport** : e-mail, ticket ITSM et rapport Excel de test ; l'opérateur
  confirme leur réception dans l'assistant.

Les preuves sont enregistrées dans ``<installation>\\recette\\<horodatage>\\`` : sorties brutes des
serveurs de licences (noms d'utilisateurs et de postes anonymisables), résumé attendu
(``*.attendu.json``), rapport Excel et résultats (``recette.json``). Envoyées à l'équipe
HeptagonesLM, les sorties deviennent des tests de non-régression (``tests/fixtures/reel``).
Le PV de recette (``pv.py``) reprend chaque point pour signature avant la mise en production.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta
from pathlib import Path, PureWindowsPath
from typing import Callable

import yaml

from heptagoneslm import __version__
from heptagoneslm.collectors.flexlm.lmstat import parse_lmstat
from heptagoneslm.collectors.rlm.rlmstat import parse_rlmstat
from heptagoneslm.config import AppConfig, ConfigError, ServerConfig, parse_config
from heptagoneslm.models import ServerStatus
from heptagoneslm.setup.answers import SECRET_FIELDS, Answers, LicenseServer
from heptagoneslm.setup.installer import ANSWERS_FILE
from heptagoneslm.setup.system import SetupError, System

log = logging.getLogger(__name__)

COLLECT_TASK = "HeptagonesLM - Collecte"
STATUS_RANK = {"error": 0, "warning": 1, "info": 2, "ok": 3}
TASK_RUNNING = 267009
TASK_NEVER_RUN = 267011

Progress = Callable[[str, str, str, float | None], None]


# ------------------------------------------------------------------------------------------ résultats


@dataclass
class Item:
    """Un point de contrôle de la recette (une ligne du PV)."""

    id: str
    category: str
    label: str
    status: str                       # ok | warning | error | info (après confirmation éventuelle)
    detail: str = ""
    details: list[str] = field(default_factory=list)
    confirm: str | None = None        # question à l'opérateur (« Message reçu ? »)
    confirmed: bool | None = None
    auto_status: str = ""             # résultat automatique, avant confirmation
    table: dict | None = None         # {"columns": [...], "rows": [[...]]}
    files: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.auto_status:
            self.auto_status = self.status
        self.apply_confirmation()

    def apply_confirmation(self) -> None:
        """Un point à confirmer n'est conforme qu'après la réponse de l'opérateur."""
        if not self.confirm or self.auto_status not in ("ok", "warning"):
            self.status = self.auto_status
        elif self.confirmed is True:
            self.status = "ok"
        elif self.confirmed is False:
            self.status = "error"
        else:
            self.status = "warning"

    def to_dict(self) -> dict:
        return asdict(self)


def plural(n: int, singular: str, plural_form: str | None = None) -> str:
    """« 0 contrôle », « 1 contrôle », « 2 contrôles » (règle française : pluriel à partir de 2)."""
    return f"{n} {singular if abs(n) < 2 else (plural_form or singular + 's')}"


def verdict(items: list[Item]) -> dict:
    counts = {k: sum(1 for i in items if i.status == k) for k in STATUS_RANK}
    pending = sum(1 for i in items if i.confirm and i.confirmed is None and i.auto_status in ("ok", "warning"))
    if counts["error"]:
        label, level = "Non conforme : corriger les points en rouge puis relancer la recette", "error"
    elif counts["warning"]:
        label, level = "Conforme avec réserves (points en orange)", "warning"
    else:
        label, level = "Conforme : bon pour la mise en production", "ok"
    return {"level": level, "label": label, "counts": counts, "pending": pending}


@dataclass
class Options:
    hours: int = 24                   # période de collecte planifiée contrôlée
    anonymize: bool = True            # noms d'utilisateurs et de postes remplacés dans les sorties enregistrées
    email_to: str = ""
    itsm: bool = True
    report_days: int = 7
    outsider_user: str = ""
    outsider_password: str = ""
    member_user: str = ""
    member_password: str = ""

    @classmethod
    def from_dict(cls, raw: dict | None) -> "Options":
        raw = dict(raw or {})
        out = cls()
        for f in fields(cls):
            if f.name not in raw:
                continue
            value = raw[f.name]
            if isinstance(f.default, bool):
                value = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "oui", "on")
            elif isinstance(f.default, int):
                try:
                    value = max(1, min(int(value), 24 * 31))
                except (TypeError, ValueError):
                    continue
            else:
                value = "" if value is None else str(value).strip()
            setattr(out, f.name, value)
        return out


# ------------------------------------------------------------------------------- installation en place


@dataclass
class Installed:
    config_path: str
    install_dir: str
    cfg: AppConfig
    answers: Answers
    collect_interval: int = 5

    def summary(self) -> dict:
        web = self.answers.web_mode
        return {"config_path": self.config_path, "install_dir": self.install_dir,
                "servers": [{"name": s.name, "port_at_host": s.port_at_host, "license_type": s.license_type}
                            for s in self.cfg.servers],
                "web_mode": web, "public_url": self.answers.public_url if web != "local" else None,
                "access_group": self.answers.access_group,
                "email": {"configured": bool(self.cfg.email.smtp_host),
                          "recipients": list((self.cfg.alerts.recipients if self.cfg.alerts else []) or
                                             [r for s in self.cfg.email.subscriptions for r in s.recipients])},
                "itsm": [{"name": c.name, "type": c.type} for c in (self.cfg.alerts.itsm if self.cfg.alerts else [])],
                "collect_interval": self.collect_interval}


def find_installed(system: System, install_dir: str | None = None) -> str | None:
    """Configuration d'une installation existante sur ce serveur (ou None)."""
    for folder in dict.fromkeys([d for d in (install_dir, r"C:\HeptagonesLM") if d]):
        path = str(PureWindowsPath(folder) / "heptagoneslm.yaml")
        if system.read_text(path) is not None:
            return path
    return None


def _web_mode(cfg: AppConfig) -> str:
    if cfg.web.auth.mode == "header":
        return "iis"
    if cfg.web.tls_cert:
        return "https"
    return "local"


def answers_from_config(cfg: AppConfig, install_dir: str, saved: dict | None, web_config: str | None) -> Answers:
    """Réponses de l'assistant d'une installation existante : fichier enregistré à l'installation
    si présent (26.3.1 et suivantes), sinon déduites de la configuration."""
    data = {k: v for k, v in (saved or {}).items() if k not in SECRET_FIELDS}
    a = Answers.from_dict(data) if data else Answers()
    a.install_dir = install_dir
    a.servers = [LicenseServer(name=s.name, port_at_host=s.port_at_host, collectors=list(s.collectors),
                               log_path=s.log_path or "", license_type=s.license_type) for s in cfg.servers]
    a.lmutil_path, a.rlmutil_path = cfg.lmutil_path, cfg.rlmutil_path if cfg.rlmutil_path != "rlmutil" else ""
    if not data:
        a.web_mode = _web_mode(cfg)
        a.web_port = cfg.web.port
        if cfg.web.public_url:
            m = re.match(r"^https?://([^/:]+)(?::(\d+))?", cfg.web.public_url)
            if m:
                a.public_host = m.group(1)
                if a.web_mode == "iis":
                    a.iis_port = int(m.group(2) or 443)
        if web_config:
            m = re.search(r'roles="([^"]+)"', web_config)
            if m:
                a.access_group = m.group(1)
        if cfg.web.auth.mode == "ldap":
            a.ldap_servers = list(cfg.web.auth.ldap_servers)
            a.base_dn, a.upn_suffix = cfg.web.auth.base_dn, cfg.web.auth.upn_suffix or ""
            if cfg.web.auth.allowed_groups:
                a.access_group = cfg.web.auth.allowed_groups[0]
        a.db_mode = "sqlite" if cfg.database.connection_string.startswith("sqlite:///") else "sqlserver"
        a.email_enabled = bool(cfg.email.smtp_host)
        a.itsm_enabled = bool(cfg.alerts and cfg.alerts.itsm)
    return a


def load_installed(system: System, config_path: str) -> Installed:
    text = system.read_text(config_path)
    if text is None:
        raise SetupError(f"Configuration introuvable : {config_path}")
    # Secrets en variables d'environnement machine (définies à l'installation) : nécessaires à la lecture
    for name, value in system.machine_env().items():
        if name.startswith("HEPTAGONESLM_"):
            os.environ.setdefault(name, value)
    try:
        cfg = parse_config(yaml.safe_load(text))
    except (ConfigError, yaml.YAMLError) as exc:
        raise SetupError(f"Configuration illisible ({config_path}) : {exc}") from None
    install_dir = str(PureWindowsPath(config_path).parent)
    saved = None
    raw = system.read_text(str(PureWindowsPath(install_dir) / ANSWERS_FILE))
    if raw:
        try:
            saved = json.loads(raw)
        except ValueError:
            saved = None
    web_config = system.read_text(str(PureWindowsPath(install_dir) / "iis" / "web.config"))
    answers = answers_from_config(cfg, install_dir, saved, web_config)
    interval = (saved or {}).get("collect_interval") or 5
    return Installed(config_path, install_dir, cfg, answers, int(interval))


# ----------------------------------------------------------------- lecture indépendante des sorties brutes

# Ligne de synthèse de lmstat, calculée par le serveur de licences lui-même
_FLEX_TOTAL_RE = re.compile(r"^Users of (?P<name>[^:\r\n]+?):\s+\(Total of (?P<issued>\d+) licenses? issued;"
                            r"\s+Total of (?P<used>\d+) licenses? in use\)", re.M)
_FLEX_UNCOUNTED_RE = re.compile(r"^Users of (?P<name>[^:\r\n]+?):\s+\(Uncounted", re.M)
_RLM_POOL_RE = re.compile(r"^(?P<name>\S+) v\S+$")
_RLM_COUNT_RE = re.compile(r"^(?:count:\s*(?P<count>\d+)|UNCOUNTED)\b.*?\binuse:\s*(?P<used>\d+)", re.I)


def read_totals(text: str, license_type: str = "flexlm") -> dict[str, dict]:
    """Jetons émis / utilisés par feature, lus directement dans la sortie brute du serveur de licences
    (sans l'analyseur de HeptagonesLM : c'est la référence de la comparaison)."""
    totals: dict[str, dict] = {}

    def add(name, issued, used):
        cur = totals.setdefault(name, {"issued": None, "in_use": 0})
        if issued is not None:
            cur["issued"] = (cur["issued"] or 0) + issued
        cur["in_use"] += used

    if license_type == "rlm":
        lines = [line.strip() for line in text.splitlines()]
        in_pool = False
        for i, line in enumerate(lines):
            if " license pool status on " in line:
                in_pool = True
                continue
            if " license usage status on " in line or " ISV server status on " in line:
                in_pool = False
                continue
            m = _RLM_POOL_RE.match(line) if in_pool else None
            if m and i + 1 < len(lines):
                c = _RLM_COUNT_RE.match(lines[i + 1])
                if c:
                    add(m.group("name"), int(c.group("count")) if c.group("count") else None, int(c.group("used")))
        return totals
    for m in _FLEX_TOTAL_RE.finditer(text):
        add(m.group("name").strip(), int(m.group("issued")), int(m.group("used")))
    for m in _FLEX_UNCOUNTED_RE.finditer(text):
        add(m.group("name").strip(), None, 0)
    return totals


def parse_output(text: str, srv: ServerConfig, when: datetime | None = None) -> ServerStatus:
    parser = parse_rlmstat if srv.license_type == "rlm" else parse_lmstat
    return parser(text, srv.port_at_host, queried_at=when or datetime.now().replace(microsecond=0))


def live_sessions(status: ServerStatus) -> dict[tuple, int]:
    out: dict[tuple, int] = {}
    for f in status.features:
        for c in f.checkouts:
            key = (f.name, c.user.lower(), c.host.lower())
            out[key] = out.get(key, 0) + (c.count or 1)
    return out


def anonymize(text: str, status: ServerStatus, port_at_host: str) -> tuple[str, dict[str, str]]:
    """Remplace les noms d'utilisateurs et de postes par des pseudonymes stables (utilisateur001…)."""
    protected = {h.lower() for part in port_at_host.split(":") for h in part.split("@")[1:]}
    users = sorted({c.user for f in status.features for c in f.checkouts})
    hosts = sorted({n for f in status.features for c in f.checkouts for n in (c.host, c.display) if n} - set(users))
    mapping = {u: f"utilisateur{i:03d}" for i, u in enumerate(users, 1) if u.lower() not in protected}
    mapping.update({h: f"poste{i:03d}" for i, h in enumerate(hosts, 1) if h.lower() not in protected})
    for name in sorted(mapping, key=len, reverse=True):
        text = re.sub(rf"(?<![\w.\-]){re.escape(name)}(?![\w.\-])", mapping[name], text)
    return text, mapping


def expected_summary(text: str, srv: ServerConfig, captured_at: datetime, anonymized: bool, status: str) -> dict:
    """Résumé attendu d'une sortie enregistrée : rejoué par les tests de non-régression."""
    parsed = parse_output(text, srv, captured_at)
    sessions = {f.name: sum(c.count or 1 for c in f.checkouts) for f in parsed.features}
    return {
        "source": "rlmstat" if srv.license_type == "rlm" else "lmstat",
        "license_type": srv.license_type, "server": srv.name, "port_at_host": srv.port_at_host,
        "captured_at": captured_at.isoformat(timespec="seconds"), "anonymized": anonymized,
        "heptagoneslm_version": __version__, "comparison": status,
        "features": {name: {**tot, "sessions": sessions.get(name, 0)}
                     for name, tot in sorted(read_totals(text, srv.license_type).items())},
        "users": len({(c.user.lower(), c.host.lower()) for f in parsed.features for c in f.checkouts}),
    }


# --------------------------------------------------------------------------------- données enregistrées


class Recorded:
    """Données enregistrées par HeptagonesLM dans sa base (dernier sondage, sessions, collectes)."""

    def __init__(self, db):
        self.db = db

    def latest(self, server: str, source: str) -> dict:
        rows = self.db.query_dicts(
            "SELECT f.name, us.issued, us.in_use, us.snapshot_time FROM usage_snapshot us "
            "JOIN feature f ON f.id = us.feature_id JOIN license_server s ON s.id = f.server_id "
            "WHERE s.name = ? AND us.snapshot_time = (SELECT MAX(u2.snapshot_time) FROM usage_snapshot u2 "
            "JOIN feature f2 ON f2.id = u2.feature_id WHERE f2.server_id = s.id)", [server])
        sessions: dict[tuple, int] = {}
        for r in self.db.query_dicts(
                "SELECT f.name AS feature, u.username, w.hostname, ls.license_count FROM license_session ls "
                "JOIN feature f ON f.id = ls.feature_id JOIN license_server s ON s.id = f.server_id "
                "JOIN app_user u ON u.id = ls.user_id JOIN workstation w ON w.id = ls.workstation_id "
                "WHERE s.name = ? AND ls.checkin_time IS NULL AND ls.source = ?", [server, source]):
            key = (r["feature"], str(r["username"]).lower(), str(r["hostname"]).lower())
            sessions[key] = sessions.get(key, 0) + int(r["license_count"] or 1)
        when = _dt(rows[0]["snapshot_time"]) if rows else None
        return {"time": when, "features": {r["name"]: {"issued": r["issued"], "in_use": r["in_use"]} for r in rows},
                "sessions": sessions}

    def runs(self, server: str, since: datetime) -> list[dict]:
        rows = self.db.query_dicts(
            "SELECT r.collector, r.started_at, r.status, r.message FROM collection_run r "
            "JOIN license_server s ON s.id = r.server_id WHERE s.name = ? AND r.started_at >= ? "
            "ORDER BY r.started_at", [server, since])
        for r in rows:
            r["started_at"] = _dt(r["started_at"])
        return rows

    def first_run(self, server: str) -> datetime | None:
        rows = self.db.query_dicts(
            "SELECT MIN(r.started_at) AS t FROM collection_run r JOIN license_server s ON s.id = r.server_id "
            "WHERE s.name = ?", [server])
        return _dt(rows[0]["t"]) if rows and rows[0]["t"] else None


def _dt(value) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value)[:19])


# ---------------------------------------------------------------------------------------- comparaison


def compare(live: dict[str, dict], live_users: dict[tuple, int], recorded: dict, track_sessions: bool) -> tuple:
    """Compare la lecture directe du serveur de licences à l'enregistrement de HeptagonesLM.
    Renvoie (statut, lignes du tableau, détails)."""
    rows, details = [], []
    errors = warnings = 0
    rec = recorded["features"]
    sessions_by_feature: dict[str, int] = {}
    for (feat, _u, _h), n in recorded["sessions"].items():
        sessions_by_feature[feat] = sessions_by_feature.get(feat, 0) + n
    for name in sorted(set(live) | set(rec), key=str.lower):
        lv, rv = live.get(name), rec.get(name)
        sess = sessions_by_feature.get(name, 0) if track_sessions else None
        flags = []
        if lv is None:
            flags.append("absente de la sortie en direct (retirée du fichier de licence ?)")
            warnings += 1
        elif rv is None:
            flags.append("non enregistrée par HeptagonesLM")
            errors += 1
        else:
            if lv["issued"] != rv["issued"]:
                flags.append("jetons émis différents")
                errors += 1
            if lv["in_use"] != rv["in_use"]:
                flags.append("jetons utilisés différents")
                warnings += 1
            if track_sessions and sess != lv["in_use"]:
                flags.append("sessions ouvertes ≠ jetons utilisés")
                warnings += 1
        rows.append([name, _n(lv and lv["issued"]), _n(rv and rv["issued"]), _n(lv and lv["in_use"]),
                     _n(rv and rv["in_use"]), "—" if sess is None else str(sess), "; ".join(flags) or "identique"])
    if track_sessions:
        missing = sorted(set(live_users) - set(recorded["sessions"]))
        extra = sorted(set(recorded["sessions"]) - set(live_users))
        if missing:
            warnings += 1
            details.append(plural(len(missing), "utilisateur connecté non vu", "utilisateurs connectés non vus")
                           + " par HeptagonesLM : "
                           + ", ".join(f"{u}@{h} ({f})" for f, u, h in missing[:8]) + (" …" if len(missing) > 8 else ""))
        if extra:
            warnings += 1
            details.append(plural(len(extra), "session ouverte", "sessions ouvertes") + " dans HeptagonesLM mais "
                           + ("plus présente" if len(extra) < 2 else "plus présentes") + " sur le serveur : "
                           + ", ".join(f"{u}@{h} ({f})" for f, u, h in extra[:8]) + (" …" if len(extra) > 8 else ""))
        if not missing and not extra:
            details.append(plural(len(live_users), "utilisateur connecté", "utilisateurs connectés")
                           + (" : identique dans HeptagonesLM" if len(live_users) == 1 else
                              " : identiques dans HeptagonesLM" if live_users else ""))
    status = "error" if errors else "warning" if warnings else "ok"
    return status, rows, details


def _n(value) -> str:
    return "—" if value is None else str(value)


COMPARE_COLUMNS = ["Feature", "Émis · serveur", "Émis · HeptagonesLM", "Utilisés · serveur",
                   "Utilisés · HeptagonesLM", "Sessions", "Résultat"]


def collection_health(runs: list[dict], since: datetime, now: datetime, interval_min: int,
                      first_run: datetime | None, hours: int) -> tuple:
    """Collecte planifiée sur la période : (statut, détail, détails)."""
    if not runs:
        return "error", f"Aucune collecte sur les {hours} dernières heures", [
            "Vérifier la tâche planifiée « HeptagonesLM - Collecte » (Planificateur de tâches) et logs\\heptagoneslm.log"]
    errors = [r for r in runs if r["status"] != "OK"]
    times = [r["started_at"] for r in runs if r["started_at"]]
    gaps = [(b - a, a) for a, b in zip(times, times[1:])]
    gaps.append((now - times[-1], times[-1]))
    worst, worst_at = max(gaps, key=lambda g: g[0])
    details = [f"{plural(len(runs), 'collecte')} depuis le {max(since, times[0]):%d/%m à %H:%M} · "
               f"{plural(len(errors), 'échec')} · "
               f"plus long intervalle : {_dur(worst)}"]
    status = "ok"
    if errors:
        rate = len(errors) / len(runs)
        last = errors[-1]
        details.append(f"Dernier échec le {last['started_at']:%d/%m à %H:%M} : {(last['message'] or '?')[:200]}")
        status = "error" if rate > 0.05 or runs[-1]["status"] != "OK" else "warning"
    if worst > timedelta(minutes=3 * max(interval_min, 1)):
        details.append(f"Interruption de {_dur(worst)} après le {worst_at:%d/%m à %H:%M} (intervalle prévu : "
                       f"{interval_min} min)")
        if status == "ok":
            status = "warning"
    coverage = now - (first_run or times[0])
    if coverage < timedelta(hours=hours) - timedelta(minutes=2 * max(interval_min, 1)):
        details.append(f"Installation collectée depuis {_dur(coverage)} seulement : refaire la recette après "
                       f"{hours} h de fonctionnement")
        if status == "ok":
            status = "warning"
    detail = (f"{len(runs) - len(errors)} / {len(runs)} collectes réussies sur {hours} h"
              if status != "error" or errors else details[0])
    return status, detail, details


def _dur(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} min"
    h, m = divmod(minutes, 60)
    return f"{h} h {m:02d}" if h < 48 else f"{h // 24} j {h % 24} h"


# --------------------------------------------------------------------------------------------- exécution


class RecetteRun:
    """Une recette : contrôles, preuves enregistrées, confirmations de l'opérateur, PV."""

    def __init__(self, system: System, installed: Installed, options: Options, progress: Progress | None = None):
        self.system, self.inst, self.opt = system, installed, options
        self.progress = progress or (lambda *a: None)
        self.items: list[Item] = []
        self.started = datetime.now().replace(microsecond=0)
        self.stamp = self.started.strftime("%Y%m%d-%H%M%S")
        self.folder = str(PureWindowsPath(installed.install_dir) / "recette" / self.stamp)
        self.files: list[str] = []
        self.host = ""
        self._sim_dir: tempfile.TemporaryDirectory | None = None

    # -- utilitaires -------------------------------------------------------------------------------
    def add(self, item: Item) -> Item:
        self.items.append(item)
        return item

    def save_text(self, name: str, text: str) -> None:
        self.system.write_text(str(PureWindowsPath(self.folder) / name), text)
        if name not in self.files:
            self.files.append(name)

    def save_bytes(self, name: str, data: bytes) -> None:
        self.system.write_bytes(str(PureWindowsPath(self.folder) / name), data)
        if name not in self.files:
            self.files.append(name)

    def step(self, key: str, label: str, fn) -> None:
        """Exécute un groupe de contrôles ; une erreur inattendue devient un point en erreur (jamais un plantage)."""
        t0 = time.monotonic()
        self.progress(key, label, "running", None)
        state = "done"
        try:
            fn()
        except Exception as exc:              # le reste de la recette continue
            log.exception("Recette : %s", label)
            self.add(Item(f"{key}:erreur", "Recette", label, "error", f"Contrôle interrompu : {exc}"))
            state = "error"
        self.progress(key, label, state, round(time.monotonic() - t0, 1))

    def open_db(self):
        from heptagoneslm.storage import Database

        if self.system.simulated:
            return self._simulated_db()
        return Database(self.inst.cfg.database.connection_string)

    def _simulated_db(self):
        """Simulation : vraie base SQLite remplie par le vrai code de collecte (activité simulée)."""
        from heptagoneslm.simulation.demo import run_demo
        from heptagoneslm.storage import Database

        if self._sim_dir is None:
            self._sim_dir = tempfile.TemporaryDirectory(prefix="heptagoneslm-recette-")
            hours = min(self.opt.hours, 48)
            now = datetime.now().replace(second=0, microsecond=0)
            end = now - timedelta(minutes=now.minute % 5)
            result = run_demo(self._sim_dir.name, days=max(1, -(-hours // 24)), end=end, make_report=False)
            db = Database("sqlite:///" + str(Path(self._sim_dir.name) / "heptagoneslm_demo.db"))
            with db:
                # journal des collectes planifiées (toutes les 5 min sur la période), comme en production
                ids = {r["name"]: r["id"] for r in db.query_dicts("SELECT id, name FROM license_server")}
                t, rows = max(result.start, now - timedelta(hours=hours)), []
                while t <= end:
                    for srv in self.inst.cfg.servers:
                        if srv.name in ids:
                            rows.append((ids[srv.name], srv.collectors[0], t, t, "OK", 1, None))
                    t += timedelta(minutes=5)
                with db.transaction():
                    db.executemany("INSERT INTO collection_run (server_id, collector, started_at, finished_at, status, "
                                   "items, message) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        return Database("sqlite:///" + str(Path(self._sim_dir.name) / "heptagoneslm_demo.db"))

    # -- déroulé -----------------------------------------------------------------------------------
    def run(self) -> dict:
        try:
            self.host = str(self.system.os_info().get("hostname") or "")
        except SetupError:
            self.host = ""
        self.system.makedirs(self.folder)
        self.step("collecte", "Collecte déclenchée (tâche planifiée)", self.trigger_collection)
        for srv in self.inst.cfg.servers:
            if srv.enabled:
                self.step(f"srv:{srv.name}", f"Comparaison en direct · {srv.name}", lambda s=srv: self.compare_server(s))
        self.step("historique", f"Collecte planifiée sur {self.opt.hours} h", self.check_history)
        self.step("installation", "Vérification de l'installation", self.check_installation)
        if self.inst.answers.web_mode != "local":
            self.step("acces", "Connexion depuis les postes", self.check_workstations)
            self.step("refus", "Refus hors du groupe autorisé", self.check_refusal)
        self.step("email", "E-mail de test", self.check_email)
        self.step("itsm", "Ticket ITSM de test", self.check_itsm)
        self.step("rapport", "Rapport Excel de test", self.check_report)
        self.save_results()
        if self._sim_dir is not None:
            self._sim_dir.cleanup()
            self._sim_dir = None
        return self.state()

    def state(self) -> dict:
        return {"stamp": self.stamp, "folder": self.folder, "files": list(self.files), "host": self.host,
                "version": __version__, "started": self.started.isoformat(timespec="seconds"),
                "config_path": self.inst.config_path, "hours": self.opt.hours, "anonymized": self.opt.anonymize,
                "items": [i.to_dict() for i in self.items], "verdict": verdict(self.items)}

    def save_results(self) -> None:
        self.save_text("recette.json", json.dumps(self.state(), ensure_ascii=False, indent=2, default=str))

    def confirm(self, item_id: str, confirmed: bool | None) -> Item:
        for item in self.items:
            if item.id == item_id and item.confirm:
                item.confirmed = confirmed
                item.apply_confirmation()
                self.save_results()
                return item
        raise SetupError(f"Point de recette inconnu : {item_id}")

    # -- 1. collecte et comparaison ------------------------------------------------------------------
    def trigger_collection(self) -> None:
        if self.system.simulated:
            self._simulated_collection()
            self.add(Item("collecte", "Comparaison avec les serveurs de licences", "Collecte déclenchée", "ok",
                          "Simulation : collecte appliquée à la base simulée"))
            return
        result = self._collect_now()
        if result is None:
            self.add(Item("collecte", "Comparaison avec les serveurs de licences", "Collecte déclenchée", "warning",
                          f"Tâche « {COLLECT_TASK} » absente : comparaison avec le dernier sondage enregistré"))
            return
        info, secs = result
        code = info.get("last_result")
        ok = code == 0
        self.add(Item("collecte", "Comparaison avec les serveurs de licences",
                      "Collecte déclenchée (tâche planifiée, compte de service)", "ok" if ok else "error",
                      f"Terminée en {secs} s, résultat 0x{int(code or 0) & 0xFFFFFFFF:X}" + ("" if ok else
                      " : voir logs\\heptagoneslm.log"), details=[f"Compte : {info.get('user') or '?'}"]))

    def _collect_now(self) -> tuple[dict, int] | None:
        """Lance la tâche de collecte (compte de service) et attend sa fin ; None si la tâche est absente."""
        if self.system.simulated:
            self._simulated_collection()
            return {"last_result": 0, "user": "simulation"}, 0
        before = self.system.task_info(COLLECT_TASK)
        if not before:
            return None
        waited = 0
        while before.get("state") == "Running" and waited < 120:    # collecte planifiée en cours : l'attendre
            time.sleep(2)
            waited += 2
            before = self.system.task_info(COLLECT_TASK) or before
        t0 = time.monotonic()
        self.system.start_task(COLLECT_TASK)
        info = before
        while time.monotonic() - t0 < 300:
            time.sleep(2)
            info = self.system.task_info(COLLECT_TASK) or {}
            finished = info.get("state") != "Running" and info.get("last_result") not in (TASK_RUNNING, TASK_NEVER_RUN)
            if finished and info.get("last_run") != before.get("last_run"):
                break
        return info, round(time.monotonic() - t0)

    def _simulated_collection(self) -> None:
        from heptagoneslm.service import CollectionService

        with self.open_db() as db:
            svc = CollectionService(self.inst.cfg, db)
            ids = svc.sync_servers()
            now = datetime.now().replace(microsecond=0)
            for srv in self.inst.cfg.servers:
                text = self._live_text(srv)
                status = parse_output(text, srv, now)
                with db.transaction():
                    items = svc.apply_status(ids[srv.name], status, srv.collectors[0], srv.sessions_from in
                                             ("lmstat", "rlmstat"))
                    run_id = svc.repo.start_run(ids[srv.name], srv.collectors[0], now)
                    svc.repo.finish_run(run_id, "OK", items, None)

    def _live_text(self, srv: ServerConfig) -> str:
        rlm = srv.license_type == "rlm"
        tool = (srv.rlmutil_path or self.inst.cfg.rlmutil_path) if rlm else (srv.lmutil_path or self.inst.cfg.lmutil_path)
        r = self.system.run([tool, "rlmstat" if rlm else "lmstat", "-a", "-c", srv.port_at_host],
                            timeout=max(30, srv.timeout_s))
        if r.code in (124, 126, 127):
            raise SetupError(r.message)
        return (r.out or "") + "\n" + (r.err or "")

    def compare_server(self, srv: ServerConfig) -> None:
        category = "Comparaison avec les serveurs de licences"
        source = srv.sessions_from
        track = source in ("lmstat", "rlmstat")
        tool = "rlmstat" if srv.license_type == "rlm" else "lmstat"
        with self.open_db() as db:
            rec = Recorded(db)
            for attempt in range(2):          # activité entre les deux lectures : une seconde lecture
                captured = datetime.now().replace(microsecond=0)
                text = self._live_text(srv)
                parsed = parse_output(text, srv, captured)
                if not parsed.server_up:
                    self.add(Item(f"srv:{srv.name}", category, f"{srv.name} ({srv.port_at_host})", "error",
                                  f"Serveur injoignable en direct : {'; '.join(parsed.errors[:2]) or text.strip()[-200:]}"))
                    return
                totals = read_totals(text, srv.license_type)
                recorded = rec.latest(srv.name, source)
                status, rows, details = compare(totals, live_sessions(parsed), recorded, track)
                if status != "warning" or attempt == 1:
                    break
                self._collect_now()            # nouvelle collecte puis nouvelle lecture, au plus près
        gap = (captured - recorded["time"]) if recorded["time"] else None
        name = f"lmstat_{_safe(srv.name)}.txt" if tool == "lmstat" else f"rlmstat_{_safe(srv.name)}.txt"
        saved, mapping = (anonymize(text, parsed, srv.port_at_host) if self.opt.anonymize else (text, {}))
        self.save_text(name, saved)
        summary = expected_summary(saved, srv, captured, self.opt.anonymize, status)
        self.save_text(name.replace(".txt", ".attendu.json"), json.dumps(summary, ensure_ascii=False, indent=2))
        n_features = len(totals)
        issued = sum(v["issued"] or 0 for v in totals.values())
        used = sum(v["in_use"] for v in totals.values())
        detail = (f"{plural(n_features, 'feature')}, {used} / {plural(issued, 'jeton utilisé', 'jetons utilisés')} · "
                  + ("identique à HeptagonesLM" if status == "ok" else
                     "écarts d'utilisation (activité entre les deux lectures ?)" if status == "warning" else
                     "écarts à corriger"))
        details = [f"Lecture en direct le {captured:%d/%m/%Y à %H:%M:%S} ; sondage HeptagonesLM du "
                   + (f"{recorded['time']:%d/%m/%Y à %H:%M:%S} ({max(0, int(gap.total_seconds()))} s avant)"
                      if gap is not None else "— (aucun jeton enregistré : le serveur ne déclare aucune licence)"
                      if not totals else "— (aucun sondage enregistré)")] + details
        if not track:
            details.append("Sessions suivies par le log debug : utilisateurs comparés dans l'historique, pas ici")
        if mapping:
            details.append(f"Sortie enregistrée anonymisée ({plural(len(mapping), 'nom remplacé', 'noms remplacés')}) : {name}")
        self.add(Item(f"srv:{srv.name}", category, f"{srv.name} ({srv.port_at_host})", status, detail, details,
                      table={"columns": COMPARE_COLUMNS, "rows": rows}, files=[name, name.replace(".txt", ".attendu.json")]))

    # -- 2. historique de collecte ---------------------------------------------------------------------
    def check_history(self) -> None:
        now = datetime.now().replace(microsecond=0)
        since = now - timedelta(hours=self.opt.hours)
        with self.open_db() as db:
            rec = Recorded(db)
            for srv in self.inst.cfg.servers:
                if not srv.enabled:
                    continue
                # une ligne par collecteur et par passage : on suit le collecteur d'état (lmstat / rlmstat)
                runs = [r for r in rec.runs(srv.name, since) if r["collector"] == srv.collectors[0]]
                status, detail, details = collection_health(runs, since, now, self.inst.collect_interval,
                                                            rec.first_run(srv.name), self.opt.hours)
                self.add(Item(f"historique:{srv.name}", "Collecte planifiée", f"{srv.name} · {self.opt.hours} h",
                              status, detail, details))

    # -- 3. installation ---------------------------------------------------------------------------------
    def check_installation(self) -> None:
        from heptagoneslm.setup.verify import verify

        v = verify(self.system, self.inst.answers, connection_string=self.inst.cfg.database.connection_string)
        checks = [c for c in v["checks"] if not c["id"].startswith("access")]
        bad = [c for c in checks if c["status"] in ("error", "warning")]
        n_err = sum(1 for c in checks if c["status"] == "error")
        status = "error" if n_err else "warning" if bad else "ok"
        n_ok = sum(1 for c in checks if c['status'] == 'ok')
        detail = (plural(n_ok, "contrôle conforme", "contrôles conformes")
                  + (f", {plural(len(bad) - n_err, 'avertissement')}" if len(bad) > n_err else "")
                  + (f", {plural(n_err, 'erreur')}" if n_err else ""))
        rows = [[c["category"], c["label"], {"ok": "Conforme", "warning": "Avertissement", "error": "Erreur",
                                            "info": "Information"}.get(c["status"], c["status"]), c["detail"]]
                for c in checks]
        self.add(Item("installation", "Installation", "Vérification de bout en bout", status, detail,
                      [f"{c['label']} : {c['detail']}" for c in bad],
                      table={"columns": ["Domaine", "Contrôle", "Résultat", "Détail"], "rows": rows}))

    # -- 4. accès --------------------------------------------------------------------------------------
    def check_workstations(self) -> None:
        from heptagoneslm.setup import sso

        checks = sso.access_checks(self.system, self.inst.answers)
        tests = [c for c in checks if c.id.startswith("access:")]
        ok = [c for c in tests if c.status == "ok"]
        url = (self.inst.answers.public_url or "").rstrip("/") + "/acces"
        if ok:
            self.add(Item("acces", "Accès des utilisateurs", "Connexion depuis un poste (membre du groupe)", "ok",
                          plural(len(ok), "poste connecté", "postes connectés") + " sans saisie de mot de passe",
                          [f"{c.label} : {c.detail}" for c in tests]))
        elif tests:
            self.add(Item("acces", "Accès des utilisateurs", "Connexion depuis un poste (membre du groupe)", "warning",
                          "Mot de passe demandé sur les postes testés : zone Intranet local à déployer par GPO",
                          [f"{c.label} : {c.detail}" for c in tests]))
        else:
            self.add(Item("acces", "Accès des utilisateurs", "Connexion depuis un poste (membre du groupe)", "warning",
                          f"Aucun test reçu : ouvrir {url} depuis un poste utilisateur puis relancer la recette",
                          confirm="Un utilisateur du groupe a ouvert l'interface depuis son poste ?"))

    def check_refusal(self) -> None:
        a, o = self.inst.answers, self.opt
        label = f"Compte hors du groupe {a.access_group or 'autorisé'} refusé"
        category = "Accès des utilisateurs"
        if not o.outsider_user or not o.outsider_password:
            self.add(Item("refus", category, label, "warning",
                          "Non testé automatiquement : indiquer un compte hors du groupe, ou confirmer le refus constaté",
                          confirm="Un utilisateur hors du groupe a bien reçu un refus d'accès ?"))
            return
        if a.web_mode == "https":
            self._refusal_ldap(label, category)
            return
        url = (a.public_url or "").rstrip("/") + "/api/me"
        outsider = self.system.http_auth_status(url, o.outsider_user, o.outsider_password)
        member = (self.system.http_auth_status(url, o.member_user, o.member_password)
                  if o.member_user and o.member_password else None)
        details = [f"{o.outsider_user} (hors du groupe) : {_http(outsider)}"]
        if member is not None:
            details.append(f"{o.member_user} (membre) : {_http(member)}")
        if outsider == 200:
            status, detail = "error", f"Accès accordé à {o.outsider_user}, hors du groupe : revoir la restriction IIS"
        elif outsider == 401 and member == 200:
            status, detail = "ok", "Refus (401) hors du groupe, accès accordé au membre : restriction effective"
        elif outsider == 401 and member is None:
            status, detail = "ok", "Refus (401) hors du groupe (accès d'un membre non contrôlé ici)"
        elif outsider == 401:
            status, detail = "warning", ("Le membre est aussi refusé depuis ce serveur : test non concluant "
                                         "(contrôle de boucle locale NTLM ?) ; tester depuis un poste")
        else:
            status, detail = "warning", f"Réponse inattendue pour {o.outsider_user} : {_http(outsider)}"
        self.add(Item("refus", category, label, status, detail, details))

    def _refusal_ldap(self, label: str, category: str) -> None:
        from heptagoneslm.web.auth import AuthError, LdapAuthenticator

        auth = LdapAuthenticator(self.inst.cfg.web.auth)
        o = self.opt
        try:
            auth.authenticate(o.outsider_user, o.outsider_password)
        except AuthError as exc:
            self.add(Item("refus", category, label, "ok", f"Connexion refusée : {exc}"))
            return
        self.add(Item("refus", category, label, "error", f"Connexion acceptée pour {o.outsider_user}, hors du groupe"))

    # -- 5. notifications et rapport ---------------------------------------------------------------------
    def check_email(self) -> None:
        cfg = self.inst.cfg
        to = self.opt.email_to or next(iter(self.inst.summary()["email"]["recipients"]), "")
        if not cfg.email.smtp_host:
            self.add(Item("email", "Notifications", "E-mail de test", "info", "Messagerie non configurée"))
            return
        if not to:
            self.add(Item("email", "Notifications", "E-mail de test", "warning", "Aucun destinataire de test"))
            return
        if not self.system.simulated:
            from email.message import EmailMessage

            from heptagoneslm.mailer import send_message

            msg = EmailMessage()
            msg["Subject"] = f"HeptagonesLM : e-mail de test de la recette ({self.started:%d/%m/%Y %H:%M})"
            msg["From"] = cfg.email.sender
            msg["To"] = to
            msg.set_content("Ce message confirme que HeptagonesLM envoie les rapports et les alertes par e-mail.\n"
                            f"Recette du {self.started:%d/%m/%Y à %H:%M} sur {self.host or 'le serveur HeptagonesLM'}.")
            try:
                send_message(msg, cfg.email)
            except Exception as exc:
                self.add(Item("email", "Notifications", "E-mail de test", "error", f"Envoi impossible : {exc}"))
                return
        self.add(Item("email", "Notifications", f"E-mail de test à {to}", "ok",
                      f"Envoyé par {cfg.email.smtp_host}:{cfg.email.smtp_port}"
                      + (" (simulation)" if self.system.simulated else ""),
                      confirm=f"Le message « e-mail de test de la recette » est-il arrivé dans la boîte de {to} ?"))

    def check_itsm(self) -> None:
        connectors = self.inst.cfg.alerts.itsm if self.inst.cfg.alerts else []
        if not connectors:
            self.add(Item("itsm", "Notifications", "Ticket ITSM de test", "info", "Aucun outil ITSM configuré"))
            return
        if not self.opt.itsm:
            self.add(Item("itsm", "Notifications", "Ticket ITSM de test", "info", "Non demandé"))
            return
        from heptagoneslm import vault
        from heptagoneslm.itsm import ItsmError, check_connector

        for c in connectors:
            label = f"Ticket de test dans {c.name}"
            secrets = [getattr(c, n) for n in ("password", "token", "app_token", "user_token")]
            if self.system.simulated and any(vault.is_reference(s) for s in secrets):
                self.add(Item(f"itsm:{c.name}", "Notifications", label, "info",
                              "Simulation : secret chiffré factice, ticket non envoyé"))
                continue
            try:
                steps = check_connector(c, self.inst.cfg)
            except ItsmError as exc:
                self.add(Item(f"itsm:{c.name}", "Notifications", label, "error", str(exc)))
                continue
            ref = next((s.split(":", 1)[1].strip() for s in steps if s.startswith("ouverture")), "")
            self.add(Item(f"itsm:{c.name}", "Notifications", label, "ok", " ; ".join(steps),
                          confirm=f"Le ticket de test {ref.split(' ')[0] if ref else ''} est-il visible (résolu) "
                                  f"dans {c.name} ?"))

    def check_report(self) -> None:
        from heptagoneslm.reporting.dataset import build_report_data

        try:
            from heptagoneslm.reporting.excel import write_excel_report
        except ImportError:
            self.add(Item("rapport", "Rapport", "Rapport Excel de test", "error", "Module openpyxl absent"))
            return
        end = datetime.now().replace(microsecond=0)
        start = end - timedelta(days=self.opt.report_days)
        with self.open_db() as db:
            data = build_report_data(db, start, end, business_hours=self.inst.cfg.reporting.business_hours)
        name = "rapport_recette.xlsx"
        target = self.system.local_path(str(PureWindowsPath(self.folder) / name))
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        write_excel_report(data, target, top_n=self.inst.cfg.reporting.top_n_chart)
        if name not in self.files:
            self.files.append(name)
        size = Path(target).stat().st_size // 1024
        compared = [i for i in self.items if i.id.startswith("srv:") and i.table]
        no_license = bool(compared) and not any(i.table["rows"] for i in compared)
        if data.summaries:
            self.add(Item("rapport", "Rapport", "Rapport Excel de test", "ok",
                          f"{plural(len(data.summaries), 'feature')}, {plural(len(data.sessions), 'session')} sur "
                          f"{self.opt.report_days} jours ({size} Ko)", files=[name],
                          confirm="Le rapport Excel s'ouvre et ses chiffres sont cohérents avec l'usage connu ?"))
        elif no_license:     # serveur de test sans licence : un rapport vide est le résultat attendu
            self.add(Item("rapport", "Rapport", "Rapport Excel de test", "info",
                          "Rapport généré, vide comme attendu : les serveurs de licences ne déclarent aucune licence",
                          files=[name]))
        else:
            self.add(Item("rapport", "Rapport", "Rapport Excel de test", "warning",
                          "Rapport généré mais vide : aucune donnée sur la période", files=[name],
                          confirm="Le rapport Excel s'ouvre et ses chiffres sont cohérents avec l'usage connu ?"))

    # -- preuves -----------------------------------------------------------------------------------------
    def evidence_zip(self) -> bytes:
        """Sorties brutes, résumés attendus et résultats : à transmettre pour les tests de non-régression."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name in self.files:
                data = self.system.read_bytes(str(PureWindowsPath(self.folder) / name))
                if data is not None:
                    z.writestr(f"recette-{self.stamp}/{name}", data)
        return buf.getvalue()


def _safe(name: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", name)


def _http(code) -> str:
    return {200: "200 (accès accordé)", 401: "401 (refusé)", 403: "403 (interdit)", None: "pas de réponse"}.get(
        code, str(code))
