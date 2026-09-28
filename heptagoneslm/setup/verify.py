"""Vérification de l'installation : chaque composant est contrôlé de bout en bout."""

from __future__ import annotations

import os
import re
import socket
from datetime import datetime, timedelta

import yaml

from heptagoneslm.config import ConfigError, parse_config
from heptagoneslm.setup.answers import Answers
from heptagoneslm.setup.checks import Check
from heptagoneslm.setup.installer import WEB_TASK, Context, _tasks, backend_url, firewall_rule_name
from heptagoneslm.setup.system import SetupError, System

NEVER_RUN = 267011        # SCHED_S_TASK_HAS_NOT_RUN
RUNNING = 267009          # SCHED_S_TASK_RUNNING


def db_status(system: System, a: Answers) -> dict:
    """Version du schéma et dernières collectes par serveur."""
    if system.simulated:
        collected = system.db.get("collected")
        return {"schema": system.db.get("schema"),
                "servers": {s.name: {"status": "OK", "at": datetime.now().strftime("%d/%m %H:%M"), "message": None}
                            for s in a.servers} if collected else {}}
    from heptagoneslm.storage import Database

    cs = a.connection_string()
    for var, value in a.machine_secrets().items():
        cs = cs.replace("${" + var + "}", value)
    db = Database(cs)
    try:
        schema = db.query_dicts("SELECT MAX(version) AS v FROM schema_info")[0]["v"]
        rows = db.query_dicts(
            "SELECT s.name AS server, r.status, r.started_at, r.message FROM collection_run r "
            "JOIN license_server s ON s.id = r.server_id "
            "WHERE r.id IN (SELECT MAX(id) FROM collection_run GROUP BY server_id, collector)")
    finally:
        db.close()
    servers = {}
    for r in rows:
        prev = servers.get(r["server"])
        if prev is None or prev["status"] == "OK":        # une erreur l'emporte
            at = r["started_at"]
            servers[r["server"]] = {"status": r["status"], "message": r["message"],
                                    "at": at.strftime("%d/%m %H:%M") if hasattr(at, "strftime") else str(at)[:16]}
    return {"schema": schema, "servers": servers}


def verify(system: System, a: Answers) -> dict:
    out: list[Check] = []

    def add(*args, **kw):
        out.append(Check(*args, **kw))

    # -- Fichiers et configuration --------------------------------------------------------
    exe, prefix = system.runtime_command(a)
    if not prefix:
        add("exe", "Fichiers", "Exécutable HeptagonesLM", "ok" if system.exists(exe) else "error", exe)
    text = system.read_text(a.config_path)
    if text is None:
        add("config", "Fichiers", "Configuration heptagoneslm.yaml", "error", f"{a.config_path} introuvable")
    else:
        try:
            parse_config(yaml.safe_load(text))
            add("config", "Fichiers", "Configuration heptagoneslm.yaml", "ok", a.config_path)
        except (ConfigError, yaml.YAMLError) as exc:
            add("config", "Fichiers", "Configuration heptagoneslm.yaml", "error", str(exc))

    # -- Base de données et collecte ----------------------------------------------------------
    try:
        st = db_status(system, a)
        schema = st.get("schema")
        add("schema", "Base de données", "Schéma de la base", "ok" if schema and schema >= 3 else "error",
            f"version {schema}" if schema else "schéma absent : relancer l'initialisation")
        for s in a.servers:
            info = st["servers"].get(s.name)
            if not info:
                add(f"collect:{s.name}", "Collecte", f"Serveur {s.name}", "warning", "Aucune collecte enregistrée")
            elif info["status"] == "OK":
                add(f"collect:{s.name}", "Collecte", f"Serveur {s.name}", "ok", f"Collecte réussie ({info['at']})")
            else:
                add(f"collect:{s.name}", "Collecte", f"Serveur {s.name}", "error",
                    f"Dernière collecte en échec ({info['at']}) : {info['message'] or 'voir le journal'}")
    except Exception as exc:
        add("schema", "Base de données", "Connexion à la base", "error", f"{exc}")

    # -- Tâches planifiées -----------------------------------------------------------------
    ctx = Context(system, a, lambda *x: None, lambda f: None)
    for spec in _tasks(ctx):
        try:
            info = system.task_info(spec.name)
        except SetupError as exc:
            info, detail = None, str(exc)
        if not info:
            add(f"task:{spec.name}", "Tâches planifiées", spec.name, "error", "Tâche absente")
            continue
        state, result = info.get("state"), info.get("last_result")
        detail = f"{state} · compte {info.get('user') or a.service_account}"
        if state == "Disabled":
            add(f"task:{spec.name}", "Tâches planifiées", spec.name, "error", detail + " : tâche désactivée")
        elif result in (0, NEVER_RUN, RUNNING, None):
            suffix = {0: " · dernier résultat OK", NEVER_RUN: " · pas encore exécutée", RUNNING: " · en cours"}.get(result, "")
            add(f"task:{spec.name}", "Tâches planifiées", spec.name, "ok", detail + suffix)
        else:
            add(f"task:{spec.name}", "Tâches planifiées", spec.name, "warning",
                detail + f" · dernier résultat 0x{int(result) & 0xFFFFFFFF:X}")

    # -- Interface web ---------------------------------------------------------------------
    url, sni = backend_url(a)
    code = system.http_status(url, sni_host=sni)
    add("web", "Interface web", f"HeptagonesLM (port {a.web_port})", "ok" if code == 200 else "error",
        "Répond" if code == 200 else f"Aucune réponse ({code or 'connexion refusée'}) : voir logs\\heptagoneslm.log")
    if a.web_mode == "iis":
        try:
            site = system.site_state(a.iis_site)
        except SetupError as exc:
            site = {"exists": False, "error": str(exc)}
        if not site.get("exists"):
            add("iis_site", "IIS", f"Site « {a.iis_site} »", "error", site.get("error") or "Site absent")
        else:
            add("iis_site", "IIS", f"Site « {a.iis_site} »", "ok" if site.get("state") == "Started" else "error",
                f"{site.get('state')} · " + ", ".join(site.get("bindings") or []))
            probe = system.http_status(f"https://127.0.0.1:{a.iis_port}/", sni_host=a.public_host)
            if probe in (200, 401):
                add("iis_chain", "IIS", "HTTPS et authentification Windows", "ok",
                    "IIS demande l'authentification Windows puis relaie vers HeptagonesLM" if probe == 401 else "Page servie")
            elif probe == 502:
                add("iis_chain", "IIS", "HTTPS et authentification Windows", "error",
                    "502 : IIS ne joint pas HeptagonesLM (proxy ARR, port interne)")
            else:
                add("iis_chain", "IIS", "HTTPS et authentification Windows", "error",
                    f"Réponse inattendue : {probe or 'aucune'} (liaison HTTPS, certificat, web.config)")
    if a.open_firewall and a.web_mode != "local":
        try:
            ok = system.firewall_exists(firewall_rule_name(a))
        except SetupError:
            ok = False
        add("firewall", "Réseau", "Règle de pare-feu", "ok" if ok else "warning",
            firewall_rule_name(a) if ok else "Règle absente : ouvrir le port manuellement")
    if a.public_host and a.web_mode != "local":
        try:
            ip = socket.gethostbyname(a.public_host) if not system.simulated else "10.1.2.3"
            add("dns", "Réseau", f"Nom DNS {a.public_host}", "ok", f"résout en {ip}")
        except OSError:
            add("dns", "Réseau", f"Nom DNS {a.public_host}", "warning",
                "Ne résout pas : créer l'enregistrement DNS (A ou CNAME) vers ce serveur")

    # -- Authentification unique et tests depuis les postes ---------------------------------
    from heptagoneslm.setup import sso
    if a.web_mode == "iis" and a.public_host:
        try:
            info = system.os_info()
        except SetupError:
            info = None
        out.extend(c for c in (sso.spn_check(system, a, info), sso.zone_check(system, a)) if c)
    out.extend(sso.access_checks(system, a))

    counts = {k: sum(1 for c in out if c.status == k) for k in ("ok", "warning", "error")}
    access_url = a.public_url.rstrip("/") + "/acces" if a.public_url and a.web_mode != "local" else None
    return {"checks": [c.to_dict() for c in out], "counts": counts, "success": counts["error"] == 0,
            "url": a.public_url, "access_url": access_url}
