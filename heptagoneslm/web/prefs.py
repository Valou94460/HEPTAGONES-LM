"""Préférences par utilisateur et checklist de démarrage de l'administrateur.

* **Visite guidée** : à la première connexion d'un utilisateur, l'interface propose une visite en 5 étapes
  adaptée à son rôle. Le fait de l'avoir vue (ou passée) est mémorisé côté serveur, par identifiant
  (table ``user_pref``), pour ne pas la relancer d'un poste à l'autre. ``TOUR_VERSION`` est augmentée
  quand la visite change assez pour être reproposée.
* **Checklist de démarrage** (administrateur) : ce qu'il reste à renseigner pour que les rapports soient
  complets (serveurs, coûts, échéances, projets, e-mail). Une page vide est presque toujours une donnée
  manquante : la checklist le dit et renvoie à l'Administration.
"""

from __future__ import annotations

import fnmatch
from datetime import datetime

from heptagoneslm.storage.db import Database
from heptagoneslm.storage.repository import Repository

TOUR_VERSION = 1


def get(db: Database, username: str) -> dict:
    row = None
    if Repository(db).has_table("user_pref"):
        rows = db.query("SELECT tour_version, onboarded_at, language FROM user_pref WHERE username = ?", [username.lower()])
        row = rows[0] if rows else None
    done = bool(row and row[1] is not None and (row[0] or 0) >= TOUR_VERSION)
    return {"onboarding_done": done, "tour_version": TOUR_VERSION, "language": row[2] if row else None}


def set_onboarding(db: Database, username: str, done: bool) -> dict:
    key, now = username.lower(), datetime.now().replace(microsecond=0)
    with db.transaction():
        exists = db.query("SELECT 1 FROM user_pref WHERE username = ?", [key])
        values = (TOUR_VERSION if done else 0, now if done else None, now)
        if exists:
            db.execute("UPDATE user_pref SET tour_version = ?, onboarded_at = ?, updated_at = ? WHERE username = ?",
                       [*values, key])
        else:
            db.insert("user_pref", {"username": key, "tour_version": values[0], "onboarded_at": values[1],
                                    "updated_at": values[2]})
    return get(db, username)


def checklist(config, db: Database, meta: dict) -> dict:
    """État de la configuration : {items: [{id, label, ok, optional, detail, action}], done, total}."""
    from heptagoneslm.autodesk import annual_cost
    from heptagoneslm.web import api

    repo = Repository(db)
    items: list[dict] = []

    def add(id_, label, ok, detail, action=None, optional=False):
        items.append({"id": id_, "label": label, "ok": bool(ok), "optional": optional, "detail": detail, "action": action})

    enabled = [s for s in config.servers if s.enabled]
    collections = api.collections_status(db)
    failing = sorted({c["server"] for c in collections if c["status"] == "ERROR"})
    if not enabled:
        add("servers", "Serveurs de licences", False, "Aucun serveur de licences actif : rien n'est collecté.", "servers")
    elif failing:
        add("servers", "Serveurs de licences", False, f"{len(enabled)} serveur(s) déclaré(s), collecte en échec : {', '.join(failing)}.", "servers")
    elif not collections:
        add("servers", "Serveurs de licences", False, f"{len(enabled)} serveur(s) déclaré(s), aucune collecte encore enregistrée.", "servers")
    else:
        add("servers", "Serveurs de licences", True, f"{len(enabled)} serveur(s) déclaré(s), dernière collecte sans erreur.", "servers")

    names = [p["name"] for p in meta.get("products", [])]
    costs = config.product_costs()
    priced = [n for n in names if n in costs or annual_cost(n, config.autodesk.annual_costs) is not None]
    if names and len(priced) == len(names):
        add("costs", "Coûts annuels", True, f"Les {len(names)} produits sont chiffrés.", "costs")
    else:
        add("costs", "Coûts annuels", False, (f"{len(priced)} produit(s) sur {len(names)} chiffré(s) : les économies sont sous-estimées."
                                              if names else "Aucun produit connu pour l'instant."), "costs")

    dated = 0
    if repo.has_column("feature", "expires_on"):
        dated = int(db.scalar("SELECT COUNT(DISTINCT COALESCE(product, name)) FROM feature WHERE expires_on IS NOT NULL") or 0)
    dated += len(config.product_renewals()) + len(config.autodesk.renewals)
    adsk = [p["name"] for p in meta.get("products", []) if "Autodesk Cloud" in p.get("servers", [])]
    adsk_missing = [n for n in adsk if not any(fnmatch.fnmatch(n.lower(), str(k).lower()) for k in config.autodesk.renewals)]
    if dated and not adsk_missing:
        add("renewals", "Échéances de renouvellement", True, "Des échéances sont connues : la page Renouvellements est alimentée.", "costs")
    elif dated:
        add("renewals", "Échéances de renouvellement", False,
            f"{len(adsk_missing)} produit(s) Autodesk sans fin d'abonnement : leur renouvellement n'apparaît pas.", "costs")
    else:
        add("renewals", "Échéances de renouvellement", False, "Aucune échéance connue : la page Renouvellements reste vide.", "costs")

    assigned = 0
    if repo.has_table("user_project"):
        assigned = int(db.scalar("SELECT COUNT(*) FROM user_project") or 0)
    if assigned or config.projects:
        add("projects", "Projets", True, (f"{assigned} utilisateur(s) affecté(s) à un projet" if assigned else "Règles de projet déclarées") + ".", "projects")
    else:
        add("projects", "Projets", False, "Aucune affectation importée : la répartition par projet est vide.", "projects")

    add("smtp", "Serveur e-mail (SMTP)", bool(config.email.smtp_host),
        "Configuré : alertes et rapports peuvent partir." if config.email.smtp_host
        else "Non configuré : aucune alerte ni rapport par e-mail (à régler dans HeptagonesLM-Setup.exe).", None)
    subs = len(config.email.subscriptions)
    add("reports", "Rapports planifiés", subs > 0, f"{subs} rapport(s) planifié(s)." if subs else "Aucun rapport planifié (facultatif).",
        "reports", optional=True)
    required = [i for i in items if not i["optional"]]
    return {"items": items, "done": sum(1 for i in required if i["ok"]), "total": len(required)}
