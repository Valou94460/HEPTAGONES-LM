"""Rôles de l'interface web (section ``web.roles``).

* **admin**   : vue globale ; administration (fonctions à venir) ;
* **reader**  : vue globale ;
* **service** : responsable de service — ne voit que les utilisateurs de ses services
  (sessions, répartition, refus, utilisateurs connectés, exports), et en option que
  certains serveurs ou produits. Les compteurs d'un produit (jetons émis / utilisés,
  saturation) restent globaux : ils ne désignent personne.

Les rôles sont attribués par groupe AD (connexion LDAP : ``memberOf`` ; authentification
unique IIS : groupes Windows transmis par le module HeptagonesLMUserHeader) ou par compte.
Sans section ``web.roles``, tout utilisateur autorisé voit tout (comportement historique).
Le filtrage est appliqué par le serveur à chaque requête : il ne dépend pas de l'interface.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from heptagoneslm.config import RolesConfig


class Forbidden(PermissionError):
    """Utilisateur authentifié mais sans rôle, ou demande hors de son périmètre."""


@dataclass
class Access:
    role: str                                  # admin | reader | service | local
    departments: list[str] = field(default_factory=list)   # vide = tous
    servers: list[str] = field(default_factory=list)
    products: list[str] = field(default_factory=list)

    @property
    def global_view(self) -> bool:
        return self.role in ("admin", "reader", "local")

    def describe(self) -> str:
        if self.global_view:
            return {"admin": "Administrateur", "reader": "Lecteur (vue globale)", "local": "Poste local"}[self.role]
        parts = ["Responsable de service : " + ", ".join(self.departments)]
        if self.servers:
            parts.append("serveurs : " + ", ".join(self.servers))
        if self.products:
            parts.append("produits : " + ", ".join(self.products))
        return " — ".join(parts)

    def as_dict(self) -> dict:
        return {"role": self.role, "global": self.global_view, "departments": self.departments,
                "servers": self.servers, "products": self.products, "label": self.describe()}


def _norm(name: str) -> str:
    """« DOMAINE\\GG_x », « GG_x@dom », « CN=GG_x,OU=… » -> « gg_x »."""
    n = (name or "").strip()
    if n.upper().startswith("CN="):
        n = n[3:].split(",", 1)[0]
    return n.split("\\")[-1].split("@")[0].strip().lower()


def _matches(entries: list[str], username: str, groups: set[str]) -> bool:
    return any(_norm(e) == username or _norm(e) in groups for e in entries)


def resolve(user, roles: RolesConfig | None, auth_mode: str) -> Access:
    """Rôle et périmètre de l'utilisateur ; lève ``Forbidden`` s'il n'a aucun rôle."""
    if auth_mode == "none":
        return Access("local")
    if roles is None:
        return Access("reader")
    username = _norm(user.username)
    groups = {_norm(g) for g in (user.groups or [])}
    if _matches(roles.admins, username, groups):
        return Access("admin")
    if _matches(roles.readers, username, groups):
        return Access("reader")
    scopes = [s for s in roles.scoped if (s.user and _norm(s.user) == username) or (s.group and _norm(s.group) in groups)]
    if scopes:
        def union(attr):
            values = [getattr(s, attr) for s in scopes]
            if attr != "departments" and any(not v for v in values):
                return []          # un des rôles couvre tous les serveurs / produits
            seen: list[str] = []
            for v in values:
                seen += [x for x in v if x not in seen]
            return seen
        return Access("service", union("departments"), union("servers"), union("products"))
    if roles.default == "reader":
        return Access("reader")
    raise Forbidden("Votre compte n'a pas de rôle dans HeptagonesLM : demandez l'accès à l'administrateur.")


def _restrict(requested: list[str], allowed: list[str], label: str) -> list[str]:
    if not allowed:
        return requested
    if not requested:
        return list(allowed)
    kept = [r for r in requested if r in allowed]
    if not kept:
        raise Forbidden(f"{label} hors de votre périmètre : {', '.join(requested)}")
    return kept


def restrict(params, access: Access):
    """Applique le périmètre aux paramètres d'un rapport (modifiés sur place)."""
    if access.global_view:
        return params
    params.departments = _restrict(params.departments, access.departments, "Service(s)")
    params.servers = _restrict(params.servers, access.servers, "Serveur(s)")
    params.products = _restrict(params.products, access.products, "Produit(s)")
    params.scoped = True
    return params


def filter_meta(meta: dict, access: Access) -> dict:
    """Listes des filtres limitées au périmètre (services, utilisateurs, serveurs, produits)."""
    if access.global_view:
        return meta
    deps = set(access.departments)
    meta = dict(meta)
    meta["departments"] = [d for d in meta["departments"] if d in deps]
    meta["users"] = [u for u in meta["users"] if u.get("department") in deps]
    if access.servers:
        meta["servers"] = [s for s in meta["servers"] if s in access.servers]
        meta["products"] = [dict(p, servers=[s for s in p["servers"] if s in access.servers])
                            for p in meta["products"] if set(p["servers"]) & set(access.servers)]
    if access.products:
        meta["products"] = [p for p in meta["products"] if p["name"] in access.products]
    return meta
