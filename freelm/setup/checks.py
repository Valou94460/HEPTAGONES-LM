"""Contrôle des prérequis du serveur, avec corrections automatiques quand c'est possible."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from freelm.setup import iis
from freelm.setup.answers import Answers
from freelm.setup.system import SetupError, System

ODBC_URL = "https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server"


@dataclass
class Check:
    id: str
    category: str
    label: str
    status: str            # ok | warning | error | info
    detail: str = ""
    fix: str | None = None       # identifiant de correction automatique
    fix_label: str | None = None
    help: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def blocking(checks: list[Check]) -> list[Check]:
    return [c for c in checks if c.status == "error"]


def run_checks(system: System, a: Answers | None = None) -> list[Check]:
    a = a or Answers()
    out: list[Check] = []

    def add(*args, **kw):
        out.append(Check(*args, **kw))

    # -- Système --------------------------------------------------------------------
    try:
        info = system.os_info()
    except SetupError as exc:
        info = {}
        add("os", "Système", "Système d'exploitation", "error", f"Lecture impossible : {exc}")
    if info:
        server = info.get("product_type") in (2, 3)
        build = int(info.get("build") or 0)
        if server and build >= 14393:
            add("os", "Système", "Système d'exploitation", "ok", f"{info['caption']} (build {build})")
        elif server:
            add("os", "Système", "Système d'exploitation", "warning",
                f"{info['caption']} : Windows Server 2016 ou supérieur recommandé")
        else:
            add("os", "Système", "Système d'exploitation", "warning",
                f"{info['caption']} : poste de travail (fonctionne, mais un serveur est recommandé)")
        ps = str(info.get("psversion") or "0")
        major_minor = tuple(int(x) for x in (ps.split(".") + ["0"])[:2] if x.isdigit())
        add("powershell", "Système", "Windows PowerShell", "ok" if major_minor >= (5, 1) else "error",
            f"version {ps}" + ("" if major_minor >= (5, 1) else " : 5.1 minimum requis"))
        if info.get("part_of_domain"):
            add("domain", "Système", "Membre du domaine", "ok", f"{info.get('domain')} ({info.get('fqdn')})")
        else:
            add("domain", "Système", "Membre du domaine", "warning",
                "Serveur hors domaine : connexion Windows / AD et compte gMSA indisponibles")
    admin = system.is_admin()
    add("admin", "Système", "Droits d'administrateur", "ok" if admin else "error",
        "Assistant lancé en administrateur" if admin else
        "Relancer FreeLM-Setup.exe par clic droit › « Exécuter en tant qu'administrateur »")
    try:
        free = system.disk_free_gb(a.install_dir)
        status = "error" if free < 2 else "warning" if free < 10 else "ok"
        add("disk", "Système", f"Espace disque ({a.install_path.drive})", status,
            f"{free:.1f} Go libres" + (" : 2 Go minimum" if status == "error" else " : 10 Go recommandés" if status == "warning" else ""))
    except OSError as exc:
        add("disk", "Système", "Espace disque", "warning", f"Lecture impossible : {exc}")

    # -- Base de données ----------------------------------------------------------------
    if a.db_mode == "sqlserver":
        drivers = [d for d in system.odbc_drivers() if "SQL Server" in d]
        if "ODBC Driver 18 for SQL Server" in drivers:
            add("odbc", "Base de données", "Pilote ODBC SQL Server", "ok", "ODBC Driver 18 for SQL Server")
        elif "ODBC Driver 17 for SQL Server" in drivers:
            add("odbc", "Base de données", "Pilote ODBC SQL Server", "warning",
                "ODBC Driver 17 détecté (fonctionne) ; version 18 recommandée", help=ODBC_URL)
        else:
            add("odbc", "Base de données", "Pilote ODBC SQL Server", "error",
                "ODBC Driver 18 for SQL Server absent : l'installer puis relancer la vérification", help=ODBC_URL)
    else:
        add("odbc", "Base de données", "Pilote ODBC SQL Server", "info", "Non requis (base SQLite locale)")

    # -- Licences ---------------------------------------------------------------------
    found = system.find_lmutil()
    if a.lmutil_path and (system.exists(a.lmutil_path) or a.lmutil_path in found):
        add("lmutil", "Licences", "lmutil.exe (outil FlexLM)", "ok", a.lmutil_path)
    elif found:
        add("lmutil", "Licences", "lmutil.exe (outil FlexLM)", "ok", found[0])
    else:
        add("lmutil", "Licences", "lmutil.exe (outil FlexLM)", "warning",
            "Introuvable : indiquer son chemin à l'étape « Serveurs de licences » "
            "(fourni avec Autodesk Network License Manager ou tout serveur FlexLM)")

    # -- Réseau ------------------------------------------------------------------------
    port_ok = system.port_free(a.web_port)
    add("port", "Réseau", f"Port interne {a.web_port}", "ok" if port_ok else "warning",
        "Libre" if port_ok else "Déjà utilisé (FreeLM déjà installé ?) : choisir un autre port ou arrêter l'application")

    # -- IIS ---------------------------------------------------------------------------
    if a.web_mode != "iis":
        add("iis", "IIS", "IIS (authentification unique)", "info",
            "Non requis pour le mode d'interface choisi")
        return out
    try:
        state = system.iis_state() or {}
    except SetupError as exc:
        add("iis", "IIS", "État d'IIS", "error", f"Lecture impossible : {exc}")
        return out
    features = state.get("features") or {}
    missing = [f for f in iis.FEATURES if not features.get(f[0])]
    for name, _client, label in iis.FEATURES:
        ok = bool(features.get(name))
        add(f"feature:{name}", "IIS", label, "ok" if ok else "error",
            "Installé" if ok else "Non installé",
            fix=None if ok else "install_iis", fix_label=None if ok else f"Installer les rôles IIS ({len(missing)})")
    modules = state.get("modules") or {}
    for key, mod in iis.MODULES.items():
        ok = bool(modules.get(key))
        add(f"module:{key}", "IIS", mod["label"], "ok" if ok else "error",
            "Installé" if ok else "Non installé (téléchargement Microsoft, ou fichier "
                                  f"{mod['msi']} déposé dans le dossier prerequis\\ hors ligne)",
            fix=None if ok else f"install_{key}", fix_label=None if ok else "Installer",
            help=None if ok else mod["url"])
    try:
        certs = [c for c in system.certificates() if not c.get("expired")]
    except SetupError:
        certs = []
    add("certs", "IIS", "Certificat HTTPS (magasin de l'ordinateur)", "ok" if certs else "warning",
        f"{len(certs)} certificat(s) avec clé privée disponible(s)" if certs else
        "Aucun certificat avec clé privée : en demander un à l'AC d'entreprise puis l'importer "
        "dans « Ordinateur local › Personnel »")
    return out


def apply_fix(system: System, fix: str) -> str:
    """Exécute une correction automatique ; renvoie un message de résultat."""
    if fix == "install_iis":
        state = system.iis_state() or {}
        features = state.get("features") or {}
        names = [f[0] for f in iis.FEATURES if not features.get(f[0])]
        if not names:
            return "Rôles IIS déjà installés"
        res = system.install_iis_features(names)
        return "Rôles IIS installés" + (" (redémarrage du serveur nécessaire)" if res.get("restart") else "")
    if fix in ("install_rewrite", "install_arr"):
        key = fix.split("_", 1)[1]
        state = system.iis_state() or {}
        if not (state.get("features") or {}).get("Web-Server"):
            raise SetupError("Installer d'abord les rôles IIS")
        system.install_module(key)
        return f"{iis.MODULES[key]['label']} installé"
    raise SetupError(f"Correction inconnue : {fix}")
