"""Faux serveur Autodesk (OAuth v2 + API Insights « Business Success Plan Reporting ») pour les tests.

Reproduit le contrat de l'exemple officiel autodesk-platform-services/aps-business-success-plan-report :
connexion OAuth 3 étapes avec PKCE, jeton de renouvellement à usage unique, ``GET /insights/v1/contexts``,
``POST /insights/v1/usage-queries`` asynchrone puis ``GET /usage-queries/:id`` paginé (colonnes / lignes).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CLIENT_ID = "client-heptagones"
CLIENT_SECRET = "secret-heptagones"

USAGE = [  # fullName, emailAddress, productName, earliest, latest, jours
    ("Jean Dupont", "Jean.Dupont@exemple.fr", "AutoCAD", "2026-07-10", "2026-09-30", 41),
    ("Jean Dupont", "jean.dupont@exemple.fr", "Revit", "2026-08-01", "2026-09-28", 20),
    ("Marie Curie", "marie.curie@exemple.fr", "AutoCAD", "2026-07-05", "2026-08-02", 6),
    ("Luc Besson", "luc.besson@exemple.fr", "Inventor Professional", "2026-09-01", "2026-09-29", 15),
]


class FakeAutodesk:
    def __init__(self, contexts=None, accepted_where: int | None = 0, hashed: bool = False, throttle_once=False,
                 page_size: int = 2, polls_before_done: int = 1, daily: bool = True):
        self.contexts = contexts if contexts is not None else [
            {"contextId": "TEAM-1", "alias": "Bureau d'études", "reportingBenefits": ["ReportingAPIAccess"]}]
        self.accepted_where = accepted_where     # index de la syntaxe de dates acceptée (None : aucune)
        self.hashed = hashed                     # données personnelles masquées : emailAddress refusé
        self.throttle_once = throttle_once
        self.page_size = page_size
        self.polls_before_done = polls_before_done
        self.daily = daily                       # usage quotidien (champ usageDate) accepté
        self.codes: dict[str, dict] = {}
        self.access: set[str] = set()
        self.refresh: set[str] = set()
        self.queries: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []
        self.token_requests: list[dict] = []
        self.lock = threading.Lock()
        self.last_where = None
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, body=None, headers=None):
                data = json.dumps(body if body is not None else {}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def authed(self) -> bool:
                token = self.headers.get("Authorization", "").removeprefix("Bearer ")
                if token not in fake.access:
                    self.reply(401, {"detail": "The token is not valid"})
                    return False
                return True

            def do_GET(self):  # noqa: N802
                url = urllib.parse.urlsplit(self.path)
                q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
                fake.calls.append(("GET", url.path))
                if url.path == "/authentication/v2/authorize":
                    # L'administrateur se connecte et consent : retour vers l'URL de rappel avec un code
                    code = secrets.token_hex(8)
                    fake.codes[code] = q
                    loc = q["redirect_uri"] + "?" + urllib.parse.urlencode({"code": code, "state": q["state"]})
                    self.send_response(302)
                    self.send_header("Location", loc)
                    self.end_headers()
                    return
                if not self.authed():
                    return
                if url.path == "/insights/v1/contexts":
                    return self.reply(200, fake.contexts)
                if url.path.startswith("/insights/v1/usage-queries/"):
                    qid = url.path.rsplit("/", 1)[1]
                    query = fake.queries.get(qid)
                    if not query:
                        return self.reply(404, {"detail": "unknown query"})
                    if query["polls"] < fake.polls_before_done:
                        query["polls"] += 1
                        return self.reply(200, {"id": qid, "status": "RUNNING"})
                    offset, limit = int(q.get("offset", 0)), min(int(q.get("limit", 100)), fake.page_size)
                    rows = query["rows"]
                    return self.reply(200, {"id": qid, "status": "COMPLETED", "columns": query["columns"],
                                            "rows": rows[offset:offset + limit],
                                            "pagination": {"offset": offset, "limit": limit,
                                                           "totalResults": len(rows)}})
                self.reply(404, {"detail": "not found"})

            def do_POST(self):  # noqa: N802
                url = urllib.parse.urlsplit(self.path)
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                fake.calls.append(("POST", url.path))
                if url.path == "/authentication/v2/token":
                    return self.token(dict(urllib.parse.parse_qsl(raw.decode())))
                if not self.authed():
                    return
                if url.path == "/insights/v1/usage-queries":
                    if fake.throttle_once:
                        fake.throttle_once = False
                        return self.reply(429, {"detail": "Too many requests"}, {"Retry-After": "1"})
                    body = json.loads(raw or b"{}")
                    return self.usage_query(body)
                self.reply(404, {"detail": "not found"})

            def token(self, form):
                fake.token_requests.append(form)
                client = form.get("client_id")
                auth = self.headers.get("Authorization", "")
                if auth.startswith("Basic "):
                    cid, _, secret = base64.b64decode(auth[6:]).decode().partition(":")
                    if (cid, secret) != (CLIENT_ID, CLIENT_SECRET):
                        return self.reply(401, {"error": "invalid_client"})
                    client = cid
                if client != CLIENT_ID:
                    return self.reply(401, {"error": "invalid_client", "error_description": "client inconnu"})
                if form.get("grant_type") == "authorization_code":
                    req = fake.codes.pop(form.get("code"), None)
                    if not req:
                        return self.reply(400, {"error": "invalid_grant", "error_description": "code inconnu"})
                    challenge = base64.urlsafe_b64encode(hashlib.sha256(form.get("code_verifier", "").encode())
                                                         .digest()).rstrip(b"=").decode()
                    if challenge != req.get("code_challenge") or form.get("redirect_uri") != req.get("redirect_uri"):
                        return self.reply(400, {"error": "invalid_grant", "error_description": "PKCE invalide"})
                elif form.get("grant_type") == "refresh_token":
                    with fake.lock:
                        if form.get("refresh_token") not in fake.refresh:
                            return self.reply(400, {"error": "invalid_grant",
                                                    "error_description": "The refresh token is invalid or expired."})
                        fake.refresh.discard(form["refresh_token"])   # usage unique
                else:
                    return self.reply(400, {"error": "unsupported_grant_type"})
                access, refresh = secrets.token_hex(12), secrets.token_hex(12)
                fake.access.add(access)
                fake.refresh.add(refresh)
                self.reply(200, {"access_token": access, "refresh_token": refresh, "token_type": "Bearer",
                                 "expires_in": 3599})

            def usage_query(self, body):
                from heptagoneslm.autodesk_api import WHERE_FORMS

                where = body.get("where", "")
                if where:
                    shape = re.sub(r"\d{4}-\d{2}-\d{2}", "D", where)
                    shapes = [re.sub(r"\d{4}-\d{2}-\d{2}", "D", f("2000-01-01", "2000-01-02")) for f in WHERE_FORMS]
                    if fake.accepted_where is None or shape != shapes[fake.accepted_where]:
                        return self.reply(400, {"detail": "Invalid where clause"})
                    fake.last_where = where
                fields = body.get("fields", [])
                if fake.hashed and "emailAddress" in fields:
                    return self.reply(400, {"detail": "emailAddress is not available: personal data is hashed"})
                ctx = body.get("contextId")
                if ctx not in {c["contextId"] for c in fake.contexts}:
                    return self.reply(403, {"detail": "No further detail provided"})
                columns = fields + body.get("metrics", [])
                rows = []
                if "usageDate" in fields:
                    if not fake.daily:
                        return self.reply(400, {"detail": "usageDate is not a valid field"})
                    for name, email, product, first, last, days in USAGE:     # premier et dernier jour d'usage
                        for day in sorted({first, last}):
                            values = {"emailAddress": email, "productName": product, "usageDate": day,
                                      "hashedUserId": hashlib.sha1(email.lower().encode()).hexdigest()[:16],
                                      "totalUniqueDays": 1}
                            rows.append([values[c] for c in columns])
                    qid = secrets.token_hex(6)
                    fake.queries[qid] = {"columns": columns, "rows": rows, "polls": 0, "where": where}
                    return self.reply(201, {"id": qid, "status": "SUBMITTED"})
                for name, email, product, first, last, days in USAGE:
                    values = {"fullName": name, "emailAddress": email, "productName": product,
                              "hashedUserId": hashlib.sha1(email.lower().encode()).hexdigest()[:16],
                              "earliestUsageDate": first, "latestUsageDate": last, "totalUniqueDays": days}
                    rows.append([values[c] for c in columns])
                qid = secrets.token_hex(6)
                fake.queries[qid] = {"columns": columns, "rows": rows, "polls": 0, "where": where}
                self.reply(201, {"id": qid, "status": "SUBMITTED"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

