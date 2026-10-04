"""Rapport de renouvellement : pour chaque échéance à venir, combien de jetons renouveler.

Échéances prises en compte (12 mois à venir, et celles dépassées depuis moins de 30 jours) :

* date d'expiration lue dans le fichier de licence (``lmstat`` / ``rlmstat``) ;
* date de contrat déclarée dans la configuration : ``renewal`` d'une règle ``products``
  (licences permanentes sous maintenance), ``autodesk.renewals`` pour les abonnements Autodesk.

Pour chacune : usage réel des 90 derniers jours au moins (pic, taux en heures ouvrées, refus),
jetons recommandés (couverture du temps ouvré, comme l'Efficacité), besoin prévu sur l'année
qui suit l'échéance (pic prévu par la Prévision), proposition de renouvellement et économie ou budget chiffrés.
Un produit avec refus ou saturations fréquentes n'est jamais proposé à la baisse.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from heptagoneslm.storage.db import Database

HORIZON_DAYS = 365
PAST_DAYS = 30              # échéances dépassées encore affichées
USAGE_DAYS = 90             # usage réel : 90 derniers jours au moins


def _match(product: str, patterns: dict[str, date]) -> date | None:
    import fnmatch

    for pattern, when in patterns.items():
        if fnmatch.fnmatch(product.lower(), str(pattern).lower()):
            return when
    return None


def _as_date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def renewals(db: Database, p, today: date | None = None) -> list[dict]:
    """Échéances de la fenêtre : [{label, product, server, expires_on, source}]."""
    from heptagoneslm.reporting.dataset import _server_filter, load_features
    from heptagoneslm.storage.repository import Repository

    today = today or min(date.today(), (p.end - timedelta(seconds=1)).date())
    lo, hi = today - timedelta(days=PAST_DAYS), today + timedelta(days=HORIZON_DAYS)
    out = []
    features = load_features(db, p.end - timedelta(days=USAGE_DAYS), p.end, p.servers or None, p.products or None)
    expiry = {}
    if Repository(db).has_column("feature", "expires_on"):
        sql_srv, p_srv = _server_filter(p.servers or None)
        expiry = {r[0]: r[1] for r in db.query(
            "SELECT f.id, f.expires_on FROM feature f JOIN license_server srv ON srv.id = f.server_id "
            "WHERE f.expires_on IS NOT NULL" + sql_srv, p_srv)}
    for f in features:
        when, source = None, None
        if f["id"] in expiry:
            when, source = _as_date(expiry[f["id"]]), "Fichier de licence"
        elif f["product"] in p.renewals:
            when, source = p.renewals[f["product"]], "Contrat (configuration)"
        if when and lo <= when <= hi:
            out.append({"label": f["label"], "product": f["product"], "server": f["server"],
                        "expires_on": when, "source": source})
    return out


def report_renewal(db: Database, p, today: date | None = None) -> dict:
    from heptagoneslm.autodesk import annual_cost
    from heptagoneslm.web import api, forecast, insights, named

    today = today or min(date.today(), (p.end - timedelta(seconds=1)).date())   # date de fin de la période
    usage_p = replace(p, start=min(p.start, p.end - timedelta(days=USAGE_DAYS)))
    opt = {r["product"]: r for r in insights.optimization_rows(db, usage_p)}
    series = {s.label: s for s in forecast.load_series(db, p)}
    rows = []
    for e in renewals(db, p, today):
        o = opt.get(e["label"])
        if not o:
            continue
        when = e["expires_on"]
        s = series.get(e["label"])
        start = max(when, today)
        need = s.needed(start, forecast.add_months(start, 12)) if s else None
        issued = o["issued"] or 0
        if o["tension"]:
            proposal = max(issued + (1 if o["denials"] else 0), need or 0)
        else:
            proposal = max(o["recommended"], need or 0)
        rows.append(_row(e, o["peak"], o["business_utilization"], o["denials"], o["recommended"], need,
                         issued, proposal, o["unit_cost"], today, o["tension"]))
    nd = named.load(db, usage_p) if p.named_renewals else None
    if nd:                      # licences nommées : une licence par utilisateur actif
        for prod in nd.products:
            when = _match(prod, p.named_renewals)
            if not when or not today - timedelta(days=PAST_DAYS) <= when <= today + timedelta(days=HORIZON_DAYS):
                continue
            issued, active = nd.seats.get(prod) or 0, nd.active.get(prod, 0)
            avg = nd.avg(prod)
            rows.append(_row({"label": prod, "server": named.SERVER, "expires_on": when, "source": "Abonnement (configuration)"},
                             nd.peak(prod), (avg / issued) if issued else None, None, active, None, issued, active,
                             annual_cost(prod, p.named_costs), today, False))
    rows.sort(key=lambda r: (r["expires_on"], r["product"]))
    has_costs = any(r["unit_cost"] is not None for r in rows)
    C = api._col
    columns = [
        C("product", "Produit"), C("server", "Serveur"), C("expires_on", "Échéance", "date"),
        C("days_left", "Jours restants", "int"), C("source", "Source"), C("issued", "Jetons actuels", "int"),
        C("peak", "Pic (90 j)", "int"), C("business_utilization", "Taux heures ouvrées", "pct"),
        C("denials", "Refus", "int"), C("recommended", "Recommandé", "int"),
        C("forecast", "Besoin prévu (12 mois après)", "int"), C("proposal", "À renouveler", "int"),
        C("delta", "Écart", "int"), C("current_cost", "Coût annuel actuel", "money"),
        C("proposed_cost", "Coût annuel proposé", "money"), C("impact", "Économie (−) / budget (+) par an", "money"),
        C("advice", "Recommandation"),
    ]
    if not has_costs:
        columns = [c for c in columns if c["type"] != "money"]
    for r in rows:
        r.pop("unit_cost", None)
    savings = -sum(r["impact"] for r in rows if (r["impact"] or 0) < 0)
    budget = sum(r["impact"] for r in rows if (r["impact"] or 0) > 0)
    upcoming = [r for r in rows if r["days_left"] >= 0]
    kpis = [
        {"label": "Échéances dans les 12 mois", "value": len(upcoming), "type": "int"},
        {"label": "Prochaine échéance", "value": upcoming[0]["expires_on"].isoformat() if upcoming else None, "type": "date"},
        {"label": "Jetons à ne pas renouveler", "value": -sum(r["delta"] for r in rows if r["delta"] < 0), "type": "int"},
        {"label": "Jetons à ajouter", "value": sum(r["delta"] for r in rows if r["delta"] > 0), "type": "int"},
    ]
    if has_costs:
        kpis += [{"label": "Économie au renouvellement / an", "value": savings, "type": "money"},
                 {"label": "Budget supplémentaire / an", "value": budget, "type": "money"}]
    items = [{"label": f"{r['product']} ({r['expires_on']:%d/%m/%Y})", "value": -r["impact"]}
             for r in sorted(rows, key=lambda r: r["impact"] or 0) if (r["impact"] or 0) < 0][:api.MAX_BAR_ITEMS]
    chart = {"kind": "bar", "format": "money", "title": "Économie annuelle au renouvellement", "items": items} if items else None
    notes = [
        f"Usage réel : {(p.end - usage_p.start).days} derniers jours jusqu'au "
        f"{(p.end - timedelta(seconds=1)):%d/%m/%Y} ; recommandé = jetons couvrant {round(p.target_coverage * 100)} % "
        "du temps ouvré ; besoin prévu = pic prévu par la Prévision sur l'année qui suit l'échéance.",
        "Un produit avec refus ou saturations fréquentes n'est jamais proposé à la baisse (un jeton de plus en cas de refus).",
    ]
    if not rows:
        notes.append("Aucune échéance dans les 12 mois : les dates viennent du fichier de licence (expiration) ou de la "
                     "configuration (renewal d'une règle products:, autodesk.renewals pour les abonnements Autodesk).")
    if not has_costs and rows:
        notes.append("Renseignez le coût annuel d'un jeton (products: annual_cost) pour chiffrer l'économie ou le budget.")
    if any(r["server"] == named.SERVER for r in rows):
        notes.append(f"{named.SERVER} : à renouveler = licences actives (utilisées depuis moins de "
                     f"{p.named_inactive_days} jours) ; les inactives sont à réaffecter ou à ne pas renouveler.")
    result = api._result("renewal", "Rapport de renouvellement", p.describe(), columns, rows, chart, kpis, notes)
    result["currency"] = p.currency
    return result


def _row(e: dict, peak, utilization, denials, recommended, need, issued: int, proposal: int, unit,
         today: date, tension: bool) -> dict:
    delta = proposal - issued
    if proposal == 0:
        advice = "Ne pas renouveler (aucun usage)"
    elif delta < 0:
        advice = f"Renouveler {proposal} jeton{'s' if proposal > 1 else ''} au lieu de {issued} (−{-delta})"
    elif delta > 0:
        advice = (f"Renouveler {proposal} jeton{'s' if proposal > 1 else ''} (+{delta})"
                  + (" : croissance prévue" if need is not None and need >= proposal else
                     " : refus ou saturations observés" if tension else ""))
    else:
        advice = "Renouveler à l'identique"
    return {
        "product": e["label"], "server": e["server"], "expires_on": e["expires_on"],
        "days_left": (e["expires_on"] - today).days, "source": e["source"], "issued": issued, "peak": peak,
        "business_utilization": utilization, "denials": denials, "recommended": recommended, "forecast": need,
        "proposal": proposal, "delta": delta, "unit_cost": unit,
        "current_cost": issued * unit if unit is not None else None,
        "proposed_cost": proposal * unit if unit is not None else None,
        "impact": delta * unit if unit is not None else None, "advice": advice,
    }
