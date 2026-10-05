"""Administration depuis l'interface web (rôle admin, ou poste local sans authentification).

* serveurs de licences : liste avec l'état de la dernière collecte, ajout, modification, suppression
  et test en direct (lmstat / rlmstat) ; seuls le nom, l'adresse ``port@serveur``, le type et l'état
  actif sont modifiables : les chemins des outils (lmutil, rlmutil) et les journaux restent réservés
  à l'assistant d'installation, pour qu'aucun programme ne puisse être désigné depuis le navigateur ;
* import d'un export « Usage report » du portail Autodesk Account (CSV ou Excel), sans passer par
  le dossier surveillé ;
* coût annuel et échéance des abonnements Autodesk (``autodesk.annual_costs``, ``autodesk.renewals``) ;
* affectation des utilisateurs aux projets (import CSV, table ``user_project``) ;
* rapports planifiés par e-mail (``email.subscriptions``) : vues, filtres, période, destinataires,
  avec un envoi d'essai.

``heptagoneslm.yaml`` est réécrit en place (le compte de service n'a le droit de modifier que ce
fichier, accordé par l'assistant quand un groupe d'administrateurs est déclaré), après validation
complète ; l'ancienne version est sauvegardée dans le dossier des journaux. La collecte suivante
(toutes les 5 minutes) relit la configuration.
"""

from __future__ import annotations

import copy
import os
import re
from datetime import date, datetime
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
        return ("Le compte de service ne peut pas modifier heptagoneslm.yaml : relancer HeptagonesLM-Setup.exe et "
                "« Réappliquer la mise à jour » (droits mis à jour), ou déclarer un groupe d'administrateurs (accueil › "
                "« Administrateurs de l'interface web »)")
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


def list_costs(path: str, products: list[str]) -> dict:
    """Coût annuel et échéance d'une licence nommée par produit Autodesk (autodesk.annual_costs, autodesk.renewals)."""
    _, raw = _load(path)
    section = raw.get("autodesk") if isinstance(raw.get("autodesk"), dict) else {}
    costs = section.get("annual_costs") or {}
    renewals = section.get("renewals") or {}
    names = sorted(set(products) | {str(k) for k in costs} | {str(k) for k in renewals})
    return {"costs": [{"product": n, "cost": costs.get(n),
                       "renewal": str(renewals[n])[:10] if renewals.get(n) else None} for n in names],
            "readonly": editable(path)}


def save_costs(path: str, costs: dict, backup_dir: str | None, renewals: dict | None = None) -> int:
    reason = editable(path)
    if reason:
        raise AdminError(reason)
    dates = {}
    for product, value in (renewals or {}).items():
        name = str(product).strip()
        if not name or len(name) > 255:
            raise AdminError("Nom de produit invalide")
        if value in (None, ""):
            continue
        try:
            dates[name] = date.fromisoformat(str(value).strip()[:10]).isoformat()
        except ValueError:
            raise AdminError(f"Échéance invalide pour {name} : date AAAA-MM-JJ attendue") from None
    clean = {}
    for product, value in (costs or {}).items():
        name = str(product).strip()
        if not name or len(name) > 255:
            raise AdminError("Nom de produit invalide")
        if value in (None, ""):
            continue                                  # champ vidé : coût retiré
        try:
            amount = round(float(str(value).replace(",", ".").replace(" ", "")), 2)
        except ValueError:
            raise AdminError(f"Coût invalide pour {name} : nombre attendu") from None
        if not 0 <= amount <= 10_000_000:
            raise AdminError(f"Coût hors limites pour {name}")
        clean[name] = amount
    previous, raw = _load(path)
    section = raw.get("autodesk") if isinstance(raw.get("autodesk"), dict) else {}
    section = dict(section)
    if clean:
        section["annual_costs"] = clean
    else:
        section.pop("annual_costs", None)
    if renewals is not None:
        if dates:
            section["renewals"] = dates
        else:
            section.pop("renewals", None)
    if section:
        raw["autodesk"] = section
    else:
        raw.pop("autodesk", None)
    _write(path, previous, raw, backup_dir)
    return len(clean)


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


def import_projects(db, filename: str, data: bytes, replace: bool) -> dict:
    """Affectation des utilisateurs aux projets déposée depuis le navigateur (CSV utilisateur ; projet)."""
    from heptagoneslm.projects import import_projects as run

    name = Path(filename or "").name
    if not name or Path(name).suffix.lower() not in (".csv", ".txt"):
        raise AdminError("Fichier attendu : CSV avec les colonnes utilisateur et projet")
    if not data:
        raise AdminError("Fichier vide")
    try:
        return run(db, name, data, replace=replace)
    except ValueError as exc:
        raise AdminError(f"Fichier non reconnu : {exc}") from None


# ---------------------------------------------------------------------------
# Rapports planifiés (email.subscriptions)
# ---------------------------------------------------------------------------

PERIOD_LABELS = {"last-month": "Mois précédent (mensuel)", "last-week": "Semaine précédente (hebdomadaire)",
                 "last-quarter": "Trimestre précédent (trimestriel)", "last-year": "Année précédente (annuel)",
                 "this-month": "Mois en cours (quotidien)", "last-7d": "7 derniers jours (quotidien)"}
SUB_NAME_RE = re.compile(r"^[^\x00-\x1f]{1,100}$")
EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


def list_subscriptions(path: str) -> dict:
    from heptagoneslm.config import SUBSCRIPTION_DIMENSIONS, SUBSCRIPTION_VIEWS
    from heptagoneslm.web import api

    _, raw = _load(path)
    email = raw.get("email") if isinstance(raw.get("email"), dict) else {}
    subs = []
    for s in email.get("subscriptions") or []:
        if isinstance(s, dict):
            subs.append({k: s.get(k) for k in ("name", "recipients", "period", "views", "servers", "products", "users",
                                               "departments", "sites", "dimension", "attach_excel", "subject")})
    return {"subscriptions": subs, "smtp": bool(email.get("smtp_host")), "readonly": editable(path),
            "views": [{"key": v, "label": api.REPORTS[v][0]} for v in SUBSCRIPTION_VIEWS],
            "periods": [{"key": k, "label": v} for k, v in PERIOD_LABELS.items()],
            "dimensions": [{"key": d, "label": api.DIMENSIONS[d]} for d in SUBSCRIPTION_DIMENSIONS]}


def _list(value) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        value = re.split(r"[;,\n]", value)
    return [str(v).strip() for v in value if str(v).strip()]


def _clean_subscription(entry: dict) -> dict:
    from heptagoneslm.config import PERIODS, SUBSCRIPTION_DIMENSIONS, SUBSCRIPTION_VIEWS

    name = str(entry.get("name", "")).strip()
    if not SUB_NAME_RE.match(name):
        raise AdminError("Nom du rapport attendu (100 caractères au plus)")
    recipients = _list(entry.get("recipients"))
    if not recipients:
        raise AdminError("Indiquer au moins un destinataire")
    bad = [r for r in recipients if not EMAIL_RE.match(r)]
    if bad:
        raise AdminError(f"Adresse e-mail invalide : {bad[0]}")
    period = str(entry.get("period") or "last-month")
    if period not in PERIODS:
        raise AdminError("Période inconnue")
    views = [v for v in _list(entry.get("views")) if v in SUBSCRIPTION_VIEWS]
    dimension = str(entry.get("dimension") or "department")
    if dimension not in SUBSCRIPTION_DIMENSIONS:
        raise AdminError("Axe d'analyse inconnu")
    out = {"name": name, "recipients": recipients, "period": period}
    if views:
        out["views"] = views
        if dimension != "department":
            out["dimension"] = dimension
    for key in ("servers", "products", "users", "departments", "sites"):
        values = _list(entry.get(key))
        if values:
            out[key] = values
    if entry.get("attach_excel") is False:
        out["attach_excel"] = False
    subject = str(entry.get("subject") or "").strip()
    if subject:
        out["subject"] = subject[:200]
    return out


def subscription_from_entry(path: str, entry: dict):
    """Abonnement validé (sans l'enregistrer) : sert à l'envoi d'essai."""
    from heptagoneslm.config import parse_config

    clean = _clean_subscription(entry)
    _, raw = _load(path)
    probe = copy.deepcopy(raw)
    probe.setdefault("email", {})
    if not isinstance(probe["email"], dict) or not probe["email"].get("smtp_host"):
        raise AdminError("Serveur SMTP non configuré (HeptagonesLM-Setup.exe › e-mail) : envoi impossible")
    probe["email"]["subscriptions"] = [clean]
    try:
        return parse_config(probe).email.subscriptions[0]
    except ConfigError as exc:
        raise AdminError(str(exc)) from None


def save_subscription(path: str, entry: dict, original: str | None, backup_dir: str | None) -> dict:
    reason = editable(path)
    if reason:
        raise AdminError(reason)
    new = _clean_subscription(entry)
    previous, raw = _load(path)
    email = raw.get("email") if isinstance(raw.get("email"), dict) else None
    if not email or not email.get("smtp_host"):
        raise AdminError("Serveur SMTP non configuré (HeptagonesLM-Setup.exe › e-mail) : les rapports ne peuvent pas être envoyés")
    subs = list(email.get("subscriptions") or [])
    names = [str(s.get("name", "")).lower() for s in subs]
    if original:
        if original.lower() not in names:
            raise AdminError(f"Rapport inconnu : {original}")
        if new["name"].lower() != original.lower() and new["name"].lower() in names:
            raise AdminError(f"Un rapport s'appelle déjà {new['name']}")
        subs[names.index(original.lower())] = new
    else:
        if new["name"].lower() in names:
            raise AdminError(f"Un rapport s'appelle déjà {new['name']}")
        subs.append(new)
    email["subscriptions"] = subs
    _write(path, previous, raw, backup_dir)
    return new


def delete_subscription(path: str, name: str, backup_dir: str | None) -> None:
    reason = editable(path)
    if reason:
        raise AdminError(reason)
    previous, raw = _load(path)
    email = raw.get("email") if isinstance(raw.get("email"), dict) else {}
    subs = list(email.get("subscriptions") or [])
    rest = [s for s in subs if str(s.get("name", "")).lower() != name.lower()]
    if len(rest) == len(subs):
        raise AdminError(f"Rapport inconnu : {name}")
    if rest:
        email["subscriptions"] = rest
    else:
        email.pop("subscriptions", None)
    _write(path, previous, raw, backup_dir)
