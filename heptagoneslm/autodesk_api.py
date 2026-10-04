"""Connecteur API Autodesk « Business Success Plan Reporting » (Insights) : usage des licences nommées.

Complète l'import des exports du portail (``heptagoneslm.autodesk``) : l'usage des licences Autodesk
nominatives est lu directement chez Autodesk, sans export manuel.

* Prérequis Autodesk : abonnement Business Success Plan (anciennement Premium) avec l'avantage
  « Reporting API », et une application créée sur aps.autodesk.com (Client ID, et Client Secret pour
  une application « Traditional Web App ») dont l'URL de rappel est
  ``http://localhost:<callback_port>/autodesk/callback``.
* Connexion (une fois) : ``heptagoneslm autodesk-login`` ouvre la page de connexion Autodesk ; un
  administrateur de l'équipe s'y connecte (OAuth 3 étapes, PKCE). Le jeton de renouvellement est
  conservé chiffré par Windows (DPAPI) et renouvelé à chaque synchronisation : il expire s'il n'est
  pas utilisé pendant 15 jours (relancer alors ``autodesk-login``).
* Synchronisation : ``heptagoneslm autodesk-sync``, ou automatiquement par la collecte au plus toutes
  les ``sync_hours`` heures. Une requête d'usage (``POST /insights/v1/usage-queries``, asynchrone) donne
  par utilisateur et par produit la première et la dernière utilisation et le nombre de jours
  d'utilisation sur ``lookback_days`` jours.

L'API ne renvoie que les utilisateurs qui ont utilisé un produit sur la période : les licences
affectées connues par le dernier export du portail (ou la dernière synchronisation) sont reprises,
avec leur dernière utilisation connue, pour que les licences inactives ou jamais utilisées restent
visibles.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import logging
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from heptagoneslm import __version__, vault
from heptagoneslm.autodesk import VENDOR, SeatRow, parse_date, previous_rows, store_rows
from heptagoneslm.config import AutodeskApiConfig
from heptagoneslm.storage.db import Database

log = logging.getLogger(__name__)

SCOPES = "data:read data:write"
SYNC_VENDOR = "autodesk-api"            # entrées du journal named_import propres aux synchronisations
REPORTING_BENEFIT = "ReportingAPIAccess"
PAGE = 100
QUERY_TIMEOUT_S = 600

# Requête d'usage : la liste des champs est le regroupement (une ligne par utilisateur et par produit).
SPEC_USAGE = {"fields": ["fullName", "emailAddress", "productName"],
              "metrics": ["earliestUsageDate", "latestUsageDate", "totalUniqueDays"]}
# Données personnelles masquées par l'administrateur Autodesk : seul l'identifiant haché est disponible.
SPEC_HASHED = {"fields": ["hashedUserId", "productName"],
               "metrics": ["earliestUsageDate", "latestUsageDate", "totalUniqueDays"]}
# Usage quotidien (une ligne par utilisateur, produit et jour) : alimente toutes les rubriques de l'interface
SPEC_DAILY = {"fields": ["emailAddress", "productName", "usageDate"], "metrics": ["totalUniqueDays"]}
SPEC_DAILY_HASHED = {"fields": ["hashedUserId", "productName", "usageDate"], "metrics": ["totalUniqueDays"]}
DAILY_OVERLAP_DAYS = 3        # jours relus à chaque synchronisation (usage remonté en retard chez Autodesk)
# La requête n'a pas de paramètre de dates : la période passe par la clause where (syntaxes essayées dans l'ordre).
WHERE_FORMS: list[Callable[[str, str], str]] = [
    lambda a, b: f"usageDate >= '{a}' AND usageDate <= '{b}'",
    lambda a, b: f"usageDate >= '{a}T00:00:00Z' AND usageDate <= '{b}T00:00:00Z'",
    lambda a, b: f"usageDate BETWEEN '{a}' AND '{b}'",
]


class ApiError(RuntimeError):
    """Erreur de l'API Autodesk (message prêt à afficher)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class NotConnected(ApiError):
    """Aucun jeton valide : lancer ``heptagoneslm autodesk-login``."""


# ----------------------------------------------------------------------------------- jetons


def default_token_file(config_path: str | os.PathLike | None) -> Path:
    base = Path(config_path).resolve().parent if config_path else Path.cwd()
    return base / "data" / "autodesk_token.dpapi"


class TokenStore:
    """Jeton OAuth sur disque : chiffré par DPAPI sous Windows, fichier réservé au propriétaire ailleurs."""

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)

    def load(self) -> dict | None:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return None
        try:
            text = vault.unprotect(raw) if os.name == "nt" else raw.decode("utf-8")
            return json.loads(text)
        except (vault.SecretError, ValueError, UnicodeDecodeError) as exc:
            raise NotConnected(f"Jeton Autodesk illisible ({exc}) : relancer « heptagoneslm autodesk-login »") from None

    def save(self, token: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(token)
        data = vault.protect(text) if os.name == "nt" else text.encode("utf-8")
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_bytes(data)
        if os.name != "nt":
            os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


# ----------------------------------------------------------------------------------- client


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


class InsightsClient:
    def __init__(self, cfg: AutodeskApiConfig, store: TokenStore, sleep: Callable[[float], None] = time.sleep):
        self.cfg = cfg
        self.store = store
        self.sleep = sleep
        self._last_headers = None

    # -- OAuth ---------------------------------------------------------------------------
    def authorize_url(self, state: str, challenge: str) -> str:
        q = urllib.parse.urlencode({"response_type": "code", "client_id": self.cfg.client_id,
                                    "redirect_uri": self.cfg.callback_url, "scope": SCOPES, "state": state,
                                    "code_challenge": challenge, "code_challenge_method": "S256"})
        return f"{self.cfg.base_url}/authentication/v2/authorize?{q}"

    def _token_request(self, form: dict) -> dict:
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
        secret = vault.resolve(self.cfg.client_secret) if self.cfg.client_secret else None
        if secret:
            basic = base64.b64encode(f"{self.cfg.client_id}:{secret}".encode()).decode("ascii")
            headers["Authorization"] = f"Basic {basic}"
        else:
            form = {**form, "client_id": self.cfg.client_id}
        status, body = self._http("POST", f"{self.cfg.base_url}/authentication/v2/token",
                                  urllib.parse.urlencode(form).encode(), headers)
        if status != 200:
            msg = body.get("error_description") or body.get("developerMessage") or body.get("error") or f"HTTP {status}"
            if body.get("error") == "invalid_grant":
                raise NotConnected(f"Connexion Autodesk expirée ou révoquée ({msg}) : relancer "
                                   "« heptagoneslm autodesk-login »", status)
            raise ApiError(f"Autodesk a refusé le jeton : {msg}", status)
        return body

    def _save(self, body: dict, previous: dict | None = None) -> dict:
        token = {"access_token": body["access_token"],
                 "refresh_token": body.get("refresh_token") or (previous or {}).get("refresh_token"),
                 "expires_at": time.time() + int(body.get("expires_in", 3600)) - 60,
                 "obtained_at": datetime.now().isoformat(timespec="seconds")}
        self.store.save(token)
        return token

    def exchange_code(self, code: str, verifier: str) -> dict:
        return self._save(self._token_request({"grant_type": "authorization_code", "code": code,
                                               "code_verifier": verifier, "redirect_uri": self.cfg.callback_url}))

    def access_token(self) -> str:
        token = self.store.load()
        if not token or not token.get("access_token"):
            raise NotConnected("Aucune connexion à Autodesk : lancer « heptagoneslm autodesk-login »")
        if token.get("expires_at", 0) > time.time():
            return token["access_token"]
        if not token.get("refresh_token"):
            raise NotConnected("Connexion Autodesk expirée : relancer « heptagoneslm autodesk-login »")
        # Le jeton de renouvellement est à usage unique : le nouveau est enregistré aussitôt.
        body = self._token_request({"grant_type": "refresh_token", "refresh_token": token["refresh_token"],
                                    "scope": SCOPES})
        return self._save(body, token)["access_token"]

    # -- HTTP ----------------------------------------------------------------------------
    def _http(self, method: str, url: str, data: bytes | None, headers: dict) -> tuple[int, dict]:
        headers = {"User-Agent": f"HeptagonesLM/{__version__}", **headers}
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout_s) as resp:
                raw, status = resp.read(), resp.status
                self._last_headers = resp.headers
        except urllib.error.HTTPError as exc:
            raw, status = exc.read(), exc.code
            self._last_headers = exc.headers
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise ApiError(f"Autodesk injoignable ({reason}) : vérifier l'accès à "
                           f"{urllib.parse.urlsplit(url).hostname} (proxy, pare-feu)") from None
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            body = {}
        return status, body if isinstance(body, (dict, list)) else {}

    def request(self, method: str, path: str, body: dict | None = None, params: dict | None = None):
        url = f"{self.cfg.base_url}/insights/v1{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(8):
            headers = {"Authorization": f"Bearer {self.access_token()}", "Accept": "application/json"}
            if data is not None:
                headers["Content-Type"] = "application/json"
            status, payload = self._http(method, url, data, headers)
            if 200 <= status < 300:
                return payload
            if status == 429 and attempt < 7:          # limite de débit par utilisateur : attendre ce que demande Autodesk
                retry = (self._last_headers or {}).get("Retry-After")
                wait = int(retry) if retry and str(retry).isdigit() else min(5 * 2 ** attempt, 60)
                log.info("Autodesk : limite de débit atteinte, nouvelle tentative dans %d s", wait)
                self.sleep(wait)
                continue
            if status >= 500 and attempt < 2:
                self.sleep(2 ** attempt)
                continue
            detail = ""
            if isinstance(payload, dict):
                detail = payload.get("detail") or payload.get("developerMessage") or payload.get("title") or ""
            if status == 401:
                raise NotConnected(f"Autodesk : accès refusé (401) {detail} — relancer « heptagoneslm autodesk-login »",
                                   status)
            if status == 403:
                raise ApiError(f"Autodesk : accès interdit (403) {detail} — le compte connecté doit être "
                               "administrateur d'une équipe Business Success Plan avec l'API de reporting", status)
            raise ApiError(f"Autodesk : HTTP {status} {detail}".rstrip(), status)
        raise ApiError("Autodesk : limite de débit toujours atteinte, synchronisation reportée", 429)

    # -- API Insights --------------------------------------------------------------------
    def contexts(self) -> list[dict]:
        data = self.request("GET", "/contexts")
        return data if isinstance(data, list) else data.get("results", []) if isinstance(data, dict) else []

    def submit_query(self, spec: dict, context_id: str, where: str) -> str:
        body = {**spec, "where": where, "orderBy": "", "contextId": context_id, "context": context_id}
        res = self.request("POST", "/usage-queries", body,
                           {"offset": 0, "limit": PAGE, "contextId": context_id, "context": context_id})
        if not res.get("id"):
            raise ApiError("Autodesk : requête d'usage sans identifiant")
        return res["id"]

    def query_rows(self, query_id: str) -> tuple[list[str], list[list]]:
        """Attend la fin de la requête puis lit toutes les pages : (colonnes, lignes)."""
        started, attempt = time.monotonic(), 0
        while True:
            page = self.request("GET", f"/usage-queries/{query_id}", params={"offset": 0, "limit": PAGE})
            status = str(page.get("status", "")).upper()
            if status == "COMPLETED":
                break
            if status in ("ERROR", "EXPIRED", "FAILED"):
                raise ApiError(f"Autodesk : requête d'usage terminée en {status}")
            if time.monotonic() - started > QUERY_TIMEOUT_S:
                raise ApiError(f"Autodesk : requête d'usage toujours {status or 'en cours'} après "
                               f"{QUERY_TIMEOUT_S // 60} min (réduire lookback_days)")
            attempt += 1
            self.sleep(min(attempt, 10))
        columns = [c.get("name") if isinstance(c, dict) else c for c in page.get("columns") or []]
        rows: list[list] = []
        total = (page.get("pagination") or {}).get("totalResults") or len(page.get("rows") or [])
        while True:
            chunk = page.get("rows") or []
            rows.extend(chunk)
            if not chunk or len(rows) >= total:
                return columns, rows
            page = self.request("GET", f"/usage-queries/{query_id}", params={"offset": len(rows), "limit": PAGE})


# ----------------------------------------------------------------------------------- connexion


def login(cfg: AutodeskApiConfig, store: TokenStore, open_browser: bool = True, timeout: int = 300,
          on_url: Callable[[str], None] | None = None) -> list[dict]:
    """Connexion OAuth 3 étapes d'un administrateur Autodesk ; renvoie les équipes accessibles."""
    client = InsightsClient(cfg, store)
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(24)
    result: dict = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):                                   # noqa: N802 (API http.server)
            url = urllib.parse.urlsplit(self.path)
            if url.path != "/autodesk/callback":
                self.send_error(404)
                return
            q = urllib.parse.parse_qs(url.query)
            if q.get("state", [""])[0] != state:
                result["error"] = "réponse de connexion inattendue (state)"
            elif "error" in q:
                result["error"] = q.get("error_description", q["error"])[0]
            else:
                result["code"] = q.get("code", [""])[0]
            ok = "code" in result
            html = ("<h2>HeptagonesLM</h2><p>Connexion à Autodesk réussie : vous pouvez fermer cette page.</p>" if ok
                    else f"<h2>HeptagonesLM</h2><p>Connexion refusée : {result.get('error', '')}</p>")
            data = f"<!doctype html><meta charset='utf-8'><title>HeptagonesLM</title>{html}".encode()
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            done.set()

        def log_message(self, *args):
            pass

    try:
        server = http.server.HTTPServer(("127.0.0.1", cfg.callback_port), Handler)
    except OSError as exc:
        raise ApiError(f"Port {cfg.callback_port} indisponible pour le retour de connexion ({exc}) : changer "
                       "autodesk.api.callback_port (et l'URL de rappel de l'application Autodesk)") from None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = client.authorize_url(state, challenge)
        if on_url:
            on_url(url)
        if open_browser:
            webbrowser.open(url)
        if not done.wait(timeout):
            raise ApiError(f"Connexion Autodesk non terminée après {timeout // 60} min")
    finally:
        server.shutdown()
        server.server_close()
    if "error" in result:
        raise ApiError(f"Connexion Autodesk refusée : {result['error']}")
    client.exchange_code(result["code"], verifier)
    return client.contexts()


# ----------------------------------------------------------------------------------- synchronisation


@dataclass
class SyncResult:
    context: str
    report_date: date
    users: int            # lignes (utilisateur, produit) renvoyées par Autodesk
    carried: int          # licences reprises du dernier état (sans usage sur la période)
    stored: int
    window: tuple[date, date]
    hashed: bool = False
    where_filtered: bool = True
    daily: int | None = None      # lignes d'usage quotidien enregistrées (None : non disponible)
    daily_error: str | None = None


def choose_context(contexts: list[dict], wanted: str | None) -> dict:
    usable = [c for c in contexts if REPORTING_BENEFIT in (c.get("reportingBenefits", [REPORTING_BENEFIT]) or [])]
    if wanted:
        for c in contexts:
            if str(c.get("contextId")) == wanted:
                return c
        names = ", ".join(f"{c.get('alias') or '?'} ({c.get('contextId')})" for c in contexts) or "aucune"
        raise ApiError(f"Équipe Autodesk {wanted} introuvable pour ce compte (équipes : {names})")
    if len(usable) == 1:
        return usable[0]
    if not usable:
        raise ApiError("Aucune équipe Autodesk avec l'API de reporting (avantage Business Success Plan) pour ce "
                       "compte administrateur")
    names = ", ".join(f"{c.get('alias') or '?'} ({c.get('contextId')})" for c in usable)
    raise ApiError(f"Plusieurs équipes Autodesk : indiquer autodesk.api.context_id ({names})")


def _col(columns: list[str], name: str) -> int | None:
    return columns.index(name) if name in columns else None


def fetch_usage(client: InsightsClient, context_id: str, start: date, end: date
                ) -> tuple[list[SeatRow], bool, int | None]:
    """Usage par utilisateur et par produit : (lignes, identifiants hachés, syntaxe de dates acceptée ou None)."""
    a, b = start.isoformat(), end.isoformat()
    last_error: ApiError | None = None
    for spec, hashed in ((SPEC_USAGE, False), (SPEC_HASHED, True)):
        for index, where in enumerate([f(a, b) for f in WHERE_FORMS] + [""]):
            try:
                query_id = client.submit_query(spec, context_id, where)
            except ApiError as exc:
                if exc.status != 400:
                    raise
                last_error = exc
                continue
            columns, raw = client.query_rows(query_id)
            if not where:
                log.warning("Autodesk : aucun filtre de dates accepté, tout l'historique d'usage a été interrogé")
            return [r for r in (_to_row(columns, x, hashed) for x in raw) if r], hashed, (index if where else None)
    raise last_error or ApiError("Autodesk : requête d'usage refusée")


def fetch_daily(client: InsightsClient, context_id: str, start: date, end: date,
                hashed: bool, where_index: int | None) -> list[tuple[str, str, date]]:
    """Usage jour par jour : [(produit, utilisateur, jour)] sur la période (bornes incluses).

    ``where_index`` : syntaxe de dates acceptée par la requête d'usage (sans filtre de dates, l'historique
    complet jour par jour serait trop volumineux : usage quotidien non demandé)."""
    if where_index is None:
        raise ApiError("filtre de dates refusé par Autodesk : usage quotidien non demandé")
    spec = SPEC_DAILY_HASHED if hashed else SPEC_DAILY
    query_id = client.submit_query(spec, context_id, WHERE_FORMS[where_index](start.isoformat(), end.isoformat()))
    columns, raw = client.query_rows(query_id)
    out = []
    iw, ip, idate = (_col(columns, "hashedUserId" if hashed else "emailAddress"), _col(columns, "productName"),
                     _col(columns, "usageDate"))
    if None in (iw, ip, idate):
        raise ApiError("Autodesk : colonnes de l'usage quotidien absentes de la réponse")
    for row in raw:
        who, product, day = str(row[iw] or "").strip(), str(row[ip] or "").strip(), parse_date(row[idate])
        if who and product and day and start <= day <= end:
            out.append((product, who if hashed else who.lower(), day))
    return out


def store_daily(db: Database, rows: list[tuple[str, str, date]], start: date, end: date,
                now: datetime | None = None) -> int:
    """Remplace l'usage quotidien de la période (à appeler dans une transaction)."""
    now = now or datetime.now().replace(microsecond=0)
    db.execute("DELETE FROM named_usage_day WHERE vendor = ? AND usage_date >= ? AND usage_date <= ?",
               [VENDOR, start, end])
    seen = set()
    for product, email, day in rows:
        key = (product.lower(), email, day)
        if key in seen:
            continue
        seen.add(key)
        db.execute("INSERT INTO named_usage_day (vendor, product, email, usage_date, imported_at) VALUES (?, ?, ?, ?, ?)",
                   [VENDOR, product[:255], email[:255], day, now])
    return len(seen)


def daily_start(db: Database, start: date) -> date:
    """Synchronisations suivantes : seuls les derniers jours sont relus (usage quotidien déjà en base)."""
    last = db.scalar("SELECT MAX(usage_date) FROM named_usage_day WHERE vendor = ?", [VENDOR])
    if not last:
        return start
    last = last if isinstance(last, date) else date.fromisoformat(str(last)[:10])
    return max(start, last - timedelta(days=DAILY_OVERLAP_DAYS))


def _to_row(columns: list[str], raw: list, hashed: bool) -> SeatRow | None:
    def get(name):
        i = _col(columns, name)
        return raw[i] if i is not None and i < len(raw) else None

    who = str(get("hashedUserId" if hashed else "emailAddress") or "").strip()
    product = str(get("productName") or "").strip()
    if not who or not product:
        return None
    days = get("totalUniqueDays")
    try:
        days = int(float(days)) if days not in (None, "") else None
    except (TypeError, ValueError):
        days = None
    return SeatRow(product=product, email=who if hashed else who.lower(),
                   name=None if hashed else (str(get("fullName") or "").strip() or None),
                   last_used=parse_date(get("latestUsageDate")), days_used=days)


def merge_with_previous(api_rows: list[SeatRow], previous: list[SeatRow]) -> tuple[list[SeatRow], int]:
    """Reprend les licences affectées connues sans usage sur la période (inactives ou jamais utilisées)."""
    prev = {(r.product.lower(), r.email): r for r in previous if not r.unassigned_date}
    out, seen = [], set()
    for r in api_rows:
        key = (r.product.lower(), r.email)
        seen.add(key)
        old = prev.get(key)
        if old:
            r.assigned_date = old.assigned_date
            r.name = r.name or old.name
            r.version = old.version
            if not r.last_used or (old.last_used and old.last_used > r.last_used):
                r.last_used = old.last_used or r.last_used
        out.append(r)
    carried = 0
    for key, old in prev.items():
        if key in seen:
            continue
        carried += 1
        out.append(SeatRow(product=old.product, email=old.email, name=old.name, assigned_date=old.assigned_date,
                           last_used=old.last_used, days_used=old.days_used if not old.last_used else None,
                           version=old.version, activity=old.activity))
    return out, carried


def last_sync(db: Database) -> datetime | None:
    value = db.scalar("SELECT MAX(imported_at) FROM named_import WHERE vendor = ?", [SYNC_VENDOR])
    if not value:
        return None
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def sync_due(db: Database, cfg: AutodeskApiConfig, now: datetime | None = None) -> bool:
    last = last_sync(db)
    return last is None or (now or datetime.now()) - last >= timedelta(hours=cfg.sync_hours)


def sync(db: Database, client: InsightsClient, today: date | None = None, dry_run: bool = False) -> SyncResult:
    """Lit l'usage chez Autodesk et enregistre l'état des licences nommées du jour."""
    cfg = client.cfg
    today = today or date.today()
    context = choose_context(client.contexts(), cfg.context_id)
    cid = str(context.get("contextId"))
    start = today - timedelta(days=cfg.lookback_days)
    rows, hashed, where_index = fetch_usage(client, cid, start, today)
    filtered = where_index is not None
    # Dernier état connu, export du portail du jour compris
    merged, carried = merge_with_previous(rows, previous_rows(db, today + timedelta(days=1)))
    label = f"API Autodesk ({context.get('alias') or cid})"
    stored = len(merged)
    # Usage jour par jour (rubriques Synthèse, Usage dans le temps, Répartition, Optimisation…) : non bloquant
    daily_rows, daily_error, dstart = None, None, daily_start(db, start)
    try:
        daily_rows = fetch_daily(client, cid, dstart, today, hashed, where_index)
    except ApiError as exc:
        daily_error = str(exc)
        log.warning("Autodesk : usage quotidien indisponible (%s) ; rapport « utilisateurs nommés » seul", exc)
    daily_stored = None
    if not dry_run:
        now = datetime.now().replace(microsecond=0)
        with db.transaction():
            stored = store_rows(db, merged, today, label, now)
            if daily_rows is not None:
                daily_stored = store_daily(db, daily_rows, dstart, today, now)
            db.execute("INSERT INTO named_import (vendor, file_name, file_hash, report_date, rows_count, imported_at) "
                       "VALUES (?, ?, ?, ?, ?, ?)",
                       [SYNC_VENDOR, label[:255], hashlib.sha256(f"api:{cid}:{now.isoformat()}:{secrets.token_hex(8)}".encode()).hexdigest(),
                        today, len(rows), now])
        log.info("Autodesk : %d lignes d'usage lues (%s), %d licences reprises du dernier état", len(rows), label,
                 carried)
    return SyncResult(context=context.get("alias") or cid, report_date=today, users=len(rows), carried=carried,
                      stored=stored, window=(start, today), hashed=hashed, where_filtered=filtered,
                      daily=daily_stored if not dry_run else (len(daily_rows) if daily_rows is not None else None),
                      daily_error=daily_error)


__all__ = ["ApiError", "InsightsClient", "NotConnected", "SyncResult", "TokenStore", "VENDOR", "choose_context",
           "default_token_file", "fetch_daily", "fetch_usage", "store_daily", "last_sync", "login", "merge_with_previous", "sync", "sync_due"]
