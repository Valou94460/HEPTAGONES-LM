"""Calcul des indicateurs de reporting à partir de la base.

Les données sont lues sur la période demandée puis agrégées en Python :
les requêtes restent portables (SQL Server / SQLite) et le volume lu est
maîtrisé grâce aux agrégats horaires (``usage_hourly``).

Définitions
-----------
* **Jetons émis** : nombre maximal de jetons déclarés par le serveur sur la période.
* **Pic** : nombre maximal de jetons utilisés simultanément (vu par lmstat).
* **Moyenne** : moyenne des jetons utilisés sur l'ensemble des sondages.
* **Taux moyen / taux au pic** : moyenne / pic rapportés aux jetons émis.
* **Taux heures ouvrées** : taux moyen restreint aux jours ouvrés, entre
  ``reporting.business_hours`` (8h-19h par défaut).
* **Heures saturées** : heures où tous les jetons étaient utilisés.
* **Heures d'utilisation** : somme des durées de session (× nb de jetons),
  tronquées aux bornes de la période.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from freelm.storage.db import Database

WEEKDAYS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]


@dataclass
class FeatureSummary:
    feature_id: int
    server: str
    feature: str
    product: str
    publisher: str | None
    label: str
    issued: int | None = None
    peak_in_use: int = 0
    avg_in_use: float = 0.0
    avg_utilization: float | None = None
    peak_utilization: float | None = None
    business_utilization: float | None = None
    saturated_hours: int = 0
    denials: int = 0
    distinct_users: int = 0
    sessions: int = 0
    usage_hours: float = 0.0


@dataclass
class ReportData:
    start: datetime
    end: datetime
    generated_at: datetime
    servers: list[str]
    summaries: list[FeatureSummary] = field(default_factory=list)
    # date -> label -> pic de jetons utilisés
    daily_peak: dict[date, dict[str, int]] = field(default_factory=dict)
    # utilisateur -> produit -> heures
    user_hours: dict[str, dict[str, float]] = field(default_factory=dict)
    user_info: dict[str, dict] = field(default_factory=dict)
    # (jour de semaine 0-6, heure 0-23) -> taux d'utilisation moyen
    hour_profile: dict[tuple[int, int], float] = field(default_factory=dict)
    contention: list[dict] = field(default_factory=list)
    sessions: list[dict] = field(default_factory=list)
    denials: list[dict] = field(default_factory=list)

    @property
    def labels(self) -> list[str]:
        return [s.label for s in self.summaries]

    @property
    def products(self) -> list[str]:
        seen: dict[str, None] = {}
        for per_user in self.user_hours.values():
            for p in per_user:
                seen.setdefault(p, None)
        return sorted(seen)


def _overlap_hours(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> float:
    start, end = max(a_start, b_start), min(a_end, b_end)
    return max((end - start).total_seconds(), 0.0) / 3600.0


def _server_filter(servers: list[str] | None, column: str = "srv.name") -> tuple[str, list]:
    if not servers:
        return "", []
    marks = ", ".join("?" for _ in servers)
    return f" AND {column} IN ({marks})", list(servers)


def load_features(
    db: Database,
    start: datetime,
    end: datetime,
    servers: list[str] | None = None,
    products: list[str] | None = None,
) -> list[dict]:
    """Features actives sur la période, avec un libellé lisible et unique.

    Le libellé est le produit, suffixé de la feature (et du serveur) en cas
    d'ambiguïté. ``products`` filtre sur le nom de produit (ou de feature si
    aucun produit n'est associé).
    """
    sql_srv, p_srv = _server_filter(servers)
    features = db.query_dicts(
        "SELECT f.id, srv.name AS server, f.name AS feature, COALESCE(f.product, f.name) AS product, "
        "f.publisher FROM feature f JOIN license_server srv ON srv.id = f.server_id "
        "WHERE f.last_seen >= ? AND f.first_seen < ?" + sql_srv + " ORDER BY srv.name, f.name",
        [start, end, *p_srv],
    )
    if products:
        wanted = set(products)
        features = [f for f in features if f["product"] in wanted]
    product_count: dict[str, int] = defaultdict(int)
    pair_count: dict[tuple[str, str], int] = defaultdict(int)
    for f in features:
        product_count[f["product"]] += 1
        pair_count[(f["product"], f["feature"])] += 1
    for f in features:
        if product_count[f["product"]] == 1:
            f["label"] = f["product"]
        elif pair_count[(f["product"], f["feature"])] == 1:
            f["label"] = f"{f['product']} [{f['feature']}]"
        else:  # même feature sur plusieurs serveurs
            f["label"] = f"{f['product']} [{f['feature']}@{f['server']}]"
    return features


def build_report_data(
    db: Database,
    start: datetime,
    end: datetime,
    servers: list[str] | None = None,
    business_hours: tuple[int, int] = (8, 19),
    now: datetime | None = None,
    products: list[str] | None = None,
) -> ReportData:
    """Construit l'ensemble des données du rapport pour la période [start, end[."""
    now = now or datetime.now().replace(microsecond=0)
    data = ReportData(start=start, end=end, generated_at=now, servers=list(servers or []))
    sql_srv, p_srv = _server_filter(servers)

    summaries: dict[int, FeatureSummary] = {
        f["id"]: FeatureSummary(
            feature_id=f["id"],
            server=f["server"],
            feature=f["feature"],
            product=f["product"],
            publisher=f["publisher"],
            label=f["label"],
        )
        for f in load_features(db, start, end, servers, products)
    }

    # -- agrégats horaires -------------------------------------------------
    hourly = db.query(
        "SELECT h.feature_id, h.hour_start, h.samples, h.sum_in_use, h.max_in_use, h.issued "
        "FROM usage_hourly h JOIN feature f ON f.id = h.feature_id "
        "JOIN license_server srv ON srv.id = f.server_id "
        "WHERE h.hour_start >= ? AND h.hour_start < ?" + sql_srv,
        [start, end, *p_srv],
    )
    samples_tot: dict[int, int] = defaultdict(int)
    sum_tot: dict[int, int] = defaultdict(int)
    biz_avg_sum: dict[int, float] = defaultdict(float)
    biz_issued_sum: dict[int, int] = defaultdict(int)
    daily: dict[date, dict[str, int]] = defaultdict(dict)
    saturated_by_day: dict[tuple[date, int], int] = defaultdict(int)
    peak_by_day: dict[tuple[date, int], int] = defaultdict(int)
    profile_avg: dict[tuple[int, int], float] = defaultdict(float)
    profile_issued: dict[tuple[int, int], int] = defaultdict(int)
    bh_start, bh_end = business_hours

    for fid, hour_start, samples, sum_in_use, max_in_use, issued in hourly:
        s = summaries.get(fid)
        if s is None or not samples:
            continue
        avg = sum_in_use / samples
        samples_tot[fid] += samples
        sum_tot[fid] += sum_in_use
        s.peak_in_use = max(s.peak_in_use, max_in_use)
        if issued is not None:
            s.issued = max(s.issued or 0, issued)
        day = hour_start.date()
        daily[day][s.label] = max(daily[day].get(s.label, 0), max_in_use)
        peak_by_day[(day, fid)] = max(peak_by_day[(day, fid)], max_in_use)
        if issued:
            key = (hour_start.weekday(), hour_start.hour)
            profile_avg[key] += avg
            profile_issued[key] += issued
            if max_in_use >= issued:
                s.saturated_hours += 1
                saturated_by_day[(day, fid)] += 1
            if hour_start.weekday() < 5 and bh_start <= hour_start.hour < bh_end:
                biz_avg_sum[fid] += avg
                biz_issued_sum[fid] += issued

    for fid, s in summaries.items():
        if samples_tot[fid]:
            s.avg_in_use = sum_tot[fid] / samples_tot[fid]
        if s.issued:
            s.avg_utilization = s.avg_in_use / s.issued
            s.peak_utilization = s.peak_in_use / s.issued
        if biz_issued_sum[fid]:
            s.business_utilization = biz_avg_sum[fid] / biz_issued_sum[fid]

    data.daily_peak = dict(sorted(daily.items()))
    data.hour_profile = {k: profile_avg[k] / profile_issued[k] for k in profile_issued if profile_issued[k]}

    # -- sessions ---------------------------------------------------------
    sessions = db.query_dicts(
        "SELECT s.feature_id, u.username, u.display_name, u.department, w.hostname, s.checkout_time, "
        "s.checkin_time, s.last_seen, s.license_count, s.source, s.close_reason "
        "FROM license_session s JOIN feature f ON f.id = s.feature_id "
        "JOIN license_server srv ON srv.id = f.server_id "
        "JOIN app_user u ON u.id = s.user_id JOIN workstation w ON w.id = s.workstation_id "
        "WHERE s.checkout_time < ? AND COALESCE(s.checkin_time, s.last_seen) >= ?" + sql_srv
        + " ORDER BY s.checkout_time",
        [end, start, *p_srv],
    )
    users_by_feature: dict[int, set[str]] = defaultdict(set)
    user_hours: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in sessions:
        s = summaries.get(row["feature_id"])
        if s is None:
            continue
        s_end = row["checkin_time"] or min(now, max(row["last_seen"], row["checkout_time"]))
        hours = _overlap_hours(start, end, row["checkout_time"], s_end) * (row["license_count"] or 1)
        s.sessions += 1
        s.usage_hours += hours
        users_by_feature[s.feature_id].add(row["username"])
        user_hours[row["username"]][s.product] += hours
        data.user_info.setdefault(
            row["username"], {"display_name": row["display_name"], "department": row["department"]}
        )
        data.sessions.append(
            {
                "server": s.server,
                "product": s.product,
                "feature": s.feature,
                "publisher": s.publisher,
                "username": row["username"],
                "display_name": row["display_name"],
                "department": row["department"],
                "hostname": row["hostname"],
                "checkout_time": row["checkout_time"],
                "checkin_time": row["checkin_time"],
                "licenses": row["license_count"],
                "hours_in_period": round(hours, 3),
                "source": row["source"],
                "close_reason": row["close_reason"] or ("en cours" if row["checkin_time"] is None else None),
            }
        )
    for fid, users in users_by_feature.items():
        summaries[fid].distinct_users = len(users)
    data.user_hours = {
        u: dict(per) for u, per in sorted(user_hours.items(), key=lambda kv: -sum(kv[1].values()))
    }

    # -- refus (contention) -----------------------------------------------------
    denials = db.query_dicts(
        "SELECT e.feature_id, e.event_time, u.username, w.hostname, e.detail "
        "FROM license_event e JOIN license_server srv ON srv.id = e.server_id "
        "LEFT JOIN app_user u ON u.id = e.user_id LEFT JOIN workstation w ON w.id = e.workstation_id "
        "WHERE e.event_type = 'DENIED' AND e.event_time >= ? AND e.event_time < ?" + sql_srv
        + " ORDER BY e.event_time",
        [start, end, *p_srv],
    )
    denials_by_day: dict[tuple[date, int], int] = defaultdict(int)
    for d in denials:
        s = summaries.get(d["feature_id"])
        if s is None:
            continue
        s.denials += 1
        denials_by_day[(d["event_time"].date(), s.feature_id)] += 1
        data.denials.append(
            {
                "event_time": d["event_time"],
                "server": s.server,
                "product": s.product,
                "feature": s.feature,
                "username": d["username"],
                "hostname": d["hostname"],
                "detail": d["detail"],
            }
        )

    for day, fid in sorted(set(denials_by_day) | set(saturated_by_day), key=lambda k: (k[0], summaries[k[1]].label)):
        s = summaries[fid]
        data.contention.append(
            {
                "date": day,
                "server": s.server,
                "product": s.product,
                "feature": s.feature,
                "issued": s.issued,
                "peak_in_use": peak_by_day.get((day, fid), 0),
                "saturated_hours": saturated_by_day.get((day, fid), 0),
                "denials": denials_by_day.get((day, fid), 0),
            }
        )

    # Features sans aucune activité ni sondage sur la période : écartées.
    data.summaries = sorted(
        (s for s in summaries.values() if samples_tot[s.feature_id] or s.sessions or s.denials),
        key=lambda s: (s.server, s.label),
    )
    return data


def previous_month(today: date | None = None) -> tuple[datetime, datetime]:
    """Bornes [début, fin[ du mois précédent."""
    today = today or date.today()
    first_this = today.replace(day=1)
    first_prev = (first_this - timedelta(days=1)).replace(day=1)
    return datetime.combine(first_prev, datetime.min.time()), datetime.combine(first_this, datetime.min.time())
