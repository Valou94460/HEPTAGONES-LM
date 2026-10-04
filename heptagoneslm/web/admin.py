"""Administration depuis l'interface web (rôle admin, ou poste local sans authentification).

* serveurs de licences : liste avec l'état de la dernière collecte, ajout, modification, suppression
  et test en direct (lmstat / rlmstat) ; seuls le nom, l'adresse ``port@serveur``, le type et l'état
  actif sont modifiables : les chemins des outils (lmutil, rlmutil) et les journaux restent réservés
  à l'assistant d'installation, pour qu'aucun programme ne puisse être désigné depuis le navigateur ;
* import d'un export « Usage report » du portail Autodesk Account (CSV ou Excel), sans passer par
  le dossier surveillé.

``heptagoneslm.yaml`` est réécrit en place (le compte de service n'a le droit de modifier que ce
fichier, accordé par l'assistant quand un groupe d'administrateurs est déclaré), après validation
complète ; l'ancienne version est sauvegardée dans le dossier des journaux. La collecte suivante
(toutes les 5 minutes) relit la configuration.
"""

from __future__ import annotations

import copy
import os
import re
from datetime import datetime
from pathlib import Path

import yaml

from heptagoneslm.config import ConfigError, parse_config

NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,40}$")
PORT_AT_HOST_RE = re.compile(r"^\d{1,5}@[A-Za-z0-9_.\-]{1,253}$")
LICENSE_TYPES = ("flexlm", "rlm")
UPLOAD_MAX = 25 * 1024 * 1024
UPLOAD_EXTENSIONS = (".csv", ".xlsx", ".txt")


class AdminError(ValueError):
    """Saisie refusée ou configuration non modifiable (message affiché tel quel)."""


def _load(path: str) -> tuple[str, dict]:
    text = Path(path).read_text(encoding="utf-8")
    raw = yaml.safe_load(text) or {}
    if not isinstance(raw, dict):
        raise AdminError("heptagoneslm.yaml illisible")
    return text, raw


def editable(path: str | None) -> str | None:
    """None si la configuration peut être modifiée, sinon la raison."""
    if not path:
        return "Configuration inconnue (interface lancée sans fichier de configuration)"
    if not os.access(path, os.W_OK):
        return ("Le compte de service ne peut pas modifier heptagoneslm.yaml : déclarer un groupe d'administrateurs "
                "dans HeptagonesLM-Setup.exe (accueil › « Administrateurs de l'interface web »)")
    return None


def list_servers(path: str, collections: list[dict]) -> dict:
    _, raw = _load(path)
    last: dict[str, dict] = {}
    for c in collections:      # dernière collecte : collecteur d'état (lmstat / rlmstat) de préférence
        prev = last.get(c["server"])
        state = c.get("collector") in ("lmstat", "rlmstat")
        if prev is None or (state and prev.get("collector") not in ("lmstat", "rlmstat")) \
                or (state == (prev.get("collector") in ("lmstat", "rlmstat")) and (c.get("last_run") or "") > (prev.get("last_run") or "")):
            last[c["server"]] = c
    servers = []
    for s in raw.get("servers") or []:
        c = last.get(s.get("name"), {})
        servers.append({"name": s.get("name"), "port_at_host": s.get("port_at_host"),
                        "license_type": str(s.get("license_type", "flexlm")).lower(),
                        "enabled": s.get("enabled", True) is not False,
                        "status": c.get("status"), "last_run": c.get("last_run"), "message": c.get("message")})
    return {"servers": servers, "readonly": editable(path)}


def _clean(entry: dict) -> dict:
    name = str(entry.get("name", "")).strip()
    target = str(entry.get("port_at_host", "")).strip()
    kind = str(entry.get("license_type", "flexlm")).strip().lower()
    if not NAME_RE.match(name):
        raise AdminError("Nom attendu : lettres, chiffres, « _ », « - » ou « . » (40 caractères au plus)")
    if not PORT_AT_HOST_RE.match(target):
        raise AdminError("Adresse attendue au format port@serveur, par exemple 27000@licsrv01")
    if kind not in LICENSE_TYPES:
        raise AdminError("Type attendu : flexlm ou rlm")
    return {"name": name, "port_at_host": target, "license_type": kind, "enabled": entry.get("enabled", True) is not False}


def _write(path: str, previous: str, raw: dict, backup_dir: str | None) -> None:
    try:
        parse_config(raw)
    except ConfigError as exc:
        raise AdminError(f"Configuration refusée : {exc}") from None
    if backup_dir:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        Path(backup_dir).mkdir(parents=True, exist_ok=True)
        (Path(backup_dir) / f"heptagoneslm.yaml.{stamp}.bak").write_text(previous, encoding="utf-8")
    head = []
    for line in previous.splitlines():         # commentaires d'en-tête conservés
        if not line.startswith("#"):
            break
        head.append(line)
    body = yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, default_flow_style=False)
    Path(path).write_text(("\n".join(head) + "\n\n" if head else "") + body, encoding="utf-8")


def save_server(path: str, entry: dict, original: str | None, backup_dir: str | None) -> dict:
    """Ajoute (``original`` vide) ou modifie le serveur ``original``."""
    reason = editable(path)
    if reason:
        raise AdminError(reason)
    new = _clean(entry)
    previous, raw = _load(path)
    servers = list(raw.get("servers") or [])
    names = [str(s.get("name", "")).lower() for s in servers]
    if original:
        if original.lower() not in names:
            raise AdminError(f"Serveur inconnu : {original}")
        i = names.index(original.lower())
        if new["name"].lower() != original.lower() and new["name"].lower() in names:
            raise AdminError(f"Un serveur s'appelle déjà {new['name']}")
        kept = copy.deepcopy(servers[i])        # collecteurs, journal, chemins d'outils : inchangés
        if kept.get("license_type", "flexlm") != new["license_type"]:
            for key in ("collectors", "sessions_from", "log_path"):
                kept.pop(key, None)             # changement de type : collecteurs par défaut du nouveau type
        kept.update({k: v for k, v in new.items() if k != "enabled"})
        if new["enabled"]:
            kept.pop("enabled", None)
        else:
            kept["enabled"] = False
        if kept.get("license_type") == "flexlm":
            kept.pop("license_type")
        servers[i] = kept
    else:
        if new["name"].lower() in names:
            raise AdminError(f"Un serveur s'appelle déjà {new['name']}")
        item = {"name": new["name"], "port_at_host": new["port_at_host"]}
        if new["license_type"] != "flexlm":
            item["license_type"] = new["license_type"]
        if not new["enabled"]:
            item["enabled"] = False
        servers.append(item)
    raw["servers"] = servers
    _write(path, previous, raw, backup_dir)
    return new


def delete_server(path: str, name: str, backup_dir: str | None) -> None:
    reason = editable(path)
    if reason:
        raise AdminError(reason)
    previous, raw = _load(path)
    servers = list(raw.get("servers") or [])
    rest = [s for s in servers if str(s.get("name", "")).lower() != name.lower()]
    if len(rest) == len(servers):
        raise AdminError(f"Serveur inconnu : {name}")
    if not rest:
        raise AdminError("Au moins un serveur de licences doit rester déclaré")
    raw["servers"] = rest
    _write(path, previous, raw, backup_dir)


def test_server(path: str, entry: dict) -> dict:
    """Interroge le serveur (lmstat / rlmstat) avec les outils de la configuration, sans rien écrire."""
    from heptagoneslm.collectors import get_collector_class

    new = _clean(entry)
    _, raw = _load(path)
    probe = copy.deepcopy(raw)
    probe["servers"] = [{"name": new["name"], "port_at_host": new["port_at_host"], "license_type": new["license_type"]}]
    try:
        cfg = parse_config(probe)
    except ConfigError as exc:
        raise AdminError(str(exc)) from None
    srv = cfg.servers[0]
    try:
        status = get_collector_class(srv.license_type, srv.collectors[0])(srv, cfg).poll()
    except Exception as exc:                     # outil absent, délai dépassé…
        return {"ok": False, "message": str(exc)[:500]}
    if not status.server_up:
        return {"ok": False, "message": "Serveur de licences injoignable (vérifier port@serveur, pare-feu, démon éditeur)"}
    used = sum(f.in_use for f in status.features)
    issued = sum(f.issued or 0 for f in status.features)
    return {"ok": True, "message": f"Serveur joignable : {len(status.features)} feature(s), {used} / {issued} jetons utilisés",
            "features": [{"name": f.name, "in_use": f.in_use, "issued": f.issued} for f in status.features[:50]]}


def import_autodesk(db, filename: str, data: bytes) -> dict:
    """Export « Usage report » du portail Autodesk Account déposé depuis le navigateur."""
    from heptagoneslm.autodesk import import_report

    name = Path(filename or "").name
    if not name or Path(name).suffix.lower() not in UPLOAD_EXTENSIONS:
        raise AdminError("Fichier attendu : export CSV ou Excel (.xlsx) du rapport d'utilisation Autodesk")
    if not data:
        raise AdminError("Fichier vide")
    try:
        return import_report(db, name, data=data)
    except ValueError as exc:                     # colonnes introuvables, format inattendu
        raise AdminError(f"Export non reconnu : {exc}") from None
