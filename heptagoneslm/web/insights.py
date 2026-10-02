"""Analyses avancées de l'interface : tableau de bord, efficacité des jetons,
optimisation des coûts, disponibilité en direct et alertes.

Complète ``heptagoneslm.web.api`` (mêmes paramètres, même format de résultat) avec
les vues inspirées des plateformes du marché (OpenLM) :

* **Tableau de bord** : indicateurs clés avec tendance et comparaison à la période
  précédente, alertes, utilisation du parc, en direct, top utilisateurs, refus,
  santé des serveurs, échéances.
* **Efficacité** : part du temps pendant laquelle au moins N jetons sont utilisés
  (courbe de charge classée) et nombre de jetons recommandé pour couvrir 95 % du
  temps ouvré.
* **Optimisation** : jetons excédentaires, économies potentielles, produits en
  tension, coût par heure d'utilisation.
* **En direct** : jetons utilisés maintenant et par qui.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from heptagoneslm.reporting.dataset import _server_filter, build_report_data, load_features
from heptagoneslm.storage.db import Database

STALE_COLLECTION = timedelta(minutes=60)   # au-delà : « collecte arrêtée »
EXPIRY_WARNING_DAYS = 30
EXPIRY_CRITICAL_DAYS = 7
EXPIRY_HORIZON_DAYS = 120                  # échéances affichées sur le tableau de bord
SATURATION_SHARE = 0.05                    # saturé > 5 % des heures ouvrées : produit en tension
UNDERUSED_PEAK = 0.5


def _api():
    from heptagoneslm.web import api  # import tardif (évite l'import circulaire)

    return api


def _days(start: datetime, end: datetime) -> list[date]:
    out, d = [], start.date()
    while datetime.combine(d, datetime.min.time()) < end:
        out.append(d)
        d += timedelta(days=1)
    return out


def _is_business(dt: datetime, business_hours: tuple[int, int]) -> bool:
    return dt.weekday() < 5 and business_hours[0] <= dt.hour < business_hours[1]


def business_hours_count(start: datetime, end: datetime, business_hours: tuple[int, int]) -> int:
    return sum(1 for d in _days(start, end) if d.weekday() < 5) * max(0, business_hours[1] - business_hours[0])


# ---------------------------------------------------------------------------
# Agrégats horaires (occupation quotidienne, saturation)
# ---------------------------------------------------------------------------


def hourly_stats(db: Database, start: datetime, end: datetime, p) -> dict:
    """Occupation par jour et globale (heures ouvrées), à partir de usage_hourly."""
    features = {f["id"]: f for f in load_features(db, start, end, p.servers or None, p.products or None)}
    sql_srv, p_srv = _server_filter(p.servers or None)
    rows = db.query(
        "SELECT h.feature_id, h.hour_start, h.samples, h.sum_in_use, h.max_in_use, h.issued "
        "FROM usage_hourly h JOIN feature f ON f.id = h.feature_id JOIN license_server srv ON srv.id = f.server_id "
        "WHERE h.hour_start >= ? AND h.hour_start < ?" + sql_srv,
        [start, end, *p_srv],
    )
    biz_avg, biz_issued = defaultdict(float), defaultdict(int)          # par jour
    hour_max, hour_issued = defaultdict(int), defaultdict(int)          # par heure (tous produits)
    saturated = defaultdict(int)                                         # heures saturées par jour
    tot_avg = tot_issued = 0.0
    for fid, hour_start, samples, sum_in_use, max_in_use, issued in rows:
        if fid not in features or not samples or not issued:
            continue
        day = hour_start.date()
        hour_max[hour_start] += max_in_use
        hour_issued[hour_start] += issued
        if max_in_use >= issued:
            saturated[day] += 1
        if _is_business(hour_start, p.business_hours):
            avg = sum_in_use / samples
            biz_avg[day] += avg
            biz_issued[day] += issued
            tot_avg += avg
            tot_issued += issued
    peak_day: dict[date, float] = defaultdict(float)
    for hour, used in hour_max.items():
        peak_day[hour.date()] = max(peak_day[hour.date()], used / hour_issued[hour])
    days = _days(start, end)
    return {
        "days": days,
        "business_utilization": [biz_avg[d] / biz_issued[d] if biz_issued[d] else None for d in days],
        "peak_utilization": [peak_day.get(d) for d in days],
        "saturated": [saturated.get(d, 0) for d in days],
        "overall_business_utilization": (tot_avg / tot_issued) if tot_issued else None,
        "saturated_total": sum(saturated.values()),
        "has_data": bool(rows),
    }


# ---------------------------------------------------------------------------
# Efficacité (courbe de charge classée)
# ---------------------------------------------------------------------------


def efficiency_stats(db: Database, p, business_only: bool = True) -> dict[int, dict]:
    """Histogramme « jetons utilisés -> nombre de sondages » par feature.

    Utilise les sondages bruts (usage_snapshot) ; au-delà de leur rétention, se
    rabat sur les maxima horaires (approximation prudente, signalée).
    """
    features = {f["id"]: f for f in load_features(db, p.start, p.end, p.servers or None, p.products or None)}
    sql_srv, p_srv = _server_filter(p.servers or None)
    hour, weekday = db.hour_of("s.snapshot_time"), db.weekday_of("s.snapshot_time")
    rows = db.query(
        f"SELECT s.feature_id, s.in_use, {hour}, {weekday}, COUNT(*), MAX(s.issued) "
        "FROM usage_snapshot s JOIN feature f ON f.id = s.feature_id JOIN license_server srv ON srv.id = f.server_id "
        "WHERE s.snapshot_time >= ? AND s.snapshot_time < ?" + sql_srv +
        f" GROUP BY s.feature_id, s.in_use, {hour}, {weekday}",
        [p.start, p.end, *p_srv],
    )
    source = "snapshots"
    if not rows:  # sondages purgés : maxima horaires
        hour, weekday = db.hour_of("h.hour_start"), db.weekday_of("h.hour_start")
        rows = db.query(
            f"SELECT h.feature_id, h.max_in_use, {hour}, {weekday}, COUNT(*), MAX(h.issued) "
            "FROM usage_hourly h JOIN feature f ON f.id = h.feature_id JOIN license_server srv ON srv.id = f.server_id "
            "WHERE h.hour_start >= ? AND h.hour_start < ?" + sql_srv +
            f" GROUP BY h.feature_id, h.max_in_use, {hour}, {weekday}",
            [p.start, p.end, *p_srv],
        )
        source = "hourly"
    stats: dict[int, dict] = {}
    bh0, bh1 = p.business_hours
    for fid, in_use, h, wd, count, issued in rows:
        if fid not in features:
            continue
        if business_only and not (int(wd) < 5 and bh0 <= int(h) < bh1):
            continue
        st = stats.setdefault(fid, {"feature": features[fid], "hist": defaultdict(int), "total": 0, "issued": 0})
        st["hist"][int(in_use)] += int(count)
        st["total"] += int(count)
        st["issued"] = max(st["issued"], int(issued or 0))
    for st in stats.values():
        hist, total = st["hist"], st["total"]
        top = max(max(hist), st["issued"]) if hist else st["issued"]
        # share_ge[x] = part des sondages avec au moins x jetons utilisés (x = 0..top)
        share_ge, acc = [0.0] * (top + 2), 0
        for x in range(top, -1, -1):
            acc += hist.get(x, 0)
            share_ge[x] = acc / total if total else 0.0
        st["share_ge"] = share_ge
        st["peak"] = max((v for v, c in hist.items() if c), default=0)
        tail = 1 - p.target_coverage
        st["recommended"] = next((x for x in range(0, top + 1) if share_ge[x + 1] <= tail + 1e-12), top)
        st["saturated_share"] = share_ge[st["issued"]] if st["issued"] else 0.0
        st["source"] = source
    return stats


def report_efficiency(db: Database, p) -> dict:
    api = _api()
    stats = efficiency_stats(db, p)
    cov = round(p.target_coverage * 100)
    rows = []
    for st in sorted(stats.values(), key=lambda s: s["feature"]["label"]):
        f, issued = st["feature"], st["issued"]
        rows.append({
            "product": f["label"], "server": f["server"], "issued": issued, "peak": st["peak"],
            "recommended": st["recommended"],
            "unused": max(issued - st["peak"], 0),
            "surplus": max(issued - st["recommended"], 0),
            "half_share": st["share_ge"][max(1, (issued + 1) // 2)] if issued else None,
            "saturated_share": st["saturated_share"],
        })
    chart = None
    if rows:
        single = len(stats) == 1
        shown = sorted(stats.values(), key=lambda s: -s["issued"])[:api.MAX_CHART_SERIES]
        shown.sort(key=lambda s: s["feature"]["label"])
        if single:
            st = shown[0]
            xs = list(range(1, max(st["issued"], st["peak"]) + 1))
            series = [{"name": st["feature"]["label"], "values": [st["share_ge"][x] for x in xs]}]
            x_type = "tokens"
        else:  # comparaison : en part du parc (10 % … 100 % des jetons émis)
            xs = [i / 10 for i in range(1, 11)]
            series = []
            for st in shown:
                n, curve = st["issued"] or 1, st["share_ge"]
                # seuil en jetons pour une part x du parc (arrondi au supérieur, au moins 1)
                values = [curve[min(len(curve) - 1, max(1, math.ceil(round(x * n, 6))))] for x in xs]
                series.append({"name": st["feature"]["label"], "values": values})
            x_type = "share"
        chart = {"kind": "line", "title": "Part du temps ouvré où au moins N jetons sont utilisés",
                 "format": "pct", "x": xs, "xType": x_type, "series": series}
    notes = [f"Nombre recommandé : couvre {cov} % du temps ouvré (jours ouvrés, {p.business_hours[0]} h – "
             f"{p.business_hours[1]} h) ; au-delà, les pics restent ponctuels."]
    if any(st["source"] == "hourly" for st in stats.values()):
        notes.append("Sondages bruts purgés sur cette période : calcul sur les maxima horaires (estimation prudente).")
    if len(stats) > 1:
        notes.append("Graphique en part du parc (10 % à 100 % des jetons émis) pour comparer les produits ; "
                     "filtrez un seul produit pour l'afficher en nombre de jetons.")
    C = api._col
    columns = [
        C("product", "Produit"), C("server", "Serveur"), C("issued", "Jetons émis", "int"),
        C("peak", "Pic observé", "int"), C("recommended", f"Recommandé ({cov} %)", "int"),
        C("surplus", "Excédent", "int"), C("unused", "Jamais utilisés", "int"),
        C("half_share", "Temps ≥ 50 % du parc", "pct"), C("saturated_share", "Temps saturé", "pct"),
    ]
    kpis = [
        {"label": "Jetons émis", "value": sum(r["issued"] for r in rows), "type": "int"},
        {"label": f"Jetons recommandés ({cov} %)", "value": sum(r["recommended"] for r in rows), "type": "int"},
        {"label": "Jetons excédentaires", "value": sum(r["surplus"] for r in rows), "type": "int"},
        {"label": "Jetons jamais utilisés", "value": sum(r["unused"] for r in rows), "type": "int"},
    ]
    return api._result("efficiency", "Efficacité des jetons", p.describe(), columns, rows, chart, kpis, notes)


# ---------------------------------------------------------------------------
# Optimisation et coûts
# ---------------------------------------------------------------------------


def optimization_rows(db: Database, p, data=None, stats=None) -> list[dict]:
    data = data or build_report_data(db, p.start, p.end, servers=p.servers or None,
                                     business_hours=p.business_hours, products=p.products or None)
    stats = stats if stats is not None else efficiency_stats(db, p)
    biz_hours = business_hours_count(p.start, p.end, p.business_hours)
    period_years = max((p.end - p.start).days, 1) / 365.0
    rows = []
    for s in data.summaries:
        st = stats.get(s.feature_id)
        issued = s.issued or 0
        recommended = st["recommended"] if st else s.peak_in_use
        unit = p.costs.get(s.product)
        tension = s.denials > 0 or (biz_hours and s.saturated_hours >= SATURATION_SHARE * biz_hours)
        surplus = 0 if tension else max(issued - recommended, 0)
        if tension:
            advice = "Pénurie : envisager +1 jeton" if s.denials else "Saturations fréquentes : à surveiller"
        elif surplus:
            advice = f"Réduire de {surplus} jeton{'s' if surplus > 1 else ''}"
        else:
            advice = "Dimensionnement adapté"
        annual = unit * issued if unit is not None else None
        period_cost = annual * period_years if annual is not None else None
        rows.append({
            "product": s.label, "server": s.server, "publisher": s.publisher, "issued": issued,
            "peak": s.peak_in_use, "recommended": recommended, "surplus": surplus,
            "business_utilization": s.business_utilization, "saturated_hours": s.saturated_hours,
            "denials": s.denials, "unit_cost": unit, "annual_cost": annual,
            "savings": surplus * unit if unit is not None else None,
            "cost_per_hour": (period_cost / s.usage_hours) if period_cost and s.usage_hours else None,
            "advice": advice, "tension": bool(tension),
        })
    return rows


def report_optimization(db: Database, p) -> dict:
    api = _api()
    rows = sorted(optimization_rows(db, p), key=lambda r: (-(r["savings"] or 0), -r["surplus"], r["product"]))
    has_costs = any(r["unit_cost"] is not None for r in rows)
    money = "money"
    C = api._col
    columns = [
        C("product", "Produit"), C("server", "Serveur"), C("issued", "Jetons émis", "int"),
        C("peak", "Pic", "int"), C("recommended", "Recommandé", "int"), C("surplus", "Excédent", "int"),
        C("business_utilization", "Taux heures ouvrées", "pct"), C("saturated_hours", "Heures saturées", "int"),
        C("denials", "Refus", "int"), C("unit_cost", "Coût annuel / jeton", money),
        C("annual_cost", "Coût annuel", money), C("savings", "Économie potentielle / an", money),
        C("cost_per_hour", "Coût / heure d'usage", money), C("advice", "Recommandation"),
    ]
    for r in rows:
        r.pop("tension", None)
        r.pop("publisher", None)
    kpis = [
        {"label": "Coût annuel du parc", "value": sum(r["annual_cost"] or 0 for r in rows) if has_costs else None, "type": money},
        {"label": "Économies potentielles / an", "value": sum(r["savings"] or 0 for r in rows) if has_costs else None, "type": money},
        {"label": "Jetons excédentaires", "value": sum(r["surplus"] for r in rows), "type": "int"},
        {"label": "Produits en tension", "value": sum(1 for r in rows if r["advice"].startswith(("Pénurie", "Saturations"))), "type": "int"},
    ]
    items = [{"label": r["product"], "value": r["savings"]} for r in rows if r["savings"]][:api.MAX_BAR_ITEMS]
    chart = {"kind": "bar", "format": money, "title": "Économies potentielles par an", "items": items} if items else None
    notes = []
    if not has_costs:
        notes.append("Renseignez le coût annuel d'un jeton (annual_cost) dans la section products: de la "
                     "configuration pour chiffrer les coûts et les économies.")
    notes.append(f"Recommandation : jetons couvrant {round(p.target_coverage * 100)} % du temps ouvré ; "
                 "un produit avec refus ou saturations fréquentes n'est jamais proposé à la baisse.")
    result = api._result("optimization", "Optimisation et coûts", p.describe(), columns, rows, chart, kpis, notes)
    result["currency"] = p.currency
    return result


# ---------------------------------------------------------------------------
# En direct
# ---------------------------------------------------------------------------


def live_state(db: Database, p) -> dict:
    # Dernier état connu de chaque feature, quel que soit son âge (l'horodatage est affiché
    # et une collecte arrêtée déclenche une alerte).
    features = {f["id"]: f for f in load_features(db, datetime(1900, 1, 1), datetime(9999, 1, 1),
                                                     p.servers or None, p.products or None)}
    if not features:
        return {"updated_at": None, "products": [], "sessions": []}
    marks = ", ".join("?" for _ in features)
    latest = db.query_dicts(
        "SELECT s.feature_id, s.snapshot_time, s.issued, s.in_use, s.queued FROM usage_snapshot s "
        "JOIN (SELECT feature_id, MAX(snapshot_time) AS t FROM usage_snapshot "
        f"WHERE feature_id IN ({marks}) GROUP BY feature_id) m "
        "ON m.feature_id = s.feature_id AND m.t = s.snapshot_time",
        list(features),
    )
    updated = max((r["snapshot_time"] for r in latest), default=None)
    from heptagoneslm.storage.repository import Repository

    borrowed_col = "s.borrowed" if Repository(db).has_column("license_session", "borrowed") else "0"
    sessions = db.query_dicts(
        "SELECT s.feature_id, u.username, u.display_name, u.department, w.hostname, w.site, s.checkout_time, "
        f"s.license_count, {borrowed_col} AS borrowed FROM license_session s JOIN app_user u ON u.id = s.user_id "
        "JOIN workstation w ON w.id = s.workstation_id "
        # Sessions issues des logs : last_seen n'avance pas entre OUT et IN, elles restent valides.
        f"WHERE s.checkin_time IS NULL AND (s.source = 'log' OR s.last_seen >= ?) AND s.feature_id IN ({marks}) "
        "ORDER BY s.checkout_time",
        [(updated or datetime.now()) - timedelta(hours=2), *features],
    ) if latest else []
    visible = _api().visible_usernames(db, p)
    if visible is not None:        # filtres utilisateur / service, périmètre d'un responsable de service
        sessions = [s for s in sessions if s["username"] in visible]
    if p.sites:
        sessions = [s for s in sessions if (s["site"] or _api().NOT_SET) in set(p.sites)]
    by_feature: dict[int, list] = defaultdict(list)
    for s in sessions:
        by_feature[s["feature_id"]].append(s)
    products = []
    for row in latest:
        f = features[row["feature_id"]]
        users = by_feature.get(row["feature_id"], [])
        products.append({
            "product": f["label"], "server": f["server"], "issued": row["issued"], "in_use": row["in_use"],
            "free": (row["issued"] - row["in_use"]) if row["issued"] is not None else None,
            "utilization": (row["in_use"] / row["issued"]) if row["issued"] else None,
            "queued": row["queued"], "updated_at": row["snapshot_time"],
            "users": [{"user": u["display_name"] or u["username"], "username": u["username"],
                       "department": u["department"], "host": u["hostname"], "site": u["site"],
                       "since": u["checkout_time"], "licenses": u["license_count"],
                       "borrowed": bool(u["borrowed"])} for u in users],
        })
    products.sort(key=lambda r: (-(r["utilization"] or 0), r["product"]))
    return {"updated_at": updated, "products": products, "sessions": sessions}


def report_live(db: Database, p) -> dict:
    api = _api()
    state = live_state(db, p)
    rows = []
    for r in state["products"]:
        rows.append({k: r[k] for k in ("product", "server", "issued", "in_use", "free", "utilization", "queued")}
                    | {"who": ", ".join(f"{u['user']} ({u['host']}{', emprunt' if u['borrowed'] else ''})" for u in r["users"]),
                       "updated_at": r["updated_at"]})
    C = api._col
    columns = [
        C("product", "Produit"), C("server", "Serveur"), C("in_use", "Utilisés", "int"), C("issued", "Émis", "int"),
        C("free", "Disponibles", "int"), C("utilization", "Occupation", "pct"), C("queued", "En attente", "int"),
        C("who", "Utilisateurs"), C("updated_at", "Mise à jour", "datetime"),
    ]
    in_use = sum(r["in_use"] or 0 for r in rows)
    issued = sum(r["issued"] or 0 for r in rows)
    kpis = [
        {"label": "Jetons utilisés", "value": in_use, "type": "int"},
        {"label": "Jetons disponibles", "value": issued - in_use, "type": "int"},
        {"label": "Utilisateurs connectés", "value": len({s["username"] for s in state["sessions"]}), "type": "int"},
        {"label": "Jetons empruntés (hors ligne)", "value": sum(s["license_count"] or 1 for s in state["sessions"] if s["borrowed"]), "type": "int"},
        {"label": "Produits saturés", "value": sum(1 for r in rows if r["issued"] and r["free"] is not None and r["free"] <= 0), "type": "int"},
    ]
    result = api._result("live", "En direct", "État au dernier sondage", columns, rows, None, kpis)
    result["live"] = {"updated_at": api._iso(state["updated_at"]),
                      "products": [dict(r, updated_at=api._iso(r["updated_at"]),
                                        users=[dict(u, since=api._iso(u["since"])) for u in r["users"]])
                                   for r in state["products"]]}
    return result


# ---------------------------------------------------------------------------
# Alertes
# ---------------------------------------------------------------------------


def expirations(db: Database, p, today: date | None = None, horizon: int = EXPIRY_HORIZON_DAYS) -> list[dict]:
    from heptagoneslm.storage.repository import Repository

    today = today or date.today()
    if not Repository(db).has_column("feature", "expires_on"):
        return []
    sql_srv, p_srv = _server_filter(p.servers or None)
    rows = db.query_dicts(
        "SELECT f.name AS feature, COALESCE(f.product, f.name) AS product, srv.name AS server, f.expires_on "
        "FROM feature f JOIN license_server srv ON srv.id = f.server_id "
        "WHERE f.expires_on IS NOT NULL AND f.expires_on <= ?" + sql_srv + " ORDER BY f.expires_on",
        [today + timedelta(days=horizon), *p_srv],
    )
    out = []
    for r in rows:
        if p.products and r["product"] not in p.products:
            continue
        d = r["expires_on"] if isinstance(r["expires_on"], date) else date.fromisoformat(str(r["expires_on"])[:10])
        out.append({"product": r["product"], "feature": r["feature"], "server": r["server"],
                    "expires_on": d.isoformat(), "days_left": (d - today).days})
    return out


def compute_alerts(db: Database, p, data, collections: list[dict], expiring: list[dict],
                   now: datetime | None = None) -> list[dict]:
    now = now or datetime.now()
    alerts: list[dict] = []

    def add(severity, title, detail, target=None):
        alerts.append({"severity": severity, "title": title, "detail": detail, "target": target})

    for c in collections:
        if c["status"] == "ERROR":
            add("critical", f"Collecte en échec : {c['server']}",
                f"{c['collector']} — {c['message'] or 'erreur'}", "collections")
    last = max((datetime.fromisoformat(c["last_run"]) for c in collections if c.get("last_run")), default=None)
    if last and now - last > STALE_COLLECTION:
        hours = (now - last).total_seconds() / 3600
        add("critical", "Collecte arrêtée",
            f"Aucune collecte depuis {hours:.0f} h (dernière : {last:%d/%m %H:%M}). Vérifier la tâche planifiée.",
            "collections")
    soon = []
    for e in expiring:
        if e["days_left"] < 0:
            add("critical", f"Licence expirée : {e['product']}", f"{e['feature']} ({e['server']}) depuis le {e['expires_on']}")
        elif e["days_left"] <= EXPIRY_CRITICAL_DAYS:
            add("critical", f"Expiration dans {e['days_left']} j : {e['product']}", f"{e['feature']} ({e['server']})")
        elif e["days_left"] <= EXPIRY_WARNING_DAYS:
            soon.append(e)
    if soon:
        names = ", ".join(f"{e['product']} ({e['days_left']} j)" for e in soon[:4])
        add("warning", f"{len(soon)} licence(s) expirent dans les {EXPIRY_WARNING_DAYS} jours",
            names + (" …" if len(soon) > 4 else "") + " — anticiper le renouvellement")
    denied = sorted((s for s in data.summaries if s.denials), key=lambda s: -s.denials)
    if denied:
        add("serious", f"{sum(s.denials for s in denied)} refus de licence sur la période",
            ", ".join(f"{s.label} ({s.denials})" for s in denied[:4]), "denials")
    biz = business_hours_count(p.start, p.end, p.business_hours)
    tense = [s for s in data.summaries if biz and s.saturated_hours >= SATURATION_SHARE * biz]
    if tense:
        add("warning", f"{len(tense)} produit(s) saturé(s) plus de {SATURATION_SHARE:.0%} du temps ouvré",
            ", ".join(f"{s.label} ({s.saturated_hours} h)" for s in tense[:4]), "contention")
    cold = [s for s in data.summaries if s.issued and s.issued >= 2 and s.peak_utilization is not None
            and s.peak_utilization < UNDERUSED_PEAK]
    if cold:
        add("info", f"{len(cold)} produit(s) sous-utilisé(s)",
            ", ".join(f"{s.label} (pic {s.peak_in_use}/{s.issued})" for s in cold[:4]) + " — pistes d'économie",
            "optimization")
    order = {"critical": 0, "serious": 1, "warning": 2, "info": 3}
    alerts.sort(key=lambda a: order[a["severity"]])
    return alerts


# ---------------------------------------------------------------------------
# Tableau de bord
# ---------------------------------------------------------------------------


def _delta(current, previous) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return (current - previous) / previous


def _daily_sessions(sessions: list[dict], days: list[date]) -> tuple[list[int], list[float]]:
    users: dict[date, set] = defaultdict(set)
    hours: dict[date, float] = defaultdict(float)
    for s in sessions:
        start = datetime.fromisoformat(s["checkout_time"]) if isinstance(s["checkout_time"], str) else s["checkout_time"]
        users[start.date()].add(s["username"])
        hours[start.date()] += s["hours_in_period"]
    return [len(users.get(d, ())) for d in days], [round(hours.get(d, 0.0), 1) for d in days]


def report_dashboard(db: Database, p) -> dict:
    api = _api()
    length = p.end - p.start
    prev = type(p)(**{**p.__dict__, "start": p.start - length, "end": p.start})
    data = build_report_data(db, p.start, p.end, servers=p.servers or None,
                             business_hours=p.business_hours, products=p.products or None)
    prev_data = build_report_data(db, prev.start, prev.end, servers=p.servers or None,
                                  business_hours=p.business_hours, products=p.products or None)
    sessions = api._filter_sessions(data.sessions, p)
    prev_sessions = api._filter_sessions(prev_data.sessions, prev)
    hs, prev_hs = hourly_stats(db, p.start, p.end, p), hourly_stats(db, prev.start, prev.end, prev)
    days = hs["days"]
    daily_users, daily_hours = _daily_sessions(sessions, days)
    denials_by_day: dict[str, int] = defaultdict(int)
    for d in data.denials:
        denials_by_day[str(d["event_time"])[:10]] += 1
    daily_denials = [denials_by_day.get(d.isoformat(), 0) for d in days]

    stats = efficiency_stats(db, p)
    opt = optimization_rows(db, p, data, stats)
    has_costs = any(r["unit_cost"] is not None for r in opt)

    def kpi(key, label, value, previous, type_, trend, spark=None):
        return {"key": key, "label": label, "value": value, "previous": previous, "type": type_,
                "delta": _delta(value, previous), "trend": trend, "spark": spark}

    users_now = len({s["username"] for s in sessions})
    users_prev = len({s["username"] for s in prev_sessions})
    hours_now = round(sum(s["hours_in_period"] for s in sessions), 1)
    hours_prev = round(sum(s["hours_in_period"] for s in prev_sessions), 1)
    den_now = sum(s.denials for s in data.summaries)
    den_prev = sum(s.denials for s in prev_data.summaries)
    kpis = [
        kpi("utilization", "Taux d'occupation (heures ouvrées)", hs["overall_business_utilization"],
            prev_hs["overall_business_utilization"], "pct", "up_good", hs["business_utilization"]),
        kpi("users", "Utilisateurs actifs", users_now, users_prev, "int", "neutral", daily_users),
        kpi("hours", "Heures d'utilisation", hours_now, hours_prev, "hours", "neutral", daily_hours),
        kpi("saturated", "Heures saturées", hs["saturated_total"], prev_hs["saturated_total"], "int", "down_good",
            hs["saturated"]),
        kpi("denials", "Refus de licence", den_now, den_prev, "int", "down_good", daily_denials),
    ]
    if has_costs:
        kpis.append(kpi("savings", "Économies potentielles / an", sum(r["savings"] or 0 for r in opt), None,
                        "money", "neutral"))
    else:
        kpis.append(kpi("surplus", "Jetons excédentaires", sum(r["surplus"] for r in opt), None, "int", "neutral"))

    live = live_state(db, p)
    collections = api.collections_status(db)
    expiring = expirations(db, p)
    alerts = compute_alerts(db, p, data, collections, expiring)

    products = [{
        "product": s.label, "server": s.server, "issued": s.issued, "peak": s.peak_in_use,
        "avg_utilization": s.avg_utilization, "business_utilization": s.business_utilization,
        "peak_utilization": s.peak_utilization, "saturated_hours": s.saturated_hours, "denials": s.denials,
    } for s in sorted(data.summaries, key=lambda s: -(s.business_utilization or 0))]

    top_users = []
    per_user: dict[str, float] = defaultdict(float)
    info: dict[str, dict] = {}
    for s in sessions:
        per_user[s["username"]] += s["hours_in_period"]
        info.setdefault(s["username"], {"display_name": s["display_name"], "department": s["department"]})
    for u, h in sorted(per_user.items(), key=lambda kv: -kv[1])[:8]:
        top_users.append({"username": u, **info[u], "hours": round(h, 1)})

    live_products = {r["server"] + "|" + r["product"]: r for r in live["products"]}
    servers = []
    for name in sorted({c["server"] for c in collections} | {s.server for s in data.summaries}):
        cols = [c for c in collections if c["server"] == name]
        status = "error" if any(c["status"] == "ERROR" for c in cols) else "ok"
        last = max((c["last_run"] for c in cols if c.get("last_run")), default=None)
        if status == "ok" and last and datetime.now() - datetime.fromisoformat(last) > STALE_COLLECTION:
            status = "stale"
        lp = [r for k, r in live_products.items() if k.startswith(name + "|")]
        servers.append({
            "server": name, "status": status, "last_run": last,
            "message": next((c["message"] for c in cols if c["status"] == "ERROR"), None),
            "products": len({s.label for s in data.summaries if s.server == name}),
            "in_use": sum(r["in_use"] or 0 for r in lp), "issued": sum(r["issued"] or 0 for r in lp),
        })

    heat = [[data.hour_profile.get((wd, h)) for wd in range(7)] for h in range(24)]
    return {
        "type": "dashboard",
        "title": "Tableau de bord",
        "description": p.describe(),
        "period": {"start": api._iso(p.start), "end": api._iso(p.end - timedelta(seconds=1)),
                   "previous_start": api._iso(prev.start), "previous_end": api._iso(prev.end - timedelta(seconds=1))},
        "currency": p.currency,
        "kpis": kpis,
        "alerts": alerts,
        "trend": {"x": [d.isoformat() for d in days],
                  "business_utilization": hs["business_utilization"],
                  "peak_utilization": hs["peak_utilization"]},
        "denials_daily": {"x": [d.isoformat() for d in days], "values": daily_denials},
        "products": products,
        "top_users": top_users,
        "servers": servers,
        "expirations": expiring,
        "heatmap": {"rows": [f"{h:02d}h" for h in range(24)], "values": heat, "has_data": any(
            v is not None for line in heat for v in line)},
        "live": {"updated_at": api._iso(live["updated_at"]),
                 "in_use": sum(r["in_use"] or 0 for r in live["products"]),
                 "issued": sum(r["issued"] or 0 for r in live["products"]),
                 "users": len({s["username"] for s in live["sessions"]}),
                 "products": [{"product": r["product"], "in_use": r["in_use"], "issued": r["issued"]}
                              for r in live["products"][:6]]},
        "columns": [], "rows": [], "chart": None, "notes": [],
    }


# ---------------------------------------------------------------------------
# Réservations (named user) et licences inutilisées
# ---------------------------------------------------------------------------

RESERVATION_LOW_USE = 0.2      # moins de 20 % des heures ouvrées : réservation peu utilisée
KIND_LABEL = {"USER": "Utilisateur", "HOST": "Poste", "GROUP": "Groupe d'utilisateurs", "HOST_GROUP": "Groupe de postes",
              "DISPLAY": "Écran", "INTERNET": "Adresse IP", "PROJECT": "Projet"}


def report_reservations(db: Database, p) -> dict:
    """Jetons réservés (fichier d'options RESERVE) et usage réel par le bénéficiaire."""
    api = _api()
    from heptagoneslm.storage.repository import Repository

    if not Repository(db).has_table("feature_reservation"):
        return api._result("reservations", "Réservations (utilisateurs nommés)", p.describe(), [], [], None, [],
                           ["Base à mettre à jour : exécuter « HeptagonesLM.exe init-db » (schéma v6)."])
    features = {f["id"]: f for f in load_features(db, p.start, p.end, p.servers or None, p.products or None)}
    rows_db = db.query_dicts("SELECT feature_id, kind, name, reserved, updated_at FROM feature_reservation "
                             "ORDER BY feature_id, kind, name")
    data = build_report_data(db, p.start, p.end, servers=p.servers or None,
                             business_hours=p.business_hours, products=p.products or None)
    by_label = {s.feature_id: s for s in data.summaries}
    sessions = api._filter_sessions(data.sessions, p)
    visible = api.visible_usernames(db, p)
    biz = business_hours_count(p.start, p.end, p.business_hours)
    rows = []
    for r in rows_db:
        f = features.get(r["feature_id"])
        if f is None:
            continue
        name = r["name"]
        if visible is not None and (r["kind"] != "USER" or name.lower() not in visible):
            continue           # périmètre limité : seules les réservations nominatives visibles
        s = by_label.get(r["feature_id"])
        hours, rate, advice = None, None, "Usage non mesurable par bénéficiaire (groupe)"
        if r["kind"] in ("USER", "HOST") and s is not None:
            key = "username" if r["kind"] == "USER" else "hostname"
            hours = round(sum(x["hours_in_period"] for x in sessions
                              if x["feature"] == s.feature and x["server"] == s.server
                              and (x[key] or "").lower() == name.lower()), 1)
            rate = hours / (r["reserved"] * biz) if biz else None
            if rate is not None and rate < RESERVATION_LOW_USE:
                advice = "Peu utilisée : envisager de libérer la réservation"
            else:
                advice = "Réservation utilisée"
        rows.append({"product": f["label"], "server": f["server"], "feature": f["feature"],
                     "kind": KIND_LABEL.get(r["kind"], r["kind"]), "beneficiary": name,
                     "reserved": r["reserved"], "hours": hours, "rate": rate, "advice": advice,
                     "updated_at": r["updated_at"]})
    rows.sort(key=lambda x: (x["rate"] if x["rate"] is not None else 9, x["product"]))
    C = api._col
    columns = [C("product", "Produit"), C("server", "Serveur"), C("kind", "Type"), C("beneficiary", "Bénéficiaire"),
               C("reserved", "Jetons réservés", "int"), C("hours", "Heures d'usage (période)", "hours"),
               C("rate", "Taux d'usage (heures ouvrées)", "pct"), C("advice", "Recommandation"),
               C("updated_at", "Constatée le", "datetime")]
    kpis = [
        {"label": "Réservations", "value": len(rows), "type": "int"},
        {"label": "Jetons réservés", "value": sum(r["reserved"] for r in rows), "type": "int"},
        {"label": "Peu utilisées", "value": sum(1 for r in rows if r["advice"].startswith("Peu")), "type": "int"},
    ]
    notes = [] if rows else ["Aucune réservation : elles proviennent des lignes RESERVE du fichier d'options "
                             "FlexLM, visibles dans lmstat."]
    notes.append(f"Taux d'usage : heures d'utilisation par le bénéficiaire rapportées aux heures ouvrées de la "
                 f"période × jetons réservés ; en dessous de {RESERVATION_LOW_USE:.0%}, la réservation immobilise "
                 "des jetons pour rien.")
    return api._result("reservations", "Réservations (utilisateurs nommés)", p.describe(), columns, rows, None,
                       kpis, notes)


def report_unused(db: Database, p) -> dict:
    """Licences jamais utilisées sur la période : candidates à la résiliation."""
    api = _api()
    data = build_report_data(db, p.start, p.end, servers=p.servers or None,
                             business_hours=p.business_hours, products=p.products or None)
    ids = [s.feature_id for s in data.summaries]
    last_use, expiry = {}, {}
    if ids:
        marks = ", ".join("?" for _ in ids)
        for fid, last in db.query(f"SELECT feature_id, MAX(COALESCE(checkin_time, last_seen)) FROM license_session "
                                  f"WHERE feature_id IN ({marks}) GROUP BY feature_id", ids):
            last_use[fid] = last
        from heptagoneslm.storage.repository import Repository

        if Repository(db).has_column("feature", "expires_on"):
            expiry = dict(db.query(f"SELECT id, expires_on FROM feature WHERE id IN ({marks})", ids))
    rows = []
    for s in data.summaries:
        if not s.issued or s.peak_in_use or s.usage_hours:
            continue
        unit = p.costs.get(s.product)
        rows.append({"product": s.label, "server": s.server, "feature": s.feature, "issued": s.issued,
                     "last_used": last_use.get(s.feature_id), "expires_on": expiry.get(s.feature_id),
                     "unit_cost": unit, "annual_cost": unit * s.issued if unit is not None else None})
    rows.sort(key=lambda r: (-(r["annual_cost"] or 0), r["product"]))
    money = "money"
    C = api._col
    columns = [C("product", "Produit"), C("server", "Serveur"), C("feature", "Feature"),
               C("issued", "Jetons émis", "int"), C("last_used", "Dernière utilisation", "datetime"),
               C("expires_on", "Échéance", "date"), C("unit_cost", "Coût annuel / jeton", money),
               C("annual_cost", "Coût annuel", money)]
    has_costs = any(r["unit_cost"] is not None for r in rows)
    kpis = [
        {"label": "Licences inutilisées", "value": len(rows), "type": "int"},
        {"label": "Jetons inutilisés", "value": sum(r["issued"] for r in rows), "type": "int"},
        {"label": "Coût annuel inutilisé", "value": sum(r["annual_cost"] or 0 for r in rows) if has_costs else None,
         "type": money},
    ]
    items = [{"label": r["product"], "value": r["annual_cost"]} for r in rows if r["annual_cost"]][:api.MAX_BAR_ITEMS]
    chart = {"kind": "bar", "format": money, "title": "Coût annuel des licences inutilisées", "items": items} if items else None
    notes = ["Licences émises par le serveur mais jamais utilisées sur la période (pic à 0) : à vérifier avec "
             "les métiers avant de les résilier au prochain renouvellement."]
    if rows and not has_costs:
        notes.append("Renseignez annual_cost dans la section products: pour chiffrer les économies.")
    result = api._result("unused", "Licences inutilisées", p.describe(), columns, rows, chart, kpis, notes)
    result["currency"] = p.currency
    return result


NAMED_STATUS = {"never": "Jamais utilisée", "inactive": "Inactive", "active": "Active"}


def report_named(db: Database, p) -> dict:
    """Licences Autodesk « utilisateur nommé » (exports du portail Autodesk Account) : affectées mais inutilisées."""
    from heptagoneslm import autodesk
    from heptagoneslm.storage.repository import Repository

    api = _api()
    money = "money"
    C = api._col
    columns = [C("status_label", "Statut"), C("product", "Produit"), C("email", "Utilisateur"),
               C("display_name", "Nom"), C("last_used", "Dernière utilisation", "date"),
               C("inactive_for", "Jours sans utilisation", "int"), C("days_used", "Jours d'utilisation", "int"),
               C("assigned_date", "Affectée le", "date"), C("unit_cost", "Coût annuel", money)]
    title = "Autodesk · utilisateurs nommés"
    how = ("Exporter le rapport depuis Autodesk Account (manage.autodesk.com › Reporting › Usage report › Export) "
           "puis l'importer : « heptagoneslm import-autodesk export.csv » ou le déposer dans le dossier "
           "autodesk.import_dir (importé à la collecte suivante).")
    if not Repository(db).has_table("named_seat"):
        return api._result("named", title, "Base à mettre à jour", columns, [],
                           notes=["Exécuter « heptagoneslm init-db » pour créer les tables des licences nommées.", how])
    if p.scoped:
        return api._result("named", title, "Réservé aux administrateurs", columns, [],
                           notes=["Les licences nommées ne sont pas rattachées à un service : rapport réservé aux "
                                  "administrateurs de licences."])
    report_date, rows = autodesk.seats(db, p.named_inactive_days, p.named_costs, p.products or None)
    if report_date is None:
        return api._result("named", title, "Aucun export importé", columns, [], notes=[how])
    for r in rows:
        r["status_label"] = NAMED_STATUS[r["status"]]
    idle = [r for r in rows if r["status"] in ("inactive", "never")]
    has_costs = any(r["unit_cost"] is not None for r in rows)
    users = {r["email"] for r in rows}
    kpis = [
        {"label": "Licences affectées", "value": len(rows), "type": "int"},
        {"label": "Utilisateurs", "value": len(users), "type": "int"},
        {"label": "Actives", "value": sum(r["status"] == "active" for r in rows), "type": "int"},
        {"label": f"Inactives (≥ {p.named_inactive_days} j)", "value": sum(r["status"] == "inactive" for r in rows),
         "type": "int"},
        {"label": "Jamais utilisées", "value": sum(r["status"] == "never" for r in rows), "type": "int"},
        {"label": "Coût annuel récupérable", "value": sum(r["recoverable_cost"] or 0 for r in rows) if has_costs else None,
         "type": money},
    ]
    per_product: dict[str, int] = {}
    for r in idle:
        per_product[r["product"]] = per_product.get(r["product"], 0) + 1
    items = [{"label": k, "value": v} for k, v in sorted(per_product.items(), key=lambda kv: (-kv[1], kv[0]))]
    chart = ({"kind": "bar", "format": "int", "title": "Licences inactives ou jamais utilisées par produit",
              "items": items[:api.MAX_BAR_ITEMS]} if items else None)
    age = (date.today() - report_date).days
    notes = [f"Licences nommées du rapport Autodesk du {report_date:%d/%m/%Y} : une licence est inactive lorsque "
             f"l'utilisateur ne l'a pas lancée depuis {p.named_inactive_days} jours (autodesk.inactive_days). "
             "La réaffecter dans Autodesk Account ou ne pas la renouveler à l'échéance."]
    sources = [r[0] for r in db.query("SELECT DISTINCT source_file FROM named_seat WHERE vendor = ? AND report_date = ?",
                                      [autodesk.VENDOR, report_date]) if r[0]]
    if sources:
        notes.append("Source : " + ", ".join(sources) + (" — usage lu par l'API Autodesk, licences sans usage sur la "
                                                         "période reprises du dernier état connu"
                                                         if any(x.startswith("API Autodesk") for x in sources) else ""))
    if age > 45:
        notes.append(f"Dernier export importé il y a {age} jours : en importer un plus récent ou configurer la "
                     "synchronisation par l'API Autodesk (autodesk.api). " + how)
    if rows and not has_costs:
        notes.append("Renseignez autodesk.annual_costs (produit ou motif → coût annuel) pour chiffrer les économies.")
    description = f"Rapport Autodesk du {report_date:%d/%m/%Y}" + (f" — produits : {', '.join(p.products)}"
                                                                  if p.products else "")
    result = api._result("named", title, description, columns, rows, chart, kpis, notes)
    result["currency"] = p.currency
    return result
