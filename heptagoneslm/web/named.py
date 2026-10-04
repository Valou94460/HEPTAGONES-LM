"""Licences Autodesk « utilisateur nommé » dans les rubriques de l'interface.

Les produits Autodesk Cloud (Vault, Product Design & Manufacturing Collection…) apparaissent comme un
serveur « Autodesk Cloud » dans la Synthèse, l'Usage dans le temps, la Répartition, le Croisement,
l'Efficacité et l'Optimisation & coûts, filtres compris (produits, utilisateurs, services).

Unités : pour une licence nommée, Autodesk ne donne ni jetons ni heures. Les jetons émis sont les
**licences affectées** (dernier état connu : export du portail ou API) ; l'usage est compté en
**utilisateurs distincts par jour** et en **jours d'utilisation** (usage quotidien lu par l'API
Autodesk, table ``named_usage_day``). Aucune heure n'est inventée : les colonnes horaires restent vides.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from heptagoneslm.storage.db import Database

SERVER = "Autodesk Cloud"
PUBLISHER = "Autodesk"


def username_of(email: str) -> str:
    """Utilisateur d'une licence nommée : partie locale de l'adresse (jean.dupont@… -> jean.dupont)."""
    return (email or "").split("@", 1)[0].strip().lower()


@dataclass
class NamedData:
    start: date
    end: date                                      # exclu
    seats: dict[str, int] = field(default_factory=dict)          # produit -> licences affectées
    active: dict[str, int] = field(default_factory=dict)         # produit -> licences actives
    idle: dict[str, int] = field(default_factory=dict)           # produit -> inactives ou jamais utilisées
    never: dict[str, int] = field(default_factory=dict)
    daily: dict[str, dict[date, set]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(set)))
    user_days: dict[tuple[str, str], int] = field(default_factory=lambda: defaultdict(int))   # (user, produit)
    names: dict[str, str] = field(default_factory=dict)          # utilisateur -> nom complet
    departments: dict[str, str | None] = field(default_factory=dict)
    has_daily: bool = False
    report_date: date | None = None

    @property
    def products(self) -> list[str]:
        return sorted(set(self.seats) | set(self.daily))

    def days_in_period(self) -> list[date]:
        last = min(self.end, date.today() + timedelta(days=1))
        return [self.start + timedelta(days=i) for i in range((last - self.start).days)]

    def business_days(self) -> list[date]:
        return [d for d in self.days_in_period() if d.weekday() < 5]

    def peak(self, product: str) -> int:
        return max((len(u) for u in self.daily.get(product, {}).values()), default=0)

    def avg(self, product: str, days: list[date] | None = None) -> float:
        days = days if days is not None else self.business_days()
        if not days:
            return 0.0
        per = self.daily.get(product, {})
        return sum(len(per.get(d, ())) for d in days) / len(days)

    def users(self, product: str) -> set:
        out: set = set()
        for u in self.daily.get(product, {}).values():
            out |= u
        return out

    def total_days(self, product: str) -> int:
        return sum(len(u) for u in self.daily.get(product, {}).values())


def included(p) -> bool:
    """Serveur « Autodesk Cloud » retenu par le filtre des serveurs."""
    return not p.servers or SERVER in p.servers


def load(db: Database, p) -> NamedData | None:
    """Licences nommées et usage quotidien de la période, filtres appliqués (None : aucune donnée)."""
    from heptagoneslm import autodesk
    from heptagoneslm.storage.repository import Repository

    repo = Repository(db)
    if not included(p) or not repo.has_table("named_seat"):
        return None
    start, end = p.start.date(), p.end.date()
    data = NamedData(start, end)
    wanted = {x.lower() for x in p.products} if p.products else None
    users = db.query_dicts("SELECT username, display_name, department FROM app_user")
    known = {u["username"].lower(): u for u in users}
    visible = None
    if p.users or p.departments:
        deps, names = set(p.departments), set(p.users)
        visible = {u for u, r in known.items()
                   if (not names or u in names) and (not deps or (r["department"] or "(non renseigné)") in deps)}
        visible |= {u for u in names if u not in known and not deps}      # utilisateur Autodesk hors référentiel

    def keep(product: str, user: str) -> bool:
        return (wanted is None or product.lower() in wanted) and (visible is None or user in visible)

    report_date, seats = autodesk.seats(db, p.named_inactive_days, {}, None)
    data.report_date = report_date
    for s in seats:
        user = username_of(s["email"])
        if not keep(s["product"], user):
            continue
        prod = s["product"]
        data.seats[prod] = data.seats.get(prod, 0) + 1
        if s["status"] == "active":
            data.active[prod] = data.active.get(prod, 0) + 1
        else:
            data.idle[prod] = data.idle.get(prod, 0) + 1
            if s["status"] == "never":
                data.never[prod] = data.never.get(prod, 0) + 1
        if s.get("display_name"):
            data.names.setdefault(user, s["display_name"])
    if repo.has_table("named_usage_day"):
        rows = db.query("SELECT product, email, usage_date FROM named_usage_day WHERE vendor = ? AND usage_date >= ? "
                        "AND usage_date < ?", [autodesk.VENDOR, start, end])
        for product, email, day in rows:
            user = username_of(email)
            if not keep(product, user):
                continue
            day = day if isinstance(day, date) else date.fromisoformat(str(day)[:10])
            if user not in data.daily[product][day]:
                data.daily[product][day].add(user)
                data.user_days[(user, product)] += 1
            data.has_daily = True
    for user, _ in data.user_days:
        data.departments.setdefault(user, (known.get(user) or {}).get("department"))
    if not data.seats and not data.has_daily:
        return None
    return data


def notes(data: NamedData | None) -> list[str]:
    if data is None:
        return []
    out = [f"{SERVER} : licences « utilisateur nommé » Autodesk. Jetons émis = licences affectées"
           + (f" (état du {data.report_date:%d/%m/%Y})" if data.report_date else "")
           + " ; usage en utilisateurs distincts par jour et en jours d'utilisation (Autodesk ne fournit pas "
           "d'heures d'utilisation)."]
    if not data.has_daily:
        out.append(f"{SERVER} : usage quotidien pas encore synchronisé (API Autodesk) — seules les licences "
                   "affectées sont connues pour cette période.")
    return out


def meta_extend(db: Database, meta: dict) -> dict:
    """Serveur, produits et utilisateurs Autodesk ajoutés aux listes des filtres."""
    from heptagoneslm import autodesk
    from heptagoneslm.storage.repository import Repository

    if not Repository(db).has_table("named_seat"):
        return meta
    _, seats = autodesk.seats(db, 30, {}, None)
    products = sorted({s["product"] for s in seats})
    if Repository(db).has_table("named_usage_day"):
        products = sorted(set(products) | {r[0] for r in db.query(
            "SELECT DISTINCT product FROM named_usage_day WHERE vendor = ?", [autodesk.VENDOR])})
    if not products:
        return meta
    meta = dict(meta)
    meta["servers"] = sorted(set(meta["servers"]) | {SERVER})
    known = {p["name"] for p in meta["products"]}
    meta["products"] = meta["products"] + [{"name": p, "publisher": PUBLISHER, "servers": [SERVER]}
                                           for p in products if p not in known]
    users = {u["username"] for u in meta["users"]}
    extra = {}
    for s in seats:
        u = username_of(s["email"])
        if u and u not in users:
            extra.setdefault(u, s.get("display_name"))
    meta["users"] = meta["users"] + [{"username": u, "display_name": n, "department": None}
                                     for u, n in sorted(extra.items())]
    return meta
