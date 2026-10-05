"""Prévision du besoin en jetons à 3, 6 et 12 mois.

Méthode, volontairement simple pour rester explicable à un acheteur :

1. **pic hebdomadaire** de chaque produit : plus grand nombre de jetons utilisés en même temps dans
   la semaine (maxima horaires collectés) ; Autodesk Cloud : plus grand nombre d'utilisateurs
   distincts sur une journée. Une semaine se lit sur son pic : le creux du week-end ne tire pas la
   projection vers le bas ;
2. **tendance** linéaire (moindres carrés) dès 8 semaines d'historique, prolongée au plus sur une
   durée égale à l'historique (8 semaines d'historique : tendance prolongée 8 semaines, puis palier) ;
3. **saisonnalité mensuelle** (écart moyen de chaque mois de l'année à la tendance) dès 12 mois
   d'historique : congés d'été, fin d'année, pics de projet récurrents ;
4. **fourchette à 90 %** : intervalle de prévision de la régression, tiré de l'écart type des résidus.

Jetons nécessaires = pic prévu arrondi au jeton le plus proche ; « avec marge » = haut de la fourchette,
pour qui veut ne jamais saturer. Le pic observé ne peut pas dépasser les jetons émis : en cas de refus,
le besoin réel est sous-estimé (signalé).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta

from heptagoneslm.reporting.dataset import _server_filter, load_features
from heptagoneslm.storage.db import Database

HISTORY_DAYS = 730          # historique lu : 24 mois (plus si la période choisie est plus longue)
MIN_WEEKS = 4               # en dessous : historique insuffisant
TREND_WEEKS = 8             # tendance calculée à partir de 8 semaines
SEASON_WEEKS = 52           # saisonnalité mensuelle à partir de 12 mois
MIN_DAYS_PER_WEEK = 3       # semaine retenue si au moins 3 jours collectés
HORIZONS = (3, 6, 12)       # mois
Z90 = 1.645                 # fourchette à 90 % (5 % de chances de dépasser le haut)
CHART_PRODUCTS = 4          # produits comparés sur le graphique (historique + prévision)


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    last = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(d.day, last))


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


@dataclass
class Fit:
    """Régression du pic hebdomadaire : tendance + effet de chaque mois de l'année."""

    origin: date
    a: float
    b: float                                   # jetons par semaine
    season: dict[int, float]
    sigma: float
    n: int
    xbar: float
    sxx: float
    trend: bool
    seasonal: bool
    x_last: float = 0.0                        # dernière semaine observée
    span: float = 0.0                          # durée de l'historique (semaines)

    def predict(self, d: date) -> tuple[float, float, float]:
        """(prévision, bas, haut) du pic de la semaine commençant le lundi ``d``."""
        x = min((d - self.origin).days / 7, self.x_last + self.span)   # tendance prolongée au plus d'un historique
        y = self.a + self.b * x + self.season.get(d.month, 0.0)
        lever = ((x - self.xbar) ** 2 / self.sxx) if self.trend and self.sxx else 0.0
        half = Z90 * self.sigma * math.sqrt(1 + 1 / self.n + lever)
        return max(y, 0.0), max(y - half, 0.0), max(y + half, 0.0)

    @property
    def per_month(self) -> float:
        return self.b * 52 / 12


def _ols(xs: list[float], ys: list[float]) -> tuple[float, float]:
    n = len(xs)
    xbar, ybar = sum(xs) / n, sum(ys) / n
    sxx = sum((x - xbar) ** 2 for x in xs)
    if not sxx:
        return ybar, 0.0
    b = sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys)) / sxx
    return ybar - b * xbar, b


def fit(points: list[tuple[date, float]]) -> Fit | None:
    """Ajuste le modèle sur les pics hebdomadaires ``[(lundi, pic)]`` (None : historique insuffisant)."""
    points = sorted(points)
    n = len(points)
    if n < MIN_WEEKS:
        return None
    origin = points[0][0]
    xs = [(d - origin).days / 7 for d, _ in points]
    ys = [float(v) for _, v in points]
    span = xs[-1] - xs[0] + 1
    trend, seasonal = span >= TREND_WEEKS, span >= SEASON_WEEKS
    season: dict[int, float] = {}
    a, b = sum(ys) / n, 0.0
    for _ in range(3 if seasonal else 1):          # tendance et saisonnalité estimées tour à tour
        adj = [y - season.get(d.month, 0.0) for (d, _), y in zip(points, ys)]
        a, b = _ols(xs, adj) if trend else (sum(adj) / n, 0.0)
        if seasonal:
            res: dict[int, list[float]] = defaultdict(list)
            for (d, _), x, y in zip(points, xs, ys):
                res[d.month].append(y - (a + b * x))
            eff = {m: sum(v) / len(v) for m, v in res.items()}
            mean = sum(eff.values()) / len(eff)
            season = {m: e - mean for m, e in eff.items()}
    resid = [y - (a + b * x + season.get(d.month, 0.0)) for (d, _), x, y in zip(points, xs, ys)]
    k = (2 if trend else 1) + (max(len(season) - 1, 0) if seasonal else 0)
    sigma = math.sqrt(sum(r * r for r in resid) / max(n - k, 1))
    xbar = sum(xs) / n
    return Fit(origin, a, b, season, sigma, n, xbar, sum((x - xbar) ** 2 for x in xs), trend, seasonal, xs[-1], span)


def weekly_peaks(daily: dict[date, float]) -> list[tuple[date, float]]:
    """Pic de chaque semaine (lundi) ayant au moins MIN_DAYS_PER_WEEK jours de données."""
    weeks: dict[date, list[float]] = defaultdict(list)
    for d, v in daily.items():
        weeks[monday(d)].append(v)
    return sorted((w, max(v)) for w, v in weeks.items() if len(v) >= MIN_DAYS_PER_WEEK)


@dataclass
class Series:
    """Historique et prévision d'un produit (une feature FlexLM / RLM, ou un produit Autodesk Cloud)."""

    label: str
    product: str
    server: str
    issued: int | None
    weeks: list[tuple[date, float]]
    base: date                                  # premier jour prévu
    unit_cost: float | None = None
    denials: int = 0
    fit: Fit | None = field(default=None)

    def future_weeks(self, until: date, start: date | None = None) -> list[date]:
        d, out = monday(start or self.base), []
        while d < until:
            out.append(d)
            d += timedelta(days=7)
        return out

    def window(self, start: date, end: date) -> tuple[float, float, float] | None:
        """Plus haut pic prévu (prévision, bas, haut) des semaines comprises entre ``start`` et ``end``."""
        if not self.fit:
            return None
        preds = [self.fit.predict(w) for w in self.future_weeks(end, start)]
        if not preds:
            return None
        return max(p[0] for p in preds), max(p[1] for p in preds), max(p[2] for p in preds)

    def needed(self, start: date, end: date, margin: bool = False) -> int | None:
        """Jetons couvrant le plus haut pic prévu (``margin`` : le haut de la fourchette à 90 %)."""
        w = self.window(start, end)
        if w is None:
            return None
        return math.ceil(w[2] - 1e-6) if margin else math.floor(w[0] + 0.5)

    def current_peak(self) -> float | None:
        recent = [v for d, v in self.weeks if d >= monday(self.base) - timedelta(days=28)]
        return max(recent) if recent else None


def history_bounds(p) -> tuple[datetime, datetime]:
    end = p.end
    return min(p.start, end - timedelta(days=HISTORY_DAYS)), end


def load_series(db: Database, p) -> list[Series]:
    """Historique de chaque produit retenu par les filtres, modèle ajusté."""
    from heptagoneslm.autodesk import annual_cost
    from heptagoneslm.web import named

    start, end = history_bounds(p)
    base = end.date()
    features = {f["id"]: f for f in load_features(db, start, end, p.servers or None, p.products or None)}
    sql_srv, p_srv = _server_filter(p.servers or None)
    day = db.day_of("h.hour_start")
    rows = db.query(
        f"SELECT h.feature_id, {day}, MAX(h.max_in_use), MAX(h.issued) FROM usage_hourly h "
        "JOIN feature f ON f.id = h.feature_id JOIN license_server srv ON srv.id = f.server_id "
        "WHERE h.hour_start >= ? AND h.hour_start < ? AND h.samples > 0" + sql_srv + f" GROUP BY h.feature_id, {day}",
        [start, end, *p_srv],
    )
    daily: dict[int, dict[date, float]] = defaultdict(dict)
    issued: dict[int, dict[date, int]] = defaultdict(dict)
    for fid, d, peak, iss in rows:
        if fid not in features:
            continue
        d = _as_date(d)
        daily[fid][d] = float(peak or 0)
        if iss is not None:
            issued[fid][d] = int(iss)
    denials = dict(db.query(
        "SELECT e.feature_id, COUNT(*) FROM license_event e WHERE e.event_type = 'DENIED' AND e.event_time >= ? "
        "AND e.event_time < ? AND e.feature_id IS NOT NULL GROUP BY e.feature_id", [end - timedelta(days=90), end]))
    out = []
    for fid, f in sorted(features.items(), key=lambda kv: kv[1]["label"]):
        if fid not in daily:
            continue
        iss = issued.get(fid, {})
        recent = [v for d, v in iss.items() if d >= base - timedelta(days=30)]
        s = Series(f["label"], f["product"], f["server"], max(recent or iss.values(), default=None),
                   weekly_peaks(daily[fid]), base, p.costs.get(f["product"]), int(denials.get(fid, 0)))
        s.fit = fit(s.weeks)
        out.append(s)
    nd = named.load(db, replace(p, start=start, end=end))
    if nd and nd.has_daily:
        first = min((d for per in nd.daily.values() for d in per), default=None)
        last = min(base, date.today() + timedelta(days=1))
        for prod in nd.products:
            per = nd.daily.get(prod, {})
            days = {}
            d = first
            while d is not None and d < last:          # jour sans utilisateur : 0 (l'usage est connu depuis « first »)
                days[d] = float(len(per.get(d, ())))
                d += timedelta(days=1)
            s = Series(prod, prod, named.SERVER, nd.seats.get(prod), weekly_peaks(days), base,
                       annual_cost(prod, p.named_costs))
            s.fit = fit(s.weeks)
            out.append(s)
    return out


def _advice(s: Series, needs: dict[int, int | None]) -> str:
    if not s.fit:
        return f"Historique insuffisant ({len(s.weeks)} semaine{'s' if len(s.weeks) > 1 else ''}, {MIN_WEEKS} minimum)"
    issued = s.issued or 0
    need12 = needs[HORIZONS[-1]] or 0
    if need12 > issued:
        when = next(h for h in HORIZONS if (needs[h] or 0) > issued)
        gap = need12 - issued
        text = f"Prévoir +{gap} jeton{'s' if gap > 1 else ''} d'ici {when} mois"
    elif s.denials:
        text = "Refus observés : besoin réel sous-estimé, envisager +1 jeton"
    elif need12 < issued:
        gap = issued - need12
        text = f"Excédent probable : {gap} jeton{'s' if gap > 1 else ''} à 12 mois"
    else:
        text = "Dimensionnement suffisant à 12 mois"
    if s.denials and need12 > issued:
        text += " (refus observés : besoin réel sous-estimé)"
    return text


def rows_for(series: list[Series]) -> list[dict]:
    rows = []
    for s in series:
        needs = {h: s.needed(s.base, add_months(s.base, h)) for h in HORIZONS}
        windows = {h: s.window(s.base, add_months(s.base, h)) for h in HORIZONS}
        issued = s.issued
        need12 = needs[12]
        margin12 = s.needed(s.base, add_months(s.base, 12), margin=True)
        gap = (need12 - (issued or 0)) if need12 is not None and issued is not None else None
        if gap is not None and gap < 1 and s.denials:      # refus : il manque au moins un jeton
            gap = 1
        w12 = windows[12]
        rows.append({
            "product": s.label, "server": s.server, "issued": issued,
            "current_peak": s.current_peak(),
            "trend": round(s.fit.per_month, 2) if s.fit and s.fit.trend else None,
            "peak_3": round(windows[3][0], 1) if windows[3] else None,
            "peak_6": round(windows[6][0], 1) if windows[6] else None,
            "peak_12": round(w12[0], 1) if w12 else None,
            "range_12": f"{w12[1]:.0f} – {w12[2]:.0f}" if w12 else None,
            "needed_3": needs[3], "needed_6": needs[6], "needed_12": need12, "margin_12": margin12, "gap": gap,
            "unit_cost": s.unit_cost,
            "impact": round(gap * s.unit_cost, 2) if gap is not None and s.unit_cost is not None else None,
            "weeks": len(s.weeks), "advice": _advice(s, needs),
        })
    return rows


def _month(d: date) -> str:
    return d.strftime("%Y-%m")


def chart_for(series: list[Series]) -> dict | None:
    fitted = [s for s in series if s.fit and s.weeks]
    if not fitted:
        return None
    base = fitted[0].base
    first_month = add_months(date(base.year, base.month, 1), -11)
    months = [_month(add_months(first_month, i)) for i in range(24)]

    def history(s: Series) -> dict[str, float]:
        out: dict[str, float] = {}
        for d, v in s.weeks:
            k = _month(d)
            out[k] = max(out.get(k, 0.0), v)
        return out

    def future(s: Series, i: int) -> dict[str, float]:
        out: dict[str, float] = {}
        for w in s.future_weeks(add_months(base, 12)):
            k = _month(w)
            out[k] = max(out.get(k, 0.0), round(s.fit.predict(w)[i], 2))
        return out

    series = []
    if len(fitted) == 1:
        s = fitted[0]
        hist, mid, high = history(s), future(s, 0), future(s, 2)
        last = max(hist)
        series.append({"name": "Pic observé", "values": [hist.get(m) for m in months], "color": "var(--series-1)"})
        series.append({"name": "Prévision", "values": [mid.get(m) if m > last else (hist[m] if m == last else None)
                                                       for m in months], "color": "var(--series-1)", "dash": True})
        series.append({"name": "Haut de fourchette (90 %)",
                       "values": [high.get(m) if m > last else (hist[m] if m == last else None) for m in months],
                       "color": "var(--series-4)", "dash": True})
        if s.issued is not None:          # dessinés en premier : le pic observé reste visible quand il atteint le parc
            series.insert(0, {"name": "Jetons émis", "values": [s.issued] * len(months), "color": "var(--series-2)"})
        title = f"{s.label} : pic mensuel observé et prévu"
    else:
        shown = sorted(fitted, key=lambda s: -(s.issued or 0))[:CHART_PRODUCTS]
        shown.sort(key=lambda s: s.label)
        for i, s in enumerate(shown, start=1):
            hist, mid = history(s), future(s, 0)
            last = max(hist)
            color = f"var(--series-{i})"
            series.append({"name": s.label, "values": [hist.get(m) for m in months], "color": color})
            series.append({"name": f"{s.label} · prévision",
                           "values": [mid.get(m) if m > last else (hist[m] if m == last else None) for m in months],
                           "color": color, "dash": True})
        title = "Pic mensuel observé (trait plein) et prévu (pointillés)"
    return {"kind": "line", "title": title, "format": "float", "x": months, "xType": "month", "series": series,
            "direct": False}


def report_forecast(db: Database, p) -> dict:
    from heptagoneslm.web import api

    series = load_series(db, p)
    rows = rows_for(series)
    rows.sort(key=lambda r: (-(r["gap"] or 0) if (r["gap"] or 0) > 0 else 1, r["product"]))
    C = api._col
    columns = [
        C("product", "Produit"), C("server", "Serveur"), C("issued", "Jetons émis", "int"),
        C("current_peak", "Pic des 4 dernières semaines", "float"), C("trend", "Tendance / mois", "float"),
        C("peak_3", "Pic prévu à 3 mois", "float"), C("peak_6", "Pic prévu à 6 mois", "float"),
        C("peak_12", "Pic prévu à 12 mois", "float"), C("range_12", "Fourchette à 12 mois (90 %)"),
        C("needed_3", "Nécessaires à 3 mois", "int"), C("needed_6", "Nécessaires à 6 mois", "int"),
        C("needed_12", "Nécessaires à 12 mois", "int"), C("margin_12", "Avec marge à 12 mois (90 %)", "int"),
        C("gap", "Écart à 12 mois", "int"),
        C("impact", "Impact annuel", "money"), C("weeks", "Semaines d'historique", "int"),
        C("advice", "Recommandation"),
    ]
    has_costs = any(r["unit_cost"] is not None for r in rows)
    for r in rows:
        r.pop("unit_cost", None)
    if not has_costs:
        columns = [c for c in columns if c["key"] != "impact"]
    more = [r["gap"] for r in rows if (r["gap"] or 0) > 0]
    less = [-r["gap"] for r in rows if (r["gap"] or 0) < 0]
    kpis = [
        {"label": "Produits à renforcer d'ici 12 mois", "value": len(more), "type": "int"},
        {"label": "Jetons à prévoir (12 mois)", "value": sum(more), "type": "int"},
        {"label": "Jetons excédentaires (12 mois)", "value": sum(less), "type": "int"},
    ]
    if has_costs:
        kpis.append({"label": "Budget supplémentaire / an",
                     "value": sum(r["impact"] for r in rows if (r["impact"] or 0) > 0), "type": "money"})
        kpis.append({"label": "Économie possible / an",
                     "value": -sum(r["impact"] for r in rows if (r["impact"] or 0) < 0), "type": "money"})
    _, end = history_bounds(p)
    first = min((s.weeks[0][0] for s in series if s.weeks), default=None)
    notes = [
        (f"Historique : semaines du {first:%d/%m/%Y} au {(end - timedelta(seconds=1)):%d/%m/%Y} (jusqu'à 24 mois) ; "
         if first else "Aucun historique sur les 24 derniers mois ; ")
        + "la prévision part de la fin de la période choisie.",
        "Méthode : pic hebdomadaire, tendance linéaire dès 8 semaines d'historique (prolongée au plus sur la durée de "
        "l'historique, puis palier), saisonnalité mensuelle dès 12 mois. "
        "Nécessaires = pic prévu arrondi au jeton le plus proche ; avec marge = haut de la fourchette à 90 % "
        "(saturation très improbable).",
        "Le pic observé ne peut pas dépasser les jetons émis : pour un produit avec des refus, le besoin réel est "
        "supérieur à la prévision.",
    ]
    if any(s.fit and not s.fit.seasonal for s in series):
        notes.append("Moins de 12 mois d'historique pour certains produits : saisonnalité non prise en compte "
                     "(congés, fins d'année) ; la prévision s'affine avec le temps.")
    if not has_costs and rows:
        notes.append("Renseignez le coût annuel d'un jeton (products: annual_cost, ou Administration pour Autodesk) "
                     "pour chiffrer le budget supplémentaire ou l'économie.")
    if len([s for s in series if s.fit]) > CHART_PRODUCTS:
        notes.append(f"Graphique limité aux {CHART_PRODUCTS} produits ayant le plus de jetons : filtrez un produit "
                     "pour voir sa fourchette.")
    if any(s.server == "Autodesk Cloud" for s in series):
        notes.append("Autodesk Cloud : pic = plus grand nombre d'utilisateurs distincts sur une journée ; "
                     "jetons émis = licences affectées.")
    result = api._result("forecast", "Prévision du besoin", p.describe(), columns, rows, chart_for(series), kpis, notes)
    result["currency"] = p.currency
    return result
