"""Alertes immédiates par e-mail et tickets ITSM (section ``alerts`` de la configuration).

Évaluées après chaque collecte (``heptagoneslm collect``) ou à la demande
(``heptagoneslm check-alerts``), elles préviennent sans attendre le rapport
mensuel :

* ``collection`` : collecte en échec sur un serveur (puis message « rétabli »
  quand elle repart), collecte arrêtée depuis plus d'une heure ;
* ``denial``     : nouveaux refus de licence sur un produit ;
* ``saturation`` : tous les jetons d'une feature utilisés au dernier sondage ;
* ``expiry``     : licence qui expire dans ``expiry_days`` jours, 7 jours, ou expirée.

Toutes les alertes nouvelles d'une évaluation partent dans un seul message.
La table ``alert_state`` évite les répétitions : une alerte toujours présente
n'est renvoyée qu'après ``repeat_hours`` ; un palier d'échéance n'est envoyé
qu'une fois ; les refus sont suivis par un curseur (seuls les nouveaux refus
sont signalés). L'état n'est mis à jour qu'après un envoi réussi : un serveur
SMTP indisponible ne fait perdre aucune alerte.
"""

from __future__ import annotations

import html
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from heptagoneslm import __version__
from heptagoneslm.config import AlertConfig, AppConfig
from heptagoneslm.storage.db import Database

log = logging.getLogger(__name__)

STALE_COLLECTION = timedelta(minutes=60)     # sans collecte au-delà : « collecte arrêtée »
SATURATION_WINDOW = timedelta(minutes=15)    # âge maximal du dernier sondage pris en compte
DENIAL_FIRST_LOOKBACK = timedelta(hours=1)   # à la mise en service : refus de la dernière heure
EXPIRY_CRITICAL_DAYS = 7
MAX_DENIAL_ROWS = 20000

SEVERITY_ORDER = {"critical": 0, "serious": 1, "warning": 2, "info": 3, "resolved": 4}
SEVERITY_LABEL = {"critical": "Critique", "serious": "Important", "warning": "À surveiller",
                  "info": "Information", "resolved": "Rétabli"}
SEVERITY_COLOR = {"critical": "#c62828", "serious": "#d9730d", "warning": "#b8860b",
                  "info": "#2a78d6", "resolved": "#2e7d32"}


@dataclass
class Alert:
    key: str
    type: str                  # collection | denial | saturation | expiry
    severity: str              # critical | serious | warning | info | resolved
    title: str
    detail: str
    repeat: bool = True        # renvoyée après repeat_hours si toujours présente
    cursor: int | None = None  # refus : dernier évènement signalé


@dataclass
class AlertResult:
    status: str                # SENT | NONE | DRY-RUN | ERROR | DISABLED
    alerts: list[Alert] = field(default_factory=list)
    detail: str = ""
    path: Path | None = None
    tickets: list[str] = field(default_factory=list)       # actions ITSM (ouverture, rappel, résolution)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# État
# ---------------------------------------------------------------------------


def _as_dt(value) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value)[:19])


def load_states(db: Database) -> dict[str, dict]:
    rows = db.query_dicts("SELECT alert_key, active, first_seen, last_sent, last_event_id, title FROM alert_state")
    for r in rows:
        r["active"] = bool(r["active"])
        r["last_sent"] = _as_dt(r["last_sent"])
        r["first_seen"] = _as_dt(r["first_seen"])
    return {r["alert_key"]: r for r in rows}


def _save_state(db: Database, states: dict[str, dict], key: str, now: datetime, *, active: bool,
                sent: bool = False, cursor: int | None = None, title: str | None = None) -> None:
    st = states.get(key)
    if st is None:
        db.execute(
            "INSERT INTO alert_state (alert_key, active, first_seen, last_sent, last_event_id, title) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [key, 1 if active else 0, now, now if sent else None, cursor, (title or "")[:500] or None])
        states[key] = {"alert_key": key, "active": active, "first_seen": now,
                       "last_sent": now if sent else None, "last_event_id": cursor, "title": title}
        return
    st["active"] = active
    if sent:
        st["last_sent"] = now
    if cursor is not None:
        st["last_event_id"] = cursor
    if title:
        st["title"] = title
    db.execute(
        "UPDATE alert_state SET active = ?, last_sent = ?, last_event_id = ?, title = ? WHERE alert_key = ?",
        [1 if active else 0, st["last_sent"], st["last_event_id"], (st["title"] or "")[:500] or None, key])


# ---------------------------------------------------------------------------
# Détection
# ---------------------------------------------------------------------------


def _server_ok(cfg: AlertConfig, server: str) -> bool:
    return not cfg.servers or server.lower() in {s.lower() for s in cfg.servers}


def _product_ok(cfg: AlertConfig, product: str) -> bool:
    return not cfg.products or product.lower() in {p.lower() for p in cfg.products}


def _collection_alerts(config: AppConfig, db: Database, now: datetime) -> list[Alert]:
    cfg = config.alerts
    enabled = {s.name.lower() for s in config.servers if s.enabled}
    rows = db.query_dicts(
        "SELECT srv.name AS server, r.collector, r.status, r.message, r.started_at "
        "FROM collection_run r JOIN license_server srv ON srv.id = r.server_id "
        "WHERE r.id IN (SELECT MAX(id) FROM collection_run GROUP BY server_id, collector)")
    out = []
    last = None
    for r in rows:
        if r["server"].lower() not in enabled or not _server_ok(cfg, r["server"]):
            continue
        started = _as_dt(r["started_at"])
        last = max(last, started) if last else started
        if r["status"] == "ERROR":
            out.append(Alert(f"collection:{r['server']}:{r['collector']}", "collection", "critical",
                             f"Collecte en échec : {r['server']}",
                             f"{r['collector']} — {r['message'] or 'erreur'} (depuis le {started:%d/%m %H:%M})"))
    if last and now - last > STALE_COLLECTION:
        hours = (now - last).total_seconds() / 3600
        out.append(Alert("collection:stale", "collection", "critical", "Collecte arrêtée",
                         f"Aucune collecte depuis {hours:.0f} h (dernière : {last:%d/%m %H:%M}). "
                         "Vérifier la tâche planifiée « Collecte »."))
    return out


def _denial_alerts(config: AppConfig, db: Database, now: datetime, states: dict[str, dict]) -> list[Alert]:
    cfg = config.alerts
    repeat = timedelta(hours=cfg.repeat_hours)
    since = now - max(repeat, DENIAL_FIRST_LOOKBACK) - timedelta(hours=1)
    rows = db.query_dicts(
        "SELECT e.id, e.event_time, srv.name AS server, COALESCE(f.product, f.name) AS product, "
        "u.username, w.hostname FROM license_event e "
        "JOIN license_server srv ON srv.id = e.server_id "
        "LEFT JOIN feature f ON f.id = e.feature_id "
        "LEFT JOIN app_user u ON u.id = e.user_id "
        "LEFT JOIN workstation w ON w.id = e.workstation_id "
        "WHERE e.event_type = 'DENIED' AND e.event_time >= ? ORDER BY e.id", [since])[:MAX_DENIAL_ROWS]
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        product = r["product"] or "?"
        if _server_ok(cfg, r["server"]) and _product_ok(cfg, product):
            groups[(r["server"], product)].append(r)
    out = []
    for (server, product), events in sorted(groups.items()):
        key = f"denial:{server}:{product}"
        st = states.get(key)
        if st and st["last_event_id"] is not None:
            events = [e for e in events if e["id"] > st["last_event_id"]]
        else:
            events = [e for e in events if _as_dt(e["event_time"]) >= now - DENIAL_FIRST_LOOKBACK]
        if len(events) < cfg.min_denials:
            continue
        if st and st["last_sent"] and now - st["last_sent"] < repeat:
            continue      # regroupés dans le prochain message (le curseur n'avance pas)
        users = defaultdict(int)
        for e in events:
            users[e["username"] or "?"] += 1
        top = ", ".join(f"{u} ({n})" if n > 1 else u for u, n in sorted(users.items(), key=lambda x: -x[1])[:5])
        first, last = _as_dt(events[0]["event_time"]), _as_dt(events[-1]["event_time"])
        span = f"le {first:%d/%m à %H:%M}" if first == last else f"du {first:%d/%m %H:%M} au {last:%d/%m %H:%M}"
        out.append(Alert(key, "denial", "serious", f"{len(events)} refus de licence : {product}",
                         f"Serveur {server}, {span} — utilisateurs : {top}"
                         + (" …" if len(users) > 5 else ""), cursor=max(e["id"] for e in events)))
    return out


def _saturation_alerts(config: AppConfig, db: Database, now: datetime) -> list[Alert]:
    cfg = config.alerts
    rows = db.query_dicts(
        "SELECT srv.name AS server, f.name AS feature, COALESCE(f.product, f.name) AS product, "
        "s.issued, s.in_use, s.queued, s.snapshot_time FROM usage_snapshot s "
        "JOIN feature f ON f.id = s.feature_id JOIN license_server srv ON srv.id = f.server_id "
        "WHERE s.id IN (SELECT MAX(id) FROM usage_snapshot WHERE snapshot_time >= ? GROUP BY feature_id)",
        [now - SATURATION_WINDOW])
    out = []
    for r in rows:
        if not r["issued"] or r["in_use"] < r["issued"]:
            continue
        if not (_server_ok(cfg, r["server"]) and _product_ok(cfg, r["product"])):
            continue
        name = r["product"] if r["product"] == r["feature"] else f"{r['product']} ({r['feature']})"
        queued = f", {r['queued']} en file d'attente" if r["queued"] else ""
        out.append(Alert(f"saturation:{r['server']}:{r['feature']}", "saturation", "warning",
                         f"Plus aucun jeton disponible : {name}",
                         f"Serveur {r['server']} : {r['in_use']}/{r['issued']} jetons utilisés{queued} "
                         f"({_as_dt(r['snapshot_time']):%d/%m %H:%M})."))
    return out


def _expiry_alerts(config: AppConfig, db: Database, today: date) -> list[Alert]:
    from heptagoneslm.storage.repository import Repository

    cfg = config.alerts
    if not Repository(db).has_column("feature", "expires_on"):
        return []
    rows = db.query_dicts(
        "SELECT srv.name AS server, f.name AS feature, COALESCE(f.product, f.name) AS product, f.expires_on "
        "FROM feature f JOIN license_server srv ON srv.id = f.server_id "
        "WHERE f.expires_on IS NOT NULL AND f.expires_on <= ?", [today + timedelta(days=cfg.expiry_days)])
    out = []
    for r in rows:
        if not (_server_ok(cfg, r["server"]) and _product_ok(cfg, r["product"])):
            continue
        d = r["expires_on"] if isinstance(r["expires_on"], date) else date.fromisoformat(str(r["expires_on"])[:10])
        days = (d - today).days
        if days < 0:
            stage, severity, title = "expired", "critical", f"Licence expirée : {r['product']}"
        elif days <= EXPIRY_CRITICAL_DAYS:
            stage, severity, title = f"{EXPIRY_CRITICAL_DAYS}d", "critical", f"Expiration dans {days} j : {r['product']}"
        else:
            stage, severity, title = f"{cfg.expiry_days}d", "warning", f"Expiration dans {days} j : {r['product']}"
        out.append(Alert(f"expiry:{r['server']}:{r['feature']}:{stage}", "expiry", severity, title,
                         f"{r['feature']} sur {r['server']} — échéance le {d:%d/%m/%Y}. Anticiper le renouvellement.",
                         repeat=False))
    return out


def evaluate(config: AppConfig, db: Database, now: datetime | None = None,
             states: dict[str, dict] | None = None) -> tuple[list[Alert], list[Alert], set[str]]:
    """Retourne (alertes à envoyer, retours à la normale, clés actuellement présentes)."""
    to_send, resolved, keys, _ = evaluate_all(config, db, now, states)
    return to_send, resolved, keys


def evaluate_all(config: AppConfig, db: Database, now: datetime | None = None,
                 states: dict[str, dict] | None = None) -> tuple[list[Alert], list[Alert], set[str], list[Alert]]:
    """Comme ``evaluate``, plus la liste des alertes présentes (hors refus), utile aux tickets ITSM."""
    cfg = config.alerts
    now = now or datetime.now().replace(microsecond=0)
    states = load_states(db) if states is None else states
    current: list[Alert] = []
    if "collection" in cfg.types:
        current += _collection_alerts(config, db, now)
    if "saturation" in cfg.types:
        current += _saturation_alerts(config, db, now)
    if "expiry" in cfg.types:
        current += _expiry_alerts(config, db, now.date())
    repeat = timedelta(hours=cfg.repeat_hours)
    to_send = []
    for a in current:
        st = states.get(a.key)
        if st and st["active"] and st["last_sent"]:
            if not a.repeat or now - st["last_sent"] < repeat:
                continue
        to_send.append(a)
    if "denial" in cfg.types:
        to_send += _denial_alerts(config, db, now, states)
    keys = {a.key for a in current}
    resolved = []
    for key, st in states.items():
        if st["active"] and st["last_sent"] and key.startswith("collection:") and key not in keys:
            title = st["title"] or key
            resolved.append(Alert(key, "collection", "resolved", title.replace("Collecte en échec", "Collecte rétablie")
                                  .replace("Collecte arrêtée", "Collecte relancée"),
                                  "La dernière collecte s'est déroulée normalement."))
    to_send.sort(key=lambda a: (SEVERITY_ORDER[a.severity], a.title))
    return to_send, resolved, keys, current


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------


def _subject(alerts: list[Alert]) -> str:
    first = alerts[0].title
    if len(alerts) == 1:
        return f"HeptagonesLM — {first}"
    return f"HeptagonesLM — {len(alerts)} alertes : {first}, …"


def render_html(alerts: list[Alert], now: datetime, link: str | None, repeat_hours: float = 24) -> str:
    e = html.escape
    rows = "".join(
        f"<tr><td style='padding:8px 10px;border-bottom:1px solid #e1e0d9;white-space:nowrap;vertical-align:top'>"
        f"<span style='display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;color:#fff;"
        f"background:{SEVERITY_COLOR[a.severity]}'>{SEVERITY_LABEL[a.severity]}</span></td>"
        f"<td style='padding:8px 10px;border-bottom:1px solid #e1e0d9'><b>{e(a.title)}</b>"
        f"<div style='color:#52514e;font-size:13px;margin-top:2px'>{e(a.detail)}</div></td></tr>"
        for a in alerts)
    footer = (f"<a href='{e(link)}' style='color:#2a78d6'>Ouvrir le tableau de bord HeptagonesLM</a><br>"
              if link else "")
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>HeptagonesLM</title></head>
<body style="margin:0;padding:24px;background:#f9f9f7;font-family:'Segoe UI',system-ui,sans-serif;color:#0b0b0b;font-size:14px">
<div style="max-width:760px;margin:0 auto;background:#ffffff;border:1px solid #e1e0d9;border-radius:10px;padding:24px">
<div style="font-size:12px;color:#2a78d6;font-weight:600;letter-spacing:.04em">HEPTAGONESLM · ALERTES</div>
<h2 style="margin:4px 0 14px;font-size:20px">{len(alerts)} alerte(s) le {now:%d/%m/%Y à %H:%M}</h2>
<table style="border-collapse:collapse;width:100%">{rows}</table>
<p style="margin:18px 0 0;color:#52514e;font-size:13px">{footer}</p>
<p style="margin:18px 0 0;color:#898781;font-size:11px">Message automatique HeptagonesLM {e(__version__)} — une alerte
toujours présente est rappelée au plus toutes les {repeat_hours:g} h (paramètre alerts.repeat_hours).</p>
</div></body></html>"""


def render_text(alerts: list[Alert], now: datetime, link: str | None) -> str:
    lines = [f"HeptagonesLM — {len(alerts)} alerte(s) le {now:%d/%m/%Y à %H:%M}", ""]
    for a in alerts:
        lines += [f"[{SEVERITY_LABEL[a.severity]}] {a.title}", f"    {a.detail}", ""]
    if link:
        lines.append(f"Tableau de bord : {link}")
    return "\n".join(lines) + "\n"


def build_message(config: AppConfig, alerts: list[Alert], now: datetime) -> EmailMessage:
    link = config.web.public_url.rstrip("/") + "/" if config.web.public_url else None
    msg = EmailMessage()
    msg["Subject"] = _subject(alerts)
    msg["From"] = config.email.sender
    msg["To"] = ", ".join(config.alerts.recipients)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="heptagoneslm")
    msg["X-Mailer"] = f"HeptagonesLM {__version__}"
    if any(a.severity == "critical" for a in alerts):
        msg["X-Priority"] = "1"
        msg["Importance"] = "High"
    msg.set_content(render_text(alerts, now, link))
    msg.add_alternative(render_html(alerts, now, link, config.alerts.repeat_hours), subtype="html")
    return msg


# ---------------------------------------------------------------------------
# Évaluation + envoi
# ---------------------------------------------------------------------------


def check_and_notify(config: AppConfig, db: Database, now: datetime | None = None,
                     dry_run_dir: str | Path | None = None, sender=None) -> AlertResult:
    """Évalue les alertes, envoie l'e-mail, synchronise les tickets ITSM et met à jour l'état.

    ``--dry-run`` : écrit l'e-mail en .eml, sans rien envoyer, sans ticket ni mise à jour de l'état.
    L'état n'est mis à jour que si l'e-mail est parti (ou si aucun destinataire n'est configuré) :
    une alerte n'est jamais perdue. Les tickets ITSM se rattrapent d'eux-mêmes à l'évaluation suivante.
    """
    rules = config.alerts
    if not rules:
        return AlertResult("DISABLED", detail="section alerts absente")
    if sender is None:
        from heptagoneslm.mailer import send_message as sender
    now = now or datetime.now().replace(microsecond=0)
    states = load_states(db)
    to_send, resolved, keys, current = evaluate_all(config, db, now, states)
    message_alerts = to_send + resolved

    if dry_run_dir:
        if not message_alerts:
            return AlertResult("NONE", [], "aucune nouvelle alerte")
        out = Path(dry_run_dir)
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"alertes_{now:%Y%m%d_%H%M%S}.eml"
        path.write_bytes(bytes(build_message(config, message_alerts, now)))
        return AlertResult("DRY-RUN", message_alerts, str(path), path)

    result = AlertResult("NONE", message_alerts)
    if rules.itsm:
        from heptagoneslm import itsm

        synced = itsm.sync(config, db, now, current, to_send, resolved)
        result.tickets, result.errors = synced.events, list(synced.errors)

    email_ok = True
    if message_alerts and rules.recipients:
        try:
            sender(build_message(config, message_alerts, now), config.email)
        except Exception as exc:
            log.error("Envoi des alertes impossible : %s", exc)
            _journal(db, now, rules.recipients, "ERROR", str(exc))
            result.errors.insert(0, f"e-mail : {exc}")
            email_ok = False
        else:
            _journal(db, now, rules.recipients, "SENT", "; ".join(a.title for a in message_alerts))
            log.info("%d alerte(s) envoyée(s) à %s", len(message_alerts), ", ".join(rules.recipients))

    if email_ok:
        with db.transaction():
            for a in to_send:
                _save_state(db, states, a.key, now, active=True, sent=True, cursor=a.cursor, title=a.title)
            for a in resolved:
                _save_state(db, states, a.key, now, active=False)
            # Conditions disparues sans message (saturation terminée, licence renouvelée) : réarmées.
            for key, st in list(states.items()):
                if st["active"] and key not in keys and not key.startswith(("denial:", "collection:")):
                    _save_state(db, states, key, now, active=False)

    parts = []
    if message_alerts and rules.recipients and email_ok:
        parts.append(f"{len(message_alerts)} alerte(s) → " + ", ".join(rules.recipients))
    elif message_alerts and not rules.recipients:
        parts.append(f"{len(message_alerts)} alerte(s)")
    if result.tickets:
        parts.append(f"{len(result.tickets)} action(s) ITSM")
    if result.errors:
        parts.append("erreurs : " + " ; ".join(result.errors))
    result.detail = " — ".join(parts) or "aucune nouvelle alerte"
    if result.errors:
        result.status = "ERROR"
    elif message_alerts or result.tickets:
        result.status = "SENT"
    return result


def _journal(db: Database, now: datetime, recipients: list[str], status: str, message: str | None) -> None:
    with db.transaction():
        db.insert("report_delivery", {
            "subscription": "Alertes", "period_start": now, "period_end": now, "sent_at": now,
            "recipients": ", ".join(recipients)[:2000], "status": status,
            "message": (message or "")[:2000] or None,
        })
