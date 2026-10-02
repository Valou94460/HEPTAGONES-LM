"""Faux ServiceNow (API Table « incident ») en HTTPS, avec authentification Basic.

Sert aux tests de l'étape « Tickets ITSM » de l'assistant et aux captures de preuve : il
reproduit les appels documentés (POST / PATCH /api/now/table/incident), refuse un mauvais
mot de passe (401) et affiche les incidents reçus dans une page HTML (/ et /nav_to.do).

    python -m tests.fake_servicenow [port]      (identifiants : svc_heptagoneslm / Snow-Test-2026)
"""

from __future__ import annotations

import base64
import html
import json
import ssl
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CA_FILE = str(FIXTURES / "itsm_test_ca.pem")
USER, PASSWORD = "svc_heptagoneslm", "Snow-Test-2026"
STATES = {"1": "Nouveau", "2": "En cours", "6": "Résolu", "7": "Fermé"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, code: int, body, ctype: str = "application/json") -> None:
        raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        if code == 401:
            self.send_header("WWW-Authenticate", 'Basic realm="Service-now"')
        self.end_headers()
        self.wfile.write(raw)

    def _auth_ok(self) -> bool:
        expected = "Basic " + base64.b64encode(f"{self.server.user}:{self.server.password}".encode()).decode()
        return self.headers.get("Authorization") == expected

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            return self._reply(200, self.server.page(), "text/html")
        if url.path == "/incidents.json":            # contrôle par les scripts de test
            return self._reply(200, list(self.server.incidents.values()))
        if url.path == "/nav_to.do":
            uri = (parse_qs(url.query).get("uri") or [""])[0]
            sys_id = uri.split("sys_id=", 1)[-1]
            return self._reply(200, self.server.page(sys_id), "text/html")
        return self._reply(404, {"error": {"message": "Not found"}})

    def do_POST(self):
        self._api("POST")

    def do_PATCH(self):
        self._api("PATCH")

    def _api(self, method: str):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        srv = self.server
        srv.calls.append((method, self.path, body))
        if not self._auth_ok():
            return self._reply(401, {"error": {"message": "User Not Authenticated",
                                               "detail": "Required to provide Auth information"}, "status": "failure"})
        now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        with srv.lock:
            if method == "POST" and self.path == "/api/now/table/incident":
                srv.seq += 1
                sys_id = f"{srv.seq:032x}"
                inc = {"sys_id": sys_id, "number": f"INC{10000 + srv.seq:07d}", "state": "1", "opened_at": now,
                       "work_notes": [], **body}
                srv.incidents[sys_id] = inc
                return self._reply(201, {"result": inc})
            if method == "PATCH" and self.path.startswith("/api/now/table/incident/"):
                inc = srv.incidents.get(self.path.rsplit("/", 1)[-1])
                if inc is None:
                    return self._reply(404, {"error": {"message": "No Record found"}})
                note = body.pop("work_notes", None)
                if note:
                    inc["work_notes"].append(f"{now} — {note}")
                if body.get("state") == "6":
                    body["resolved_at"] = now
                inc.update(body)
                return self._reply(200, {"result": inc})
        return self._reply(400, {"error": {"message": "Invalid request"}})


class FakeServiceNow(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64

    def __init__(self, port: int = 0, user: str = USER, password: str = PASSWORD):
        super().__init__(("127.0.0.1", port), Handler)
        self.tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.tls.load_cert_chain(str(FIXTURES / "itsm_test_cert.pem"), str(FIXTURES / "itsm_test_key.pem"))
        self.user, self.password = user, password
        self.calls: list[tuple] = []
        self.incidents: dict[str, dict] = {}
        self.seq = 0
        self.lock = threading.Lock()

    def process_request_thread(self, request, client_address):
        # Négociation TLS dans le fil de la requête : un client lent ou interrompu ne bloque
        # jamais la boucle d'acceptation (le serveur reste joignable pendant toute la session)
        try:
            request = self.tls.wrap_socket(request, server_side=True)
        except (OSError, ssl.SSLError) as exc:
            print(f"TLS refusé pour {client_address} : {exc}", file=sys.stderr, flush=True)
            self.shutdown_request(request)
            return
        super().process_request_thread(request, client_address)

    def handle_error(self, request, client_address):
        import traceback
        print(f"Erreur sur une requête de {client_address} :", file=sys.stderr, flush=True)
        traceback.print_exc()

    @property
    def url(self) -> str:
        return f"https://127.0.0.1:{self.server_address[1]}"

    def start(self) -> "FakeServiceNow":
        threading.Thread(target=self.serve_forever, daemon=True).start()
        return self

    def page(self, sys_id: str | None = None) -> str:
        e = lambda v: html.escape(str(v or ""))
        rows = [i for i in self.incidents.values() if sys_id in (None, i["sys_id"])]
        cards = "".join(f"""
          <section class="inc"><header><b>{e(i['number'])}</b><span class="st st{e(i['state'])}">{e(STATES.get(i['state'], i['state']))}</span></header>
          <h2>{e(i.get('short_description'))}</h2>
          <dl><dt>Ouvert le</dt><dd>{e(i['opened_at'])}</dd><dt>Appelant</dt><dd>{e(i.get('caller_id'))}</dd>
          <dt>Groupe d'affectation</dt><dd>{e(i.get('assignment_group') or '—')}</dd><dt>Urgence / impact</dt><dd>{e(i.get('urgency'))} / {e(i.get('impact'))}</dd>
          <dt>Corrélation</dt><dd>{e(i.get('correlation_display'))} · {e(i.get('correlation_id'))}</dd>
          <dt>Description</dt><dd><pre>{e(i.get('description'))}</pre></dd>
          <dt>Notes de travail</dt><dd>{'<br>'.join(e(n) for n in i['work_notes']) or '—'}</dd>
          <dt>Résolution</dt><dd>{e(i.get('close_code') or '—')} {('· ' + e(i.get('resolved_at'))) if i.get('resolved_at') else ''}<br>{e(i.get('close_notes'))}</dd></dl></section>"""
                        for i in rows) or "<p>Aucun incident reçu.</p>"
        return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Faux ServiceNow de test — incidents</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;margin:0;background:#f4f6f8;color:#1d2733}}
.top{{background:#293e40;color:#fff;padding:14px 24px;font-size:18px}}.top small{{opacity:.75;margin-left:12px}}
main{{padding:20px 24px;display:grid;gap:16px}}.inc{{background:#fff;border:1px solid #d5dbe0;border-radius:8px;padding:16px 20px}}
header{{display:flex;gap:12px;align-items:center}}h2{{font-size:17px;margin:8px 0 12px}}
.st{{font-size:12px;padding:2px 10px;border-radius:10px;background:#e3e8ec}}.st6{{background:#d7f0dd;color:#1d6b33}}.st1{{background:#fde7c8;color:#8a4b00}}
dl{{display:grid;grid-template-columns:180px 1fr;gap:6px 14px;margin:0;font-size:14px}}dt{{color:#5b6875}}pre{{margin:0;white-space:pre-wrap;font-family:inherit}}</style></head>
<body><div class="top">Faux ServiceNow de test (API Table) <small>{e(self.url)} · incidents reçus : {len(self.incidents)}</small></div><main>{cards}</main></body></html>"""


if __name__ == "__main__":
    srv = FakeServiceNow(int(sys.argv[1]) if len(sys.argv) > 1 else 8443)
    print(f"Faux ServiceNow : {srv.url} (compte {USER}, AC de test {CA_FILE})", flush=True)
    srv.serve_forever()
