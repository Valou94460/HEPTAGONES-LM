"""Tickets ITSM automatiques à partir des alertes (section ``alerts.itsm``).

Connecteurs : ServiceNow (API Table, incident), Jira (API REST v2, Cloud et Data
Center), GLPI (API REST) et webhook générique (JSON, Microsoft Teams, Slack).

Cycle de vie d'un ticket, pour chaque connecteur et chaque alerte acceptée
(types et gravité minimale du connecteur) :

* **ouverture** quand l'alerte est présente et qu'aucun ticket n'est ouvert pour
  elle — y compris si le connecteur était injoignable lors de la première
  évaluation (rattrapage à la collecte suivante) ;
* **commentaire** à chaque rappel de l'alerte (au plus un par heure) ;
* **résolution** quand la condition disparaît : collecte rétablie, jetons de
  nouveau disponibles, licence renouvelée ; pour les refus, après
  ``repeat_hours`` sans nouveau refus.

La table ``itsm_ticket`` mémorise les tickets ouverts (identifiant, numéro,
lien) : aucun doublon d'une évaluation à l'autre. Une erreur sur un connecteur
n'empêche ni les autres connecteurs ni l'envoi des e-mails.
"""

from __future__ import annotations

import base64
import json
import logging
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from heptagoneslm import __version__
from heptagoneslm.config import SEVERITIES, AppConfig, ItsmConfig
from heptagoneslm.storage.db import Database

log = logging.getLogger(__name__)

REMINDER_MIN_INTERVAL = timedelta(hours=1)    # au plus un commentaire de rappel par heure et par ticket
URGENCY = {"critical": 1, "serious": 2, "warning": 3, "info": 3}   # ServiceNow (1 haute – 3 basse)
GLPI_URGENCY = {"critical": 5, "serious": 4, "warning": 3, "info": 2}  # GLPI (5 très haute – 1 très basse)


class ItsmError(RuntimeError):
    """Échec d'un appel à l'outil ITSM (réseau, authentification, réponse inattendue)."""


@dataclass
class Ticket:
    ticket_id: str | None
    ref: str | None = None
    url: str | None = None


@dataclass
class SyncResult:
    events: list[str] = field(default_factory=list)   # « ServiceNow : INC0012 ouvert — … »
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Contenu des tickets
# ---------------------------------------------------------------------------


def summary(alert) -> str:
    return f"[HeptagonesLM] {alert.title}"[:250]


def description(alert, config: AppConfig, now: datetime) -> str:
    lines = [alert.detail, "",
             f"Gravité : {alert.severity}",
             f"Type d'alerte : {alert.type}",
             f"Détectée le : {now:%d/%m/%Y %H:%M}",
             f"Identifiant d'alerte : {alert.key}"]
    if config.web.public_url:
        lines.append(f"Tableau de bord : {config.web.public_url.rstrip('/')}/")
    lines += ["", f"Ticket créé automatiquement par HeptagonesLM {__version__}. Il est complété à chaque rappel "
                  "et résolu automatiquement quand la condition disparaît."]
    return "\n".join(lines)


def accepts(cfg: ItsmConfig, alert) -> bool:
    if alert.severity == "resolved" or alert.type not in cfg.types:
        return False
    return SEVERITIES.index(alert.severity) >= SEVERITIES.index(cfg.min_severity)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class Http:
    def __init__(self, cfg: ItsmConfig):
        self.cfg = cfg
        if cfg.verify_tls:
            self.context = ssl.create_default_context(cafile=cfg.ca_file) if cfg.ca_file else ssl.create_default_context()
        else:
            log.warning("SÉCURITÉ : %s — vérification du certificat TLS désactivée (verify_tls: false)", cfg.name)
            self.context = ssl._create_unverified_context()  # noqa: S323 - choix explicite de l'exploitant

    def call(self, method: str, url: str, body=None, headers: dict | None = None):
        data = None
        hdrs = {"Accept": "application/json", "User-Agent": f"HeptagonesLM/{__version__}", **(headers or {})}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout_s, context=self.context) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode("utf-8", errors="replace").strip()
            raise ItsmError(f"{self.cfg.name} : HTTP {exc.code} sur {method} {_path(url)} — {detail or exc.reason}") from None
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise ItsmError(f"{self.cfg.name} : {self.cfg.url} injoignable ({reason})") from None
        if not raw.strip():
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return raw.decode("utf-8", errors="replace")


def _path(url: str) -> str:
    return "/" + url.split("//", 1)[-1].split("/", 1)[-1] if "//" in url else url


def _basic(user: str, secret: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{secret}".encode()).decode()


# ---------------------------------------------------------------------------
# Connecteurs
# ---------------------------------------------------------------------------


class Connector:
    label = "ITSM"

    def __init__(self, cfg: ItsmConfig, config: AppConfig):
        self.cfg, self.config, self.http = cfg, config, Http(cfg)

    def open(self, alert, now: datetime) -> Ticket:
        raise NotImplementedError

    def comment(self, ticket: Ticket, text: str) -> None:
        raise NotImplementedError

    def resolve(self, ticket: Ticket, text: str) -> None:
        raise NotImplementedError

    def close(self) -> None:
        """Libère la session éventuelle (GLPI)."""


class ServiceNow(Connector):
    label = "ServiceNow"

    def _headers(self):
        return {"Authorization": _basic(self.cfg.username, self.cfg.password)}

    def open(self, alert, now):
        body = {"short_description": summary(alert)[:160], "description": description(alert, self.config, now),
                "urgency": str(URGENCY[alert.severity]), "impact": str(URGENCY[alert.severity]),
                "correlation_id": alert.key[:100], "correlation_display": "HeptagonesLM"}
        for key, value in (("assignment_group", self.cfg.assignment_group), ("caller_id", self.cfg.caller),
                           ("category", self.cfg.category)):
            if value:
                body[key] = value
        body.update(self.cfg.fields)
        res = self.http.call("POST", f"{self.cfg.url}/api/now/table/incident", body, self._headers())
        try:
            rec = res["result"]
            sys_id = rec["sys_id"]
        except (TypeError, KeyError):
            raise ItsmError(f"{self.cfg.name} : réponse inattendue à la création d'incident") from None
        return Ticket(sys_id, rec.get("number"), f"{self.cfg.url}/nav_to.do?uri=incident.do?sys_id={sys_id}")

    def comment(self, ticket, text):
        self.http.call("PATCH", f"{self.cfg.url}/api/now/table/incident/{ticket.ticket_id}",
                       {"work_notes": text}, self._headers())

    def resolve(self, ticket, text):
        self.http.call("PATCH", f"{self.cfg.url}/api/now/table/incident/{ticket.ticket_id}",
                       {"state": "6", "close_code": "Solution provided", "close_notes": text}, self._headers())


class Jira(Connector):
    label = "Jira"

    def _headers(self):
        secret = self.cfg.token or self.cfg.password
        if self.cfg.username:
            return {"Authorization": _basic(self.cfg.username, secret)}   # Jira Cloud : e-mail + jeton d'API
        return {"Authorization": f"Bearer {secret}"}                      # Data Center : jeton personnel (PAT)

    def open(self, alert, now):
        fields = {"project": {"key": self.cfg.project}, "summary": summary(alert),
                  "description": description(alert, self.config, now),
                  "issuetype": {"name": self.cfg.issue_type}, "labels": ["heptagoneslm", f"alerte-{alert.type}"]}
        fields.update(self.cfg.fields)
        res = self.http.call("POST", f"{self.cfg.url}/rest/api/2/issue", {"fields": fields}, self._headers())
        try:
            key = res["key"]
        except (TypeError, KeyError):
            raise ItsmError(f"{self.cfg.name} : réponse inattendue à la création du ticket") from None
        return Ticket(str(res.get("id") or key), key, f"{self.cfg.url}/browse/{key}")

    def comment(self, ticket, text):
        self.http.call("POST", f"{self.cfg.url}/rest/api/2/issue/{ticket.ref}/comment", {"body": text}, self._headers())

    def resolve(self, ticket, text):
        self.comment(ticket, text)
        res = self.http.call("GET", f"{self.cfg.url}/rest/api/2/issue/{ticket.ref}/transitions", None, self._headers())
        transitions = (res or {}).get("transitions", []) if isinstance(res, dict) else []
        wanted = [w.lower() for w in self.cfg.resolve_transitions]
        by_name = {t.get("name", "").lower(): t for t in transitions}
        match = next((by_name[w] for w in wanted if w in by_name), None)
        if match is None:
            names = ", ".join(t.get("name", "?") for t in transitions) or "aucune"
            raise ItsmError(f"{self.cfg.name} : aucune transition de résolution pour {ticket.ref} "
                            f"(disponibles : {names} — renseigner resolve_transitions)")
        self.http.call("POST", f"{self.cfg.url}/rest/api/2/issue/{ticket.ref}/transitions",
                       {"transition": {"id": match["id"]}}, self._headers())


class Glpi(Connector):
    label = "GLPI"

    def __init__(self, cfg, config):
        super().__init__(cfg, config)
        self.session: str | None = None

    def _headers(self):
        if self.session is None:
            res = self.http.call("GET", f"{self.cfg.url}/initSession",
                                 None, {"App-Token": self.cfg.app_token, "Authorization": f"user_token {self.cfg.user_token}"})
            try:
                self.session = res["session_token"]
            except (TypeError, KeyError):
                raise ItsmError(f"{self.cfg.name} : ouverture de session GLPI refusée") from None
        return {"App-Token": self.cfg.app_token, "Session-Token": self.session}

    def _web(self) -> str:
        base = self.cfg.url
        return base[: -len("/apirest.php")] if base.endswith("/apirest.php") else base

    def open(self, alert, now):
        body = {"name": summary(alert), "content": description(alert, self.config, now).replace("\n", "<br>"),
                "urgency": GLPI_URGENCY[alert.severity], "type": 1}
        if self.cfg.entity_id is not None:
            body["entities_id"] = self.cfg.entity_id
        if self.cfg.category_id is not None:
            body["itilcategories_id"] = self.cfg.category_id
        body.update(self.cfg.fields)
        res = self.http.call("POST", f"{self.cfg.url}/Ticket", {"input": body}, self._headers())
        try:
            tid = str(res["id"] if isinstance(res, dict) else res[0]["id"])
        except (TypeError, KeyError, IndexError):
            raise ItsmError(f"{self.cfg.name} : réponse inattendue à la création du ticket") from None
        return Ticket(tid, tid, f"{self._web()}/front/ticket.form.php?id={tid}")

    def comment(self, ticket, text):
        self.http.call("POST", f"{self.cfg.url}/Ticket/{ticket.ticket_id}/ITILFollowup",
                       {"input": {"itemtype": "Ticket", "items_id": int(ticket.ticket_id), "content": text}},
                       self._headers())

    def resolve(self, ticket, text):
        self.http.call("POST", f"{self.cfg.url}/Ticket/{ticket.ticket_id}/ITILSolution",
                       {"input": {"itemtype": "Ticket", "items_id": int(ticket.ticket_id), "content": text}},
                       self._headers())

    def close(self):
        if self.session:
            try:
                self.http.call("GET", f"{self.cfg.url}/killSession", None,
                               {"App-Token": self.cfg.app_token, "Session-Token": self.session})
            except ItsmError as exc:
                log.debug("Fermeture de session GLPI : %s", exc)
            self.session = None


class Webhook(Connector):
    label = "Webhook"

    def _post(self, event: str, alert_like: dict, text: str):
        link = self.config.web.public_url
        if self.cfg.format == "json":
            body = {"source": "HeptagonesLM", "version": __version__, "event": event, "alert": alert_like,
                    "message": text, "link": link, "at": datetime.now().replace(microsecond=0).isoformat()}
        elif self.cfg.format == "slack":
            body = {"text": f"*{alert_like['title']}*\n{text}" + (f"\n<{link}|Tableau de bord>" if link else "")}
        else:  # teams
            body = {"text": f"**{alert_like['title']}**\n\n{text}" + (f"\n\n[Tableau de bord]({link})" if link else "")}
        self.http.call("POST", self.cfg.url, body, self.cfg.headers)

    def open(self, alert, now):
        self._post("opened", {"key": alert.key, "type": alert.type, "severity": alert.severity,
                              "title": alert.title, "detail": alert.detail}, alert.detail)
        return Ticket(None)

    def comment(self, ticket, text):
        self._post("reminder", {"key": ticket.alert_key, "title": ticket.title}, text)

    def resolve(self, ticket, text):
        self._post("resolved", {"key": ticket.alert_key, "title": ticket.title, "severity": "resolved"}, text)


CONNECTORS = {"servicenow": ServiceNow, "jira": Jira, "glpi": Glpi, "webhook": Webhook}


def make_connector(cfg: ItsmConfig, config: AppConfig) -> Connector:
    return CONNECTORS[cfg.type](cfg, config)


# ---------------------------------------------------------------------------
# Synchronisation avec les alertes
# ---------------------------------------------------------------------------


def _as_dt(value):
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value)[:19])


def open_tickets(db: Database) -> dict[tuple[str, str], dict]:
    rows = db.query_dicts("SELECT id, connector, alert_key, ticket_id, ticket_ref, url, title, opened_at, updated_at "
                          "FROM itsm_ticket WHERE status = 'OPEN'")
    for r in rows:
        r["opened_at"], r["updated_at"] = _as_dt(r["opened_at"]), _as_dt(r["updated_at"])
    return {(r["connector"].lower(), r["alert_key"]): r for r in rows}


def _ticket(row: dict) -> Ticket:
    t = Ticket(row["ticket_id"], row["ticket_ref"], row["url"])
    t.alert_key, t.title = row["alert_key"], row.get("title") or row["alert_key"]
    return t


def _ref(label: str, ticket: Ticket | dict) -> str:
    ref = ticket.ref if isinstance(ticket, Ticket) else ticket.get("ticket_ref")
    return f"{label} {ref}" if ref else label


def sync(config: AppConfig, db: Database, now: datetime, current: list, sent: list, resolved: list) -> SyncResult:
    """Ouvre, complète et résout les tickets selon l'état des alertes."""
    result = SyncResult()
    rules = config.alerts
    tickets = open_tickets(db)
    present = {a.key: a for a in current}
    sent_keys = {a.key for a in sent}
    resolved_note = {a.key: a.title for a in resolved}
    candidates = list(current) + [a for a in sent if a.key not in present]      # refus : alertes « évènement »
    repeat = timedelta(hours=rules.repeat_hours)

    for cfg in rules.itsm:
        conn = make_connector(cfg, config)
        name = cfg.name.lower()
        try:
            for alert in candidates:
                if not accepts(cfg, alert):
                    continue
                row = tickets.get((name, alert.key))
                try:
                    if row is None:
                        t = conn.open(alert, now)
                        with db.transaction():
                            db.insert("itsm_ticket", {
                                "connector": cfg.name, "alert_key": alert.key, "ticket_id": t.ticket_id,
                                "ticket_ref": t.ref, "url": t.url, "title": alert.title[:500], "status": "OPEN",
                                "opened_at": now, "updated_at": now})
                        result.events.append(f"{_ref(conn.label, t)} ouvert — {alert.title}")
                    elif alert.key in sent_keys and now - row["updated_at"] >= REMINDER_MIN_INTERVAL:
                        conn.comment(_ticket(row), f"Rappel {now:%d/%m/%Y %H:%M} : {alert.title} — {alert.detail}")
                        _touch(db, row["id"], now)
                        result.events.append(f"{_ref(conn.label, row)} complété — {alert.title}")
                except ItsmError as exc:
                    result.errors.append(str(exc))
            for (cname, key), row in tickets.items():
                if cname != name or key in present:
                    continue
                if key.startswith("denial:"):
                    if key in sent_keys or now - row["updated_at"] < repeat:
                        continue
                    note = f"Aucun nouveau refus depuis {rules.repeat_hours:g} h : ticket résolu automatiquement."
                elif key in resolved_note:
                    note = f"{resolved_note[key]} le {now:%d/%m/%Y à %H:%M} : ticket résolu automatiquement."
                else:
                    note = (f"Condition disparue le {now:%d/%m/%Y à %H:%M} (jetons de nouveau disponibles, licence "
                            "renouvelée ou alerte désactivée) : ticket résolu automatiquement.")
                try:
                    conn.resolve(_ticket(row), note)
                    with db.transaction():
                        db.execute("UPDATE itsm_ticket SET status = 'RESOLVED', updated_at = ?, resolved_at = ? "
                                   "WHERE id = ?", [now, now, row["id"]])
                    result.events.append(f"{_ref(conn.label, row)} résolu — {row.get('title') or key}")
                except ItsmError as exc:
                    result.errors.append(str(exc))
        finally:
            conn.close()
    for e in result.events:
        log.info("ITSM : %s", e)
    for e in result.errors:
        log.error("ITSM : %s", e)
    return result


def _touch(db: Database, ticket_row_id: int, now: datetime) -> None:
    with db.transaction():
        db.execute("UPDATE itsm_ticket SET updated_at = ? WHERE id = ?", [now, ticket_row_id])


def test_connector(cfg: ItsmConfig, config: AppConfig, resolve: bool = True) -> list[str]:
    """Ouvre un ticket de test, le commente puis le résout (vérification d'installation)."""
    from heptagoneslm.alerts import Alert

    now = datetime.now().replace(microsecond=0)
    alert = Alert(f"test:{now:%Y%m%d%H%M%S}", "collection", "info", "Test de connexion HeptagonesLM",
                  "Ticket de test créé par « heptagoneslm test-itsm » : il peut être ignoré.")
    conn = make_connector(cfg, config)
    steps = []
    try:
        t = conn.open(alert, now)
        t.alert_key, t.title = alert.key, alert.title
        steps.append(f"ouverture : {t.ref or 'OK'}" + (f" ({t.url})" if t.url else ""))
        conn.comment(t, "Commentaire de test HeptagonesLM.")
        steps.append("commentaire : OK")
        if resolve:
            conn.resolve(t, "Test terminé : ticket résolu automatiquement.")
            steps.append("résolution : OK")
    finally:
        conn.close()
    return steps
