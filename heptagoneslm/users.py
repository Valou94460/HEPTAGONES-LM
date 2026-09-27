"""Import du référentiel utilisateurs (nom complet, service) depuis un CSV.

Le fichier peut provenir d'un export Active Directory ou RH. Colonnes
reconnues (en-tête obligatoire, insensible à la casse, séparateur ``;`` ou
``,`` détecté automatiquement) :

* identifiant : ``username``, ``utilisateur``, ``login``, ``samaccountname``
* nom complet : ``display_name``, ``nom``, ``displayname``, ``name``
* service     : ``department``, ``service``, ``departement``, ``département``

Exemple d'export AD (PowerShell) ::

    Get-ADUser -Filter * -Properties DisplayName,Department |
      Select-Object SamAccountName,DisplayName,Department |
      Export-Csv users.csv -NoTypeInformation -Encoding UTF8
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from heptagoneslm.storage.repository import Repository

ALIASES = {
    "username": {"username", "utilisateur", "login", "samaccountname", "user", "identifiant"},
    "display_name": {"display_name", "displayname", "nom", "name", "nom complet"},
    "department": {"department", "service", "departement", "département"},
}


def read_users_csv(path: str | Path) -> list[dict]:
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    first = text.splitlines()[0] if text else ""
    delimiter = ";" if first.count(";") >= first.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    mapping: dict[str, str] = {}
    for col in reader.fieldnames or []:
        norm = col.strip().strip('"').lower()
        for target, names in ALIASES.items():
            if norm in names and target not in mapping:
                mapping[target] = col
    if "username" not in mapping:
        raise ValueError(f"Colonne identifiant introuvable (attendu : {', '.join(sorted(ALIASES['username']))})")
    users = []
    for row in reader:
        username = (row.get(mapping["username"]) or "").strip()
        if not username:
            continue
        users.append({
            "username": username,
            "display_name": (row.get(mapping.get("display_name", ""), "") or "").strip() or None,
            "department": (row.get(mapping.get("department", ""), "") or "").strip() or None,
        })
    return users


def import_users(repo: Repository, users: list[dict]) -> tuple[int, int]:
    """Retourne (utilisateurs mis à jour, utilisateurs créés)."""
    updated = created = 0
    for u in users:
        if repo.update_user_info(u["username"], u["display_name"], u["department"]):
            updated += 1
        else:
            created += 1
    return updated, created
