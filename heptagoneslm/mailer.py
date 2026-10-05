"""Envoi périodique du rapport par e-mail (abonnements).

Chaque abonnement (``email.subscriptions`` dans la configuration) définit des
destinataires, une période (mois précédent par défaut) et des filtres
(serveurs, produits). Le message contient :

* un résumé HTML (indicateurs clés, tableau par produit, points d'attention :
  produits saturés, produits sous-utilisés), avec un lien vers l'interface ;
* le classeur Excel complet en pièce jointe (si ``attach_excel``).

Rapport sur mesure : avec ``views`` (vues de l'interface : prévision, renouvellements, répartition
par projet…), le message reprend chacune de ces vues (indicateurs et premières lignes) avec les
filtres de l'abonnement (serveurs, produits, utilisateurs, services, sites), et le classeur joint
contient une feuille par vue.

Les envois sont enregistrés dans ``report_delivery`` : avec ``--skip-sent``, un
abonnement n'est envoyé qu'une fois par période, même si la tâche planifiée
tourne tous les jours (un 1er du mois manqué est rattrapé le lendemain).
``--dry-run`` écrit les messages en fichiers ``.eml`` (ouvrables dans Outlook)
sans rien envoyer.
"""

from __future__ import annotations

import html
import logging
import smtplib
import ssl
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from urllib.parse import urlencode

from heptagoneslm import __version__
from heptagoneslm.config import AppConfig, EmailConfig, Subscription
from heptagoneslm.reporting.dataset import ReportData, build_report_data, period_bounds
from heptagoneslm.storage.db import Database

log = logging.getLogger(__name__)

MONTHS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
             "septembre", "octobre", "novembre", "décembre"]
SATURATION_ALERT_SHARE = 0.05  # saturé plus de 5 % des heures ouvrées : « à surveiller »
UNDERUSED_PEAK = 0.5           # pic < 50 % des jetons : « sous-utilisé »
XLSX_TYPE = ("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ---------------------------------------------------------------------------
# Mise en forme
# ---------------------------------------------------------------------------


def _nb(value: float, decimals: int = 0) -> str:
    text = f"{value:,.{decimals}f}".replace(",", " ").replace(".", ",")
    return text


def _pct(value: float | None) -> str:
    return "—" if value is None else _nb(value * 100, 1) + " %"


def period_label(start: datetime, end: datetime) -> str:
    """« septembre 2026 » pour un mois civil complet, sinon « du … au … »."""
    next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    if start.day == 1 and start.time() == datetime.min.time() and end == next_month:
        return f"{MONTHS_FR[start.month - 1]} {start.year}"
    return f"du {start:%d/%m/%Y} au {(end - timedelta(seconds=1)):%d/%m/%Y}"


def business_hours_count(start: datetime, end: datetime, business_hours: tuple[int, int]) -> int:
    days = sum(1 for i in range((end - start).days) if (start + timedelta(days=i)).weekday() < 5)
    return days * max(0, business_hours[1] - business_hours[0])


def insights(data: ReportData, business_hours: tuple[int, int] = (8, 19)) -> tuple[list, list]:
    """(produits à surveiller, produits sous-utilisés).

    À surveiller : au moins un refus, ou saturation sur plus de 5 % des heures
    ouvrées de la période. Sous-utilisé : pic inférieur à 50 % des jetons.
    """
    threshold = max(1.0, SATURATION_ALERT_SHARE * business_hours_count(data.start, data.end, business_hours))
    hot = [s for s in data.summaries if s.denials > 0 or s.saturated_hours >= threshold]
    hot.sort(key=lambda s: (-s.denials, -s.saturated_hours))
    cold = [s for s in data.summaries
            if s.issued and s.issued >= 2 and s.peak_utilization is not None and s.peak_utilization < UNDERUSED_PEAK]
    cold.sort(key=lambda s: s.peak_utilization)
    return hot, cold


def _hot_text(s) -> str:
    advice = "jetons supplémentaires à envisager" if s.denials else "saturation fréquente, à suivre"
    return f"{s.saturated_hours} h saturée(s), {s.denials} refus — {advice}"


def report_link(public_url: str | None, sub: Subscription, start: datetime, end: datetime) -> str | None:
    if not public_url:
        return None
    params = [("type", sub.views[0] if sub.views else "summary"), ("preset", "custom"),
              ("start", start.date().isoformat()), ("end", (end - timedelta(days=1)).date().isoformat())]
    params += [("server", s) for s in sub.servers] + [("product", p) for p in sub.products]
    params += [("user", u) for u in sub.users] + [("department", d) for d in sub.departments]
    params += [("site", s) for s in sub.sites]
    if sub.views:
        params.append(("dimension", sub.dimension))
    return public_url.rstrip("/") + "/#" + urlencode(params)


def _kpis(data: ReportData) -> list[tuple[str, str]]:
    issued = sum(s.issued or 0 for s in data.summaries)
    avg = sum(s.avg_in_use for s in data.summaries if s.issued)
    users = {s["username"] for s in data.sessions}
    return [
        ("Taux d'occupation moyen", _pct(avg / issued) if issued else "—"),
        ("Heures saturées", _nb(sum(s.saturated_hours for s in data.summaries))),
        ("Refus", _nb(sum(s.denials for s in data.summaries))),
        ("Utilisateurs actifs", _nb(len(users))),
        ("Heures d'utilisation", _nb(sum(s["hours_in_period"] for s in data.sessions), 0) + " h"),
    ]


VIEW_ROWS = 15            # lignes de chaque vue reprises dans le message (le classeur joint contient tout)
VIEW_COLUMNS = 8


def _value(value, type_: str, currency: str) -> str:
    if value is None or value == "":
        return "—"
    if type_ == "pct":
        return _pct(value)
    if type_ == "money":
        return _nb(value) + " " + currency
    if type_ in ("float", "hours"):
        return _nb(value, 1 if type_ == "hours" else 2) + (" h" if type_ == "hours" else "")
    if type_ == "int":
        return _nb(value)
    if type_ == "date":
        return date.fromisoformat(str(value)[:10]).strftime("%d/%m/%Y")
    if type_ == "datetime":
        return datetime.fromisoformat(str(value)).strftime("%d/%m/%Y %H:%M")
    return str(value)


def _view_columns(result: dict) -> list[dict]:
    cols = result["columns"]
    if len(cols) <= VIEW_COLUMNS:
        return cols
    advice = [c for c in cols if c["key"] == "advice"]          # la recommandation reste visible
    return [c for c in cols if c["key"] != "advice"][:VIEW_COLUMNS - len(advice)] + advice


def render_views_html(results: list[dict]) -> str:
    e = html.escape
    cell = "padding:6px 10px;border-bottom:1px solid #e1e0d9;"
    num = cell + "text-align:right;white-space:nowrap;"
    out = []
    for r in results:
        cur = r.get("currency", "€")
        cols = _view_columns(r)
        kpis = " · ".join(f"{e(k['label'])} : <b>{e(_value(k['value'], k['type'], cur))}</b>" for k in r.get("kpis", [])[:6])
        head = "".join(f"<th style='{num if c['type'] not in ('text',) else cell}'>{e(c['label'])}</th>" for c in cols)
        body = "".join(
            "<tr>" + "".join(f"<td style='{num if c['type'] not in ('text',) else cell}'>{e(_value(row.get(c['key']), c['type'], cur))}</td>"
                             for c in cols) + "</tr>"
            for row in r["rows"][:VIEW_ROWS]
        ) or f"<tr><td colspan='{len(cols)}' style='{cell}color:#898781'>Aucune donnée sur la période.</td></tr>"
        more = (f"<div style='color:#898781;font-size:12px;margin-top:4px'>{len(r['rows'])} lignes au total : "
                "voir le classeur joint ou l'interface.</div>") if len(r["rows"]) > VIEW_ROWS else ""
        notes = "".join(f"<li>{e(n)}</li>" for n in r.get("notes", [])[:3])
        out.append(
            f"<h3 style='font-size:15px;margin:24px 0 4px'>{e(r['title'])}</h3>"
            + (f"<div style='color:#52514e;font-size:13px;margin-bottom:6px'>{kpis}</div>" if kpis else "")
            + f"<table style='border-collapse:collapse;width:100%;font-size:13px'><tr style='background:#f3f2ee;color:#52514e;"
            f"text-align:left'>{head}</tr>{body}</table>{more}"
            + (f"<ul style='margin:6px 0 0;padding-left:18px;color:#52514e;font-size:12px'>{notes}</ul>" if notes else ""))
    return "".join(out)


def render_views_text(results: list[dict]) -> list[str]:
    lines = []
    for r in results:
        cur = r.get("currency", "€")
        lines += ["", r["title"] + " :"]
        lines += [f"  {k['label']} : {_value(k['value'], k['type'], cur)}" for k in r.get("kpis", [])[:6]]
        cols = _view_columns(r)
        for row in r["rows"][:VIEW_ROWS]:
            lines.append("  - " + " · ".join(f"{c['label']} {_value(row.get(c['key']), c['type'], cur)}" for c in cols))
        if len(r["rows"]) > VIEW_ROWS:
            lines.append(f"  … {len(r['rows'])} lignes au total (classeur joint ou interface)")
    return lines


def render_html(sub: Subscription, data: ReportData, link: str | None, attached: bool, note: str | None,
                views: list[dict] | None = None) -> str:
    e = html.escape
    label = period_label(data.start, data.end)
    hot, cold = insights(data)
    cell = "padding:6px 10px;border-bottom:1px solid #e1e0d9;"
    num = cell + "text-align:right;white-space:nowrap;"
    rows = "".join(
        f"<tr><td style='{cell}'>{e(s.label)}</td><td style='{cell}color:#52514e'>{e(s.server)}</td>"
        f"<td style='{num}'>{s.issued if s.issued is not None else '—'}</td><td style='{num}'>{s.peak_in_use}</td>"
        f"<td style='{num}'>{_pct(s.avg_utilization)}</td><td style='{num}'>{_pct(s.business_utilization)}</td>"
        f"<td style='{num}'>{s.saturated_hours}</td><td style='{num}'>{s.denials}</td></tr>"
        for s in sorted(data.summaries, key=lambda s: (-(s.peak_utilization or 0), -(s.avg_utilization or 0)))
    ) or f"<tr><td colspan='8' style='{cell}color:#898781'>Aucune donnée sur la période.</td></tr>"
    tiles = "".join(
        f"<td style='padding:10px 14px;border:1px solid #e1e0d9;border-radius:8px;background:#fcfcfb'>"
        f"<div style='font-size:12px;color:#52514e'>{e(k)}</div><div style='font-size:22px;font-weight:600'>{e(v)}</div></td>"
        f"<td style='width:8px'></td>"
        for k, v in _kpis(data)
    )

    def bullet_list(items, fmt):
        return "<ul style='margin:6px 0 0;padding-left:20px'>" + "".join(f"<li>{fmt(s)}</li>" for s in items[:10]) + "</ul>"

    attention = ""
    if hot:
        attention += ("<h3 style='font-size:15px;margin:20px 0 4px'>⚠ Produits à surveiller (pénurie)</h3>"
                      + bullet_list(hot, lambda s: f"<b>{e(s.label)}</b> : {e(_hot_text(s))}"))
    if cold:
        attention += ("<h3 style='font-size:15px;margin:20px 0 4px'>Produits sous-utilisés (pistes d'économie)</h3>"
                      + bullet_list(cold, lambda s: f"<b>{e(s.label)}</b> : pic à {_pct(s.peak_utilization)} "
                                    f"({s.peak_in_use} jeton(s) sur {s.issued})"))
    footer_bits = []
    if attached:
        footer_bits.append("Le classeur Excel est joint à ce message" + (" (une feuille par vue)." if views else "."))
    if note:
        footer_bits.append(e(note))
    if link:
        footer_bits.append(f"<a href='{e(link)}' style='color:#2a78d6'>Ouvrir le rapport dans HeptagonesLM</a>")
    scope = []
    if sub.servers:
        scope.append("serveurs : " + ", ".join(sub.servers))
    if sub.products:
        scope.append("produits : " + ", ".join(sub.products))
    for what, values in (("utilisateurs", sub.users), ("services", sub.departments), ("sites", sub.sites)):
        if values:
            scope.append(f"{what} : " + ", ".join(values))
    detail = render_views_html(views) if views else f"""<h3 style="font-size:15px;margin:22px 0 6px">Détail par produit</h3>
<table style="border-collapse:collapse;width:100%;font-size:13px">
<tr style="background:#f3f2ee;color:#52514e;text-align:left">
<th style="{cell}">Produit</th><th style="{cell}">Serveur</th><th style="{num}">Jetons</th><th style="{num}">Pic</th>
<th style="{num}">Taux moyen</th><th style="{num}">Taux h. ouvrées</th><th style="{num}">H. saturées</th><th style="{num}">Refus</th></tr>
{rows}
</table>"""
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>HeptagonesLM</title></head>
<body style="margin:0;padding:24px;background:#f9f9f7;font-family:'Segoe UI',system-ui,sans-serif;color:#0b0b0b;font-size:14px">
<div style="max-width:900px;margin:0 auto;background:#ffffff;border:1px solid #e1e0d9;border-radius:10px;padding:24px">
<div style="font-size:12px;color:#2a78d6;font-weight:600;letter-spacing:.04em">HEPTAGONESLM · {e(sub.name.upper())}</div>
<h2 style="margin:4px 0 2px;font-size:20px">Utilisation des licences — {e(label)}</h2>
<div style="color:#52514e;font-size:13px">{e(' — '.join(scope) or 'Tous serveurs et produits')}</div>
<table role="presentation" style="margin:18px 0;border-collapse:separate"><tr>{tiles}</tr></table>
{attention}
{detail}
<p style="margin:18px 0 0;color:#52514e;font-size:13px">{'<br>'.join(footer_bits)}</p>
<p style="margin:18px 0 0;color:#898781;font-size:11px">Message automatique HeptagonesLM {e(__version__)} — abonnement « {e(sub.name)} ».</p>
</div></body></html>"""


def render_text(sub: Subscription, data: ReportData, link: str | None, attached: bool, note: str | None,
                views: list[dict] | None = None) -> str:
    lines = [f"HeptagonesLM — utilisation des licences — {period_label(data.start, data.end)} ({sub.name})", ""]
    lines += [f"{k} : {v}" for k, v in _kpis(data)]
    hot, cold = insights(data)
    if hot:
        lines += ["", "Produits à surveiller :"] + [f"  - {s.label} : {_hot_text(s)}" for s in hot[:10]]
    if cold:
        lines += ["", "Produits sous-utilisés :"] + [f"  - {s.label} : pic {_pct(s.peak_utilization)}" for s in cold[:10]]
    if views:
        lines += render_views_text(views)
    else:
        lines += ["", "Détail par produit :"]
        lines += [f"  - {s.label} ({s.server}) : pic {s.peak_in_use}/{s.issued}, taux moyen {_pct(s.avg_utilization)}"
                  for s in data.summaries]
    if attached:
        lines += ["", "Classeur Excel en pièce jointe."]
    if note:
        lines += ["", note]
    if link:
        lines += ["", f"Rapport en ligne : {link}"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Construction, envoi, journal
# ---------------------------------------------------------------------------


def build_message(sub: Subscription, data: ReportData, email_cfg: EmailConfig, public_url: str | None,
                  xlsx: bytes | None, xlsx_name: str, recipients: list[str],
                  views: list[dict] | None = None) -> EmailMessage:
    attached = xlsx is not None and len(xlsx) <= email_cfg.max_attachment_mb * 1024 * 1024
    note = None
    if xlsx is not None and not attached:
        note = (f"Classeur Excel non joint : {len(xlsx) / 1048576:.1f} Mo dépasse la limite de "
                f"{email_cfg.max_attachment_mb:g} Mo (le télécharger depuis l'interface).")
    link = report_link(public_url, sub, data.start, data.end)
    msg = EmailMessage()
    msg["Subject"] = sub.subject or f"HeptagonesLM — Utilisation des licences {period_label(data.start, data.end)} — {sub.name}"
    msg["From"] = email_cfg.sender
    msg["To"] = ", ".join(recipients)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="heptagoneslm")
    msg["X-Mailer"] = f"HeptagonesLM {__version__}"
    msg.set_content(render_text(sub, data, link, attached, note, views))
    msg.add_alternative(render_html(sub, data, link, attached, note, views), subtype="html")
    if attached:
        msg.add_attachment(xlsx, maintype=XLSX_TYPE[0], subtype=XLSX_TYPE[1], filename=xlsx_name)
    return msg


def send_message(msg: EmailMessage, cfg: EmailConfig) -> None:
    context = ssl.create_default_context()
    if cfg.security == "ssl":
        smtp = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=cfg.timeout_s, context=context)
    else:
        smtp = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=cfg.timeout_s)
    with smtp:
        smtp.ehlo()
        if cfg.security == "starttls":
            smtp.starttls(context=context)
            smtp.ehlo()
        if cfg.username:
            smtp.login(cfg.username, cfg.password or "")
        smtp.send_message(msg)


@dataclass
class DeliveryResult:
    subscription: str
    status: str            # SENT | SKIPPED | DRY-RUN | ERROR
    detail: str = ""
    path: Path | None = None


def already_sent(db: Database, name: str, start: datetime, end: datetime) -> bool:
    return bool(db.scalar(
        "SELECT COUNT(*) FROM report_delivery WHERE subscription = ? AND period_start = ? AND period_end = ? "
        "AND status = 'SENT'", [name, start, end]))


def _record(db: Database, sub: Subscription, start: datetime, end: datetime, recipients: list[str],
            status: str, message: str | None) -> None:
    with db.transaction():
        db.insert("report_delivery", {
            "subscription": sub.name, "period_start": start, "period_end": end,
            "sent_at": datetime.now().replace(microsecond=0), "recipients": ", ".join(recipients)[:2000],
            "status": status, "message": (message or "")[:2000] or None,
        })


def run_views(config: AppConfig, db: Database, sub: Subscription, start: datetime, end: datetime) -> list[dict]:
    """Vues choisies par l'abonnement, calculées avec ses filtres (mêmes résultats que l'interface)."""
    from heptagoneslm.web import api

    query = {"start": [start.date().isoformat()], "end": [(end - timedelta(days=1)).date().isoformat()],
             "server": sub.servers, "product": sub.products, "user": sub.users, "department": sub.departments,
             "site": sub.sites, "dimension": [sub.dimension], "granularity": ["day" if (end - start).days <= 62 else "week"]}
    p = api.params_from_config(query, config, today=end.date())
    return [api.run_report(db, v, p) for v in sub.views]


def deliver(config: AppConfig, db: Database, sub: Subscription, today: date | None = None,
            dry_run_dir: str | Path | None = None, override_to: list[str] | None = None,
            skip_sent: bool = False, period: str | None = None, sender=send_message) -> DeliveryResult:
    """Prépare et envoie (ou écrit en .eml) le rapport d'un abonnement."""
    start, end = period_bounds(period or sub.period, today)
    recipients = override_to or sub.recipients
    if skip_sent and not dry_run_dir and not override_to and already_sent(db, sub.name, start, end):
        return DeliveryResult(sub.name, "SKIPPED", f"déjà envoyé pour {period_label(start, end)}")

    data = build_report_data(db, start, end, servers=sub.servers or None,
                             business_hours=config.reporting.business_hours, products=sub.products or None)
    xlsx = None
    xlsx_name = f"HeptagonesLM_{sub.name}_{start:%Y%m%d}_{(end - timedelta(seconds=1)):%Y%m%d}.xlsx".replace(" ", "_")
    views = run_views(config, db, sub, start, end) if sub.views else None
    if views is not None and sub.attach_excel:
        from heptagoneslm.web.api import export_workbook

        xlsx = export_workbook(views)
    elif sub.attach_excel:
        import io

        from heptagoneslm.reporting.excel import build_workbook

        buf = io.BytesIO()
        build_workbook(data, config.reporting.top_n_chart).save(buf)
        xlsx = buf.getvalue()
    msg = build_message(sub, data, config.email, config.web.public_url, xlsx, xlsx_name, recipients, views)

    if dry_run_dir:
        out = Path(dry_run_dir)
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{sub.name}_{start:%Y%m%d}.eml".replace(" ", "_")
        path.write_bytes(bytes(msg))
        return DeliveryResult(sub.name, "DRY-RUN", str(path), path)

    try:
        sender(msg, config.email)
    except Exception as exc:
        log.error("Envoi « %s » impossible : %s", sub.name, exc)
        if not override_to:
            _record(db, sub, start, end, recipients, "ERROR", str(exc))
        return DeliveryResult(sub.name, "ERROR", str(exc))
    if not override_to:      # essai vers d'autres destinataires : l'envoi planifié de la période reste à faire
        _record(db, sub, start, end, recipients, "SENT", None)
    log.info("Rapport « %s » (%s) envoyé à %s", sub.name, period_label(start, end), ", ".join(recipients))
    return DeliveryResult(sub.name, "SENT", f"{period_label(start, end)} → {', '.join(recipients)}")
