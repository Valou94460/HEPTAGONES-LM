"""Tickets ITSM : faux serveur reproduisant les API ServiceNow, Jira, GLPI et webhook."""

import base64
import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml

from heptagoneslm.alerts import check_and_notify
from heptagoneslm.config import ConfigError, parse_config
from heptagoneslm.itsm import test_connector
from heptagoneslm.models import EventType, LicenseEvent
from heptagoneslm.service import CollectionService
from heptagoneslm.storage.repository import Repository
from tests.helpers import make_db
from tests.test_collection import co, status_at

NOW = datetime(2026, 9, 29, 10, 0)


class FakeItsm(BaseHTTPRequestHandler):
    """Réponses minimales, conformes aux API documentées des éditeurs."""

    def log_message(self, *args):
        pass

    def _reply(self, code, body=None):
        raw = b"" if body is None else (body.encode() if isinstance(body, str) else json.dumps(body).encode())
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _handle(self):
        srv = self.server
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        srv.calls.append((self.command, self.path, dict(self.headers), body))
        if srv.down:
            return self._reply(503, {"error": "maintenance"})
        p, m = self.path, self.command
        srv.n += 1
        # --- ServiceNow
        if p == "/api/now/table/incident" and m == "POST":
            return self._reply(201, {"result": {"sys_id": f"sys{srv.n}", "number": f"INC00{srv.n}"}})
        if p.startswith("/api/now/table/incident/") and m == "PATCH":
            return self._reply(200, {"result": {}})
        # --- Jira
        if p == "/rest/api/2/issue" and m == "POST":
            return self._reply(201, {"id": str(1000 + srv.n), "key": f"LIC-{srv.n}"})
        if p.endswith("/comment") and m == "POST":
            return self._reply(201, {"id": "1"})
        if p.endswith("/transitions") and m == "GET":
            return self._reply(200, {"transitions": [{"id": "21", "name": "In Progress"}, {"id": "31", "name": "Done"}]})
        if p.endswith("/transitions") and m == "POST":
            return self._reply(204)
        # --- GLPI
        if p == "/glpi/apirest.php/initSession":
            if self.headers.get("App-Token") != "app" or self.headers.get("Authorization") != "user_token usr":
                return self._reply(401, ["ERROR_WRONG_APP_TOKEN_PARAMETER", ""])
            return self._reply(200, {"session_token": "sess"})
        if p.startswith("/glpi/apirest.php/"):
            if self.headers.get("Session-Token") != "sess":
                return self._reply(401, ["ERROR_SESSION_TOKEN_INVALID", ""])
            if p.endswith("/killSession"):
                return self._reply(200)
            if p == "/glpi/apirest.php/Ticket":
                return self._reply(201, {"id": 7, "message": ""})
            return self._reply(201, {"id": srv.n})
        # --- Webhook (Teams répond « 1 » en texte)
        if p == "/hook":
            return self._reply(200, "1")
        return self._reply(404, {"error": "not found"})

    do_GET = do_POST = do_PATCH = _handle


class ItsmTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeItsm)
        cls.server.calls, cls.server.down, cls.server.n = [], False, 0
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.server.calls.clear()
        self.server.down = False
        self.server.n = 0

    def config(self, connectors, recipients=None, **rules):
        alerts = {"itsm": [{"allow_http": True, "url": self.base, **c} for c in connectors], **rules}
        raw = {"database": {"connection_string": "sqlite:///:memory:"},
               "servers": [{"name": "AUTODESK", "port_at_host": "27000@licsrv1"}],
               "products": [{"pattern": "87545ACD_2024_*", "product": "AutoCAD 2024"}],
               "web": {"public_url": "https://heptagoneslm.entreprise.local"},
               "alerts": alerts}
        if recipients:
            alerts["recipients"] = recipients
            raw["email"] = {"smtp_host": "smtp.entreprise.local", "sender": "h@entreprise.local"}
        return parse_config(raw)

    def prepare(self, cfg):
        self.cfg, self.db = cfg, make_db()
        self.svc = CollectionService(cfg, self.db)
        self.ids = self.svc.sync_servers()
        self.repo = Repository(self.db)
        self.mails = []

    def run_(self, when, status):
        with self.db.transaction():
            rid = self.repo.start_run(self.ids["AUTODESK"], "lmstat", when)
            self.repo.finish_run(rid, status, 0, "Serveur 27000@licsrv1 injoignable" if status == "ERROR" else None)

    def check(self, when, status="OK"):
        self.run_(when, status)
        return check_and_notify(self.cfg, self.db, now=when, sender=lambda m, c: self.mails.append(m))

    def calls(self, method=None, prefix=""):
        return [c for c in self.server.calls if (method is None or c[0] == method) and c[1].startswith(prefix)]

    def tickets(self):
        return self.db.query_dicts("SELECT connector, alert_key, ticket_id, ticket_ref, url, status FROM itsm_ticket ORDER BY id")


class ServiceNowTest(ItsmTestCase):
    def test_incident_lifecycle(self):
        self.prepare(self.config([{"type": "servicenow", "username": "svc", "password": "S3cret",
                                   "assignment_group": "Licences", "caller": "svc_heptagoneslm"}],
                                 recipients=["dsi@entreprise.local"]))
        r = self.check(NOW, "ERROR")
        self.assertEqual(r.status, "SENT")
        self.assertEqual(r.tickets, ["ServiceNow INC001 ouvert — Collecte en échec : AUTODESK"])
        method, path, headers, body = self.calls("POST")[0]
        self.assertEqual(path, "/api/now/table/incident")
        self.assertEqual(headers["Authorization"], "Basic " + base64.b64encode(b"svc:S3cret").decode())
        self.assertEqual(body["short_description"], "[HeptagonesLM] Collecte en échec : AUTODESK")
        self.assertEqual((body["urgency"], body["assignment_group"], body["caller_id"]), ("1", "Licences", "svc_heptagoneslm"))
        self.assertEqual(body["correlation_id"], "collection:AUTODESK:lmstat")
        self.assertIn("https://heptagoneslm.entreprise.local/", body["description"])
        self.assertEqual(self.tickets()[0]["url"], f"{self.base}/nav_to.do?uri=incident.do?sys_id=sys1")
        self.assertEqual(len(self.mails), 1)                         # l'e-mail part aussi

        # toujours en panne : rien de nouveau (ni doublon, ni commentaire)
        self.check(NOW + timedelta(minutes=5), "ERROR")
        self.assertEqual(len(self.server.calls), 1)
        # rappel après 24 h : note de travail sur le même incident
        self.check(NOW + timedelta(hours=25), "ERROR")
        method, path, _, body = self.server.calls[-1]
        self.assertEqual((method, path), ("PATCH", "/api/now/table/incident/sys1"))
        self.assertIn("Rappel", body["work_notes"])
        # rétablissement : incident résolu
        r = self.check(NOW + timedelta(hours=26))
        method, path, _, body = self.server.calls[-1]
        self.assertEqual((method, path, body["state"]), ("PATCH", "/api/now/table/incident/sys1", "6"))
        self.assertIn("Collecte rétablie", body["close_notes"])
        self.assertEqual(r.tickets, ["ServiceNow INC001 résolu — Collecte en échec : AUTODESK"])
        self.assertEqual(self.tickets()[0]["status"], "RESOLVED")
        # nouvelle panne plus tard : nouvel incident
        self.check(NOW + timedelta(hours=30), "ERROR")
        self.assertEqual([t["status"] for t in self.tickets()], ["RESOLVED", "OPEN"])


class JiraTest(ItsmTestCase):
    def add_denials(self, when, users):
        events = [LicenseEvent(when + timedelta(seconds=i), EventType.DENIED, "87545ACD_2024_0F", u, f"PC-{u}", "adskflex")
                  for i, u in enumerate(users)]
        with self.db.transaction():
            self.svc.apply_events(self.ids["AUTODESK"], events, "log", track_sessions=False)

    def test_denials_issue_comment_and_transition(self):
        self.prepare(self.config([{"type": "jira", "token": "PAT-123", "project": "LIC"}]))   # Data Center : PAT
        self.add_denials(NOW - timedelta(minutes=2), ["jdoe", "asmith"])
        r = self.check(NOW)
        self.assertEqual(r.tickets, ["Jira LIC-1 ouvert — 2 refus de licence : AutoCAD 2024"])
        _, _, headers, body = self.calls("POST", "/rest/api/2/issue")[0]
        self.assertEqual(headers["Authorization"], "Bearer PAT-123")
        self.assertEqual(body["fields"]["project"], {"key": "LIC"})
        self.assertEqual(body["fields"]["issuetype"], {"name": "Incident"})
        self.assertIn("heptagoneslm", body["fields"]["labels"])
        self.assertEqual(self.mails, [])                              # aucun destinataire e-mail : ITSM seul
        # nouveaux refus le lendemain : commentaire sur LIC-1
        self.add_denials(NOW + timedelta(hours=25), ["bmartin"])
        self.check(NOW + timedelta(hours=25, minutes=5))
        self.assertEqual(self.calls("POST", "/rest/api/2/issue/LIC-1/comment")[-1][3]["body"][:6], "Rappel")
        # plus aucun refus pendant 24 h : commentaire, puis transition « Done » (id 31)
        r = self.check(NOW + timedelta(hours=50))
        self.assertEqual(self.calls("POST", "/rest/api/2/issue/LIC-1/transitions")[-1][3], {"transition": {"id": "31"}})
        self.assertEqual(r.tickets, ["Jira LIC-1 résolu — 2 refus de licence : AutoCAD 2024"])

    def test_cloud_basic_auth_and_test_command(self):
        cfg = self.config([{"type": "jira", "username": "svc@entreprise.fr", "token": "api-token", "project": "LIC"}])
        steps = test_connector(cfg.alerts.itsm[0], cfg)
        self.assertEqual(steps[0], f"ouverture : LIC-1 ({self.base}/browse/LIC-1)")
        self.assertEqual(steps[1:], ["commentaire : OK", "résolution : OK"])
        auth = self.calls("POST", "/rest/api/2/issue")[0][2]["Authorization"]
        self.assertEqual(auth, "Basic " + base64.b64encode(b"svc@entreprise.fr:api-token").decode())

    def test_missing_transition_is_reported(self):
        cfg = self.config([{"type": "jira", "token": "t", "project": "LIC", "resolve_transitions": ["Clôturer"]}])
        with self.assertRaisesRegex(Exception, "In Progress, Done"):
            test_connector(cfg.alerts.itsm[0], cfg)


class GlpiTest(ItsmTestCase):
    def test_expiry_ticket_then_renewal(self):
        self.prepare(self.config([{"type": "glpi", "url": f"{self.base}/glpi/apirest.php", "app_token": "app",
                                   "user_token": "usr", "entity_id": 3, "category_id": 12}]))
        fid = self.svc._feature_id(self.ids["AUTODESK"], "87545ACD_2024_0F", NOW)
        with self.db.transaction():
            self.repo.set_feature_expiry(fid, date(2026, 10, 2))       # J-3 : critique
        r = self.check(NOW)
        self.assertEqual(r.tickets, ["GLPI 7 ouvert — Expiration dans 3 j : AutoCAD 2024"])
        body = self.calls("POST", "/glpi/apirest.php/Ticket")[0][3]["input"]
        self.assertEqual((body["urgency"], body["type"], body["entities_id"], body["itilcategories_id"]), (5, 1, 3, 12))
        self.assertEqual(self.tickets()[0]["url"], f"{self.base}/glpi/front/ticket.form.php?id=7")
        self.assertTrue(self.calls("GET", "/glpi/apirest.php/killSession"))        # session fermée
        # licence renouvelée : solution ajoutée au ticket
        with self.db.transaction():
            self.db.execute("UPDATE feature SET expires_on = ? WHERE id = ?", [date(2027, 12, 31), fid])
        r = self.check(NOW + timedelta(hours=1))
        solution = self.calls("POST", "/glpi/apirest.php/Ticket/7/ITILSolution")[-1][3]["input"]
        self.assertEqual((solution["itemtype"], solution["items_id"]), ("Ticket", 7))
        self.assertEqual(r.tickets, ["GLPI 7 résolu — Expiration dans 3 j : AutoCAD 2024"])

    def test_wrong_token(self):
        cfg = self.config([{"type": "glpi", "url": f"{self.base}/glpi/apirest.php", "app_token": "mauvais", "user_token": "usr"}])
        with self.assertRaisesRegex(Exception, "HTTP 401"):
            test_connector(cfg.alerts.itsm[0], cfg)


class WebhookAndResilienceTest(ItsmTestCase):
    def saturate(self, when):
        full = status_at(when, [co("u1", "1", when - timedelta(hours=1)), co("u2", "2", when - timedelta(hours=1))], issued=2)
        with self.db.transaction():
            self.svc.apply_status(self.ids["AUTODESK"], full, "lmstat", track_sessions=True)

    def test_teams_webhook_with_min_severity(self):
        self.prepare(self.config([{"type": "webhook", "url": f"{self.base}/hook", "format": "teams",
                                   "min_severity": "warning", "headers": {"X-Cle": "abc"}},
                                  {"type": "webhook", "name": "sans-saturation", "url": f"{self.base}/hook"}]))
        self.saturate(NOW - timedelta(minutes=2))
        r = self.check(NOW)
        posts = self.calls("POST", "/hook")
        self.assertEqual(len(posts), 1)            # le 2e webhook (gravité « serious » par défaut) ignore la saturation
        self.assertIn("**Plus aucun jeton disponible", posts[0][3]["text"])
        self.assertEqual(posts[0][2]["X-Cle"], "abc")
        self.assertEqual(r.tickets, ["Webhook ouvert — Plus aucun jeton disponible : AutoCAD 2024 (87545ACD_2024_0F)"])

    def test_json_webhook_events(self):
        self.prepare(self.config([{"type": "webhook", "url": f"{self.base}/hook"}]))
        self.check(NOW, "ERROR")
        self.check(NOW + timedelta(minutes=10))
        events = [c[3]["event"] for c in self.calls("POST", "/hook")]
        self.assertEqual(events, ["opened", "resolved"])
        self.assertEqual(self.calls("POST", "/hook")[0][3]["alert"]["severity"], "critical")

    def test_connector_down_then_catch_up(self):
        self.prepare(self.config([{"type": "servicenow", "username": "svc", "password": "p"}],
                                 recipients=["dsi@entreprise.local"]))
        self.server.down = True
        r = self.check(NOW, "ERROR")
        self.assertEqual(r.status, "ERROR")
        self.assertIn("HTTP 503", r.errors[0])
        self.assertEqual(len(self.mails), 1)          # l'e-mail est parti malgré la panne ITSM
        self.assertEqual(self.tickets(), [])
        self.server.down = False
        r = self.check(NOW + timedelta(minutes=5), "ERROR")    # pas de nouvel e-mail, mais l'incident est rattrapé
        self.assertEqual(len(self.mails), 1)
        self.assertEqual(r.tickets, ["ServiceNow INC001 ouvert — Collecte en échec : AUTODESK"])
        self.assertEqual(len(self.tickets()), 1)

    def test_dry_run_touches_nothing(self):
        self.prepare(self.config([{"type": "webhook", "url": f"{self.base}/hook"}], recipients=["a@entreprise.local"]))
        self.run_(NOW, "ERROR")
        with tempfile.TemporaryDirectory() as tmp:
            r = check_and_notify(self.cfg, self.db, now=NOW, dry_run_dir=tmp)
        self.assertEqual(r.status, "DRY-RUN")
        self.assertEqual(self.server.calls, [])


class ItsmConfigTest(unittest.TestCase):
    def cfg(self, alerts, email=None):
        raw = {"database": {"connection_string": "sqlite:///:memory:"}, "servers": [], "alerts": alerts}
        if email is not None:
            raw["email"] = email
        return parse_config(raw)

    def test_itsm_only_needs_no_smtp(self):
        c = self.cfg({"itsm": [{"type": "jira", "url": "https://jira.entreprise.local", "token": "t", "project": "LIC"}]})
        self.assertEqual((c.alerts.recipients, c.alerts.itsm[0].name, c.alerts.itsm[0].min_severity), ([], "jira", "serious"))

    def test_validation(self):
        bad = [
            {"itsm": [{"type": "remedy", "url": "https://x"}]},
            {"itsm": [{"type": "jira", "url": "http://jira", "token": "t", "project": "P"}]},          # http refusé
            {"itsm": [{"type": "servicenow", "url": "https://sn"}]},                                    # identifiants
            {"itsm": [{"type": "jira", "url": "https://jira", "project": "P"}]},                        # jeton
            {"itsm": [{"type": "glpi", "url": "https://glpi/apirest.php", "app_token": "a"}]},
            {"itsm": [{"type": "webhook", "url": "https://h", "format": "xml"}]},
            {"itsm": [{"type": "webhook", "url": "https://h", "min_severity": "grave"}]},
            {"itsm": [{"type": "webhook", "url": "https://h"}, {"type": "webhook", "url": "https://i"}]},  # noms en double
            {"recipients": ["a@b.fr"]},                                                                  # pas de SMTP
        ]
        for alerts in bad:
            with self.assertRaises(ConfigError, msg=alerts):
                self.cfg(alerts)
        with self.assertRaises(ConfigError):                    # section déclarée deux fois
            self.cfg({"recipients": ["a@b.fr"]}, {"smtp_host": "smtp", "alerts": {"recipients": ["a@b.fr"]}})

    def test_legacy_email_alerts_still_accepted(self):
        c = parse_config({"database": {"connection_string": "sqlite:///:memory:"}, "servers": [],
                          "email": {"smtp_host": "smtp", "alerts": {"recipients": ["a@b.fr"]}}})
        self.assertIs(c.alerts, c.email.alerts)
        self.assertEqual(c.alerts.recipients, ["a@b.fr"])

    def test_secrets_from_environment(self):
        os.environ["HLM_TEST_SN_PWD"] = "depuis-env"
        try:
            c = self.cfg({"itsm": [{"type": "servicenow", "url": "https://sn", "username": "svc",
                                    "password": "${HLM_TEST_SN_PWD}"}]})
        finally:
            del os.environ["HLM_TEST_SN_PWD"]
        self.assertEqual(c.alerts.itsm[0].password, "depuis-env")


class CliTest(ItsmTestCase):
    def test_test_itsm_command(self):
        from heptagoneslm.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "h.yaml")
            with open(path, "w", encoding="utf-8") as fh:
                yaml.safe_dump({"database": {"connection_string": f"sqlite:///{tmp}/h.db"}, "servers": [],
                                "alerts": {"itsm": [
                                    {"name": "ServiceNow-prod", "type": "servicenow", "url": self.base, "allow_http": True,
                                     "username": "svc", "password": "p"},
                                    {"name": "GLPI", "type": "glpi", "url": f"{self.base}/glpi/apirest.php",
                                     "allow_http": True, "app_token": "faux", "user_token": "usr"}]}}, fh)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["-c", path, "test-itsm"])
                self.assertEqual(main(["-c", path, "test-itsm", "--connector", "ServiceNow-prod"]), 0)
        text = out.getvalue()
        self.assertEqual(code, 2)                                   # GLPI : jeton refusé
        self.assertRegex(text, r"ServiceNow-prod\s+OK\s+ouverture : INC00\d+")
        self.assertRegex(text, r"GLPI\s+ERREUR .*HTTP 401")


if __name__ == "__main__":
    unittest.main()
