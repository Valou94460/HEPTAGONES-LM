"""Projets : affectation des sessions de licence à un projet, pour la refacturation par projet.

Le projet d'une session est, dans l'ordre :

1. celui de l'utilisateur dans la table ``user_project``, alimentée par un import CSV
   (``heptagoneslm import-projects``, ou Administration › Projets) : export des membres d'un groupe
   AD, d'un outil de gestion de projets ou d'un fichier RH ;
2. la première règle ``projects`` de la configuration qui correspond : motifs sur le nom du poste
   (``hosts``), de l'utilisateur (``users``) ou nom du service (``departments``).

Sans correspondance, la session est rangée sous « (non renseigné) ». Exemple d'export des membres
d'un groupe AD (PowerShell) ::

    Get-ADGroupMember "GG-Projet-A12" -Recursive |
      Select-Object SamAccountName, @{n="project"; e={"A12 Viaduc"}} |
      Export-Csv projets.csv -NoTypeInformation -Encoding UTF8
"""

from __future__ import annotations

import csv
import fnmatch
import io
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ALIASES = {
    "username": {"username", "utilisateur", "login", "samaccountname", "user", "identifiant"},
    "project": {"project", "projet", "affaire", "code projet", "code_projet"},
}


@dataclass
class ProjectRule:
    project: str
    hosts: list[str] = field(default_factory=list)          # motifs fnmatch, insensibles à la casse
    users: list[str] = field(default_factory=list)
    departments: list[str] = field(default_factory=list)    # noms exacts (insensibles à la casse)

    def matches(self, username: str | None, hostname: str | None, department: str | None) -> bool:
        def any_pattern(value, patterns):
            return bool(value) and any(fnmatch.fnmatchcase(value.lower(), p.lower()) for p in patterns)

        return (any_pattern(hostname, self.hosts) or any_pattern(username, self.users)
                or bool(department) and department.lower() in {d.lower() for d in self.departments})


class Resolver:
    """Projet d'une session : import CSV d'abord, puis règles de la configuration."""

    def __init__(self, rules: list[ProjectRule] | None, assigned: dict[str, str] | None = None):
        self.rules = rules or []
        self.assigned = {k.lower(): v for k, v in (assigned or {}).items()}

    def __bool__(self) -> bool:
        return bool(self.rules or self.assigned)

    def of(self, username: str | None, hostname: str | None = None, department: str | None = None) -> str | None:
        if username and username.lower() in self.assigned:
            return self.assigned[username.lower()]
        for rule in self.rules:
            if rule.matches(username, hostname, department):
                return rule.project
        return None


def resolver(db, rules: list[ProjectRule] | None) -> Resolver:
    from heptagoneslm.storage.repository import Repository

    assigned = {}
    if Repository(db).has_table("user_project"):
        assigned = {r[0]: r[1] for r in db.query("SELECT username, project FROM user_project")}
    return Resolver(rules, assigned)


def read_projects_csv(data: bytes) -> list[tuple[str, str]]:
    """[(utilisateur, projet)] d'un CSV (en-tête obligatoire, séparateur « ; » ou « , » détecté)."""
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("Encodage du fichier non reconnu (UTF-8 ou Windows-1252 attendu)")
    first = text.splitlines()[0] if text else ""
    reader = csv.DictReader(io.StringIO(text), delimiter=";" if first.count(";") >= first.count(",") else ",")
    mapping: dict[str, str] = {}
    for col in reader.fieldnames or []:
        norm = col.strip().strip('"').lower()
        for target, names in ALIASES.items():
            if norm in names and target not in mapping:
                mapping[target] = col
    missing = [k for k in ALIASES if k not in mapping]
    if missing:
        raise ValueError("Colonnes attendues : utilisateur (" + ", ".join(sorted(ALIASES["username"])) + ") et projet ("
                         + ", ".join(sorted(ALIASES["project"])) + ")")
    out = []
    for row in reader:
        user = (row.get(mapping["username"]) or "").strip().lower()
        project = (row.get(mapping["project"]) or "").strip()
        if user and project:
            out.append((user[:255], project[:255]))
    if not out:
        raise ValueError("Aucune ligne utilisateur ; projet dans le fichier")
    return out


def import_projects(db, filename: str, data: bytes, replace: bool = False) -> dict:
    """Enregistre les affectations ; ``replace`` efface d'abord les affectations existantes."""
    pairs = read_projects_csv(data)
    now = datetime.now().replace(microsecond=0)
    name = Path(filename or "projets.csv").name[:255]
    latest = dict(pairs)                     # un utilisateur listé deux fois : la dernière ligne l'emporte
    with db.transaction():
        if replace:
            db.execute("DELETE FROM user_project")
        existing = {r[0] for r in db.query("SELECT username FROM user_project")}
        for user, project in latest.items():
            if user in existing:
                db.execute("UPDATE user_project SET project = ?, source_file = ?, updated_at = ? WHERE username = ?",
                           [project, name, now, user])
            else:
                db.insert("user_project", {"username": user, "project": project, "source_file": name, "updated_at": now})
    return {"users": len(latest), "projects": len(set(latest.values())), "file": name}


def summary(db) -> list[dict]:
    from heptagoneslm.storage.repository import Repository

    if not Repository(db).has_table("user_project"):
        return []
    return db.query_dicts("SELECT project, COUNT(*) AS users, MAX(updated_at) AS updated_at FROM user_project "
                          "GROUP BY project ORDER BY project")
