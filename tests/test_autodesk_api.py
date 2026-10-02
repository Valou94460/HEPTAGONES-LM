"""Connecteur API Autodesk (Business Success Plan Reporting) contre un faux serveur Autodesk."""

import io
import os
import tempfile
import threading
import time
import unittest
import urllib.request
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta
from pathlib import Path

from heptagoneslm import autodesk, autodesk_api
from heptagoneslm.autodesk_api import (ApiError, InsightsClient, NotConnected, TokenStore, choose_context, login,
                                       merge_with_previous, sync, sync_due)
from heptagoneslm.config import AutodeskApiConfig, ConfigError, parse_config
from tests.fake_autodesk import CLIENT_ID, CLIENT_SECRET, FakeAutodesk
from tests.helpers import make_db

TODAY = date(2026, 10, 2)
EXPORT = ("Product name,User email,First name,Last name,Assigned date,Last used date,Days used\n"
          "AutoCAD,jean.dupont@exemple.fr,Jean,Dupont,2025-01-10,2026-06-01,30\n"
          "AutoCAD,paul.martin@exemple.fr,Paul,Martin,2026-02-01,,0\n"
          "Revit,ancien@exemple.fr,Ancien,Salarié,2024-01-01,2025-01-01,3\n")


def free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def follow(url: str) -> None:
    """Navigateur de l'administrateur : suit la redirection Autodesk jusqu'à l'URL de rappel locale."""
    def run():
        try:
            urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=10).read()
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


class Base(unittest.TestCase):
    secret = None

    def setUp(self):
        self.fake = FakeAutodesk(**getattr(self, "fake_kw", {}))
        self.addCleanup(self.fake.close)
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = AutodeskApiConfig(client_id=CLIENT_ID, client_secret=self.secret, callback_port=free_port(),
                                     base_url=self.fake.url, token_file=str(self.tmp / "token.json"))
        self.store = TokenStore(self.cfg.token_file)

    def connect(self) -> list[dict]:
        return login(self.cfg, self.store, open_browser=False, timeout=20, on_url=follow)

    def client(self) -> InsightsClient:
        return InsightsClient(self.cfg, self.store, sleep=lambda s: None)


class LoginTest(Base):
    def test_pkce_login_lists_teams_and_stores_token(self):
        contexts = self.connect()
        self.assertEqual([c["contextId"] for c in contexts], ["TEAM-1"])
        token = self.store.load()
        self.assertTrue(token["access_token"] and token["refresh_token"])
        raw = (self.tmp / "token.json").read_bytes()
        if os.name == "nt":                                                 # chiffré par DPAPI sous Windows
            self.assertNotIn(token["refresh_token"].encode(), raw)
        self.assertEqual(self.fake.token_requests[0]["grant_type"], "authorization_code")
        self.assertIn("code_verifier", self.fake.token_requests[0])      # PKCE
        self.assertEqual(self.fake.token_requests[0]["client_id"], CLIENT_ID)

    def test_refresh_token_is_rotated(self):
        self.connect()
        first = self.store.load()
        expired = {**first, "expires_at": time.time() - 10}
        self.store.save(expired)
        self.client().contexts()
        second = self.store.load()
        self.assertNotEqual(first["refresh_token"], second["refresh_token"])   # usage unique : nouveau jeton conservé
        self.store.save({**expired, "refresh_token": first["refresh_token"]})   # ancien jeton : refusé
        with self.assertRaisesRegex(NotConnected, "autodesk-login"):
            self.client().contexts()

    def test_not_connected(self):
        with self.assertRaisesRegex(NotConnected, "autodesk-login"):
            self.client().contexts()

    def test_callback_rejects_foreign_state(self):
        def forged(url):
            follow(f"http://127.0.0.1:{self.cfg.callback_port}/autodesk/callback?code=x&state=autre")
        with self.assertRaisesRegex(ApiError, "state"):
            login(self.cfg, self.store, open_browser=False, timeout=20, on_url=forged)


class SecretLoginTest(Base):
    secret = CLIENT_SECRET

    def test_confidential_client_uses_basic_auth(self):
        self.connect()
        self.assertNotIn("client_id", self.fake.token_requests[0])


class SyncTest(Base):
    fake_kw = {"accepted_where": 1, "throttle_once": True}

    def test_sync_merges_with_portal_export(self):
        db = make_db()
        autodesk.import_report(db, self.tmp / "usage_report_20260915.csv", EXPORT.encode())
        self.connect()
        r = sync(db, self.client(), today=TODAY)
        self.assertEqual((r.context, r.users, r.carried, r.hashed), ("Bureau d'études", 4, 2, False))
        self.assertIn("2026-07-04", self.fake.last_where)                     # 2e syntaxe de dates acceptée, 90 jours
        self.assertIn("T00:00:00Z", self.fake.last_where)
        report_date, rows = autodesk.seats(db, 30, {"AutoCAD": 2300})
        self.assertEqual(report_date, TODAY)
        got = {(x["product"], x["email"]): (x["status"], x["days_used"]) for x in rows}
        self.assertEqual(got, {
            ("AutoCAD", "jean.dupont@exemple.fr"): ("active", 41),            # usage API (e-mail normalisé)
            ("Revit", "jean.dupont@exemple.fr"): ("active", 20),
            ("AutoCAD", "marie.curie@exemple.fr"): ("inactive", 6),           # dernière utilisation le 02/08
            ("Inventor Professional", "luc.besson@exemple.fr"): ("active", 15),
            ("AutoCAD", "paul.martin@exemple.fr"): ("never", 0),              # reprise de l'export : jamais utilisée
            ("Revit", "ancien@exemple.fr"): ("inactive", None),               # reprise : inactive depuis 2025
        })
        jean = next(x for x in rows if x["email"] == "jean.dupont@exemple.fr" and x["product"] == "AutoCAD")
        self.assertEqual(autodesk.parse_date(jean["assigned_date"]), date(2025, 1, 10))   # affectation conservée
        self.assertFalse(sync_due(db, self.cfg))
        self.assertTrue(sync_due(db, self.cfg, datetime.now() + timedelta(hours=25)))

    def test_dry_run_writes_nothing(self):
        db = make_db()
        self.connect()
        r = sync(db, self.client(), today=TODAY, dry_run=True)
        self.assertEqual(r.users, 4)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM named_seat"), 0)
        self.assertIsNone(autodesk_api.last_sync(db))


class HashedTest(Base):
    fake_kw = {"hashed": True, "accepted_where": None}

    def test_hashed_identities_and_no_date_filter(self):
        db = make_db()
        self.connect()
        r = sync(db, self.client(), today=TODAY)
        self.assertTrue(r.hashed)
        self.assertFalse(r.where_filtered)
        emails = {x["email"] for x in autodesk.seats(db)[1]}
        self.assertTrue(all("@" not in e and len(e) == 16 for e in emails))


class ContextTest(unittest.TestCase):
    def test_choose_context(self):
        a = {"contextId": "A", "alias": "Lyon", "reportingBenefits": ["ReportingAPIAccess"]}
        b = {"contextId": "B", "alias": "Paris", "reportingBenefits": ["ReportingAPIAccess"]}
        c = {"contextId": "C", "alias": "Sans API", "reportingBenefits": []}
        self.assertEqual(choose_context([a, c], None)["contextId"], "A")
        self.assertEqual(choose_context([a, b], "B")["contextId"], "B")
        with self.assertRaisesRegex(ApiError, "context_id"):
            choose_context([a, b], None)
        with self.assertRaisesRegex(ApiError, "introuvable"):
            choose_context([a], "Z")
        with self.assertRaisesRegex(ApiError, "Aucune équipe"):
            choose_context([c], None)

    def test_wrong_team_is_forbidden(self):
        fake = FakeAutodesk()
        self.addCleanup(fake.close)
        tmp = Path(tempfile.mkdtemp())
        cfg = AutodeskApiConfig(client_id=CLIENT_ID, context_id="TEAM-1", base_url=fake.url,
                                callback_port=free_port())
        store = TokenStore(tmp / "t.json")
        login(cfg, store, open_browser=False, timeout=20, on_url=follow)
        fake.contexts.append({"contextId": "TEAM-2", "alias": "Autre"})
        client = InsightsClient(AutodeskApiConfig(**{**cfg.__dict__, "context_id": "TEAM-2"}), store,
                                sleep=lambda s: None)
        fake.contexts.pop()  # l'équipe n'est plus accessible côté Autodesk : 403 explicite
        with self.assertRaisesRegex(ApiError, "introuvable"):
            sync(make_db(), client, today=TODAY)

    def test_merge_skips_unassigned(self):
        prev = [autodesk.SeatRow("AutoCAD", "a@x.fr", unassigned_date=date(2026, 1, 1)),
                autodesk.SeatRow("AutoCAD", "b@x.fr", last_used=date(2026, 3, 1), days_used=9)]
        merged, carried = merge_with_previous([], prev)
        self.assertEqual((carried, [m.email for m in merged], merged[0].days_used), (1, ["b@x.fr"], None))


class ConfigCliTest(Base):
    def test_config(self):
        base = {"database": {"connection_string": "sqlite:///:memory:"},
                "servers": [{"name": "A", "port_at_host": "27000@a"}]}
        cfg = parse_config({**base, "autodesk": {"api": {"client_id": "abc", "context_id": "T1",
                                                          "lookback_days": 30}}})
        api = cfg.autodesk.api
        self.assertEqual((api.client_id, api.context_id, api.lookback_days, api.callback_url),
                         ("abc", "T1", 30, "http://localhost:8770/autodesk/callback"))
        for bad in ({"client_id": ""}, {"client_id": "a", "lookback_days": 400}, {"client_id": "a", "callback_port": 80},
                    {"client_id": "a", "base_url": "http://autodesk.example"}):
            with self.assertRaises(ConfigError):
                parse_config({**base, "autodesk": {"api": bad}})

    def test_cli_login_sync_and_collect(self):
        from heptagoneslm import cli

        db_file = self.tmp / "h.db"
        config = self.tmp / "h.yaml"
        config.write_text(
            f"database:\n  connection_string: sqlite:///{db_file.as_posix()}\n"
            "servers:\n  - name: A\n    port_at_host: 27000@hote-inexistant\n"
            f"lmutil_path: {(self.tmp / 'absent').as_posix()}\n"
            f"autodesk:\n  api:\n    client_id: {CLIENT_ID}\n    base_url: {self.fake.url}\n"
            f"    callback_port: {self.cfg.callback_port}\n", encoding="utf-8")
        self.assertEqual(cli.main(["-c", str(config), "init-db"]), 0)
        out = io.StringIO()
        orig = autodesk_api.login

        def login_follow(*a, **kw):
            kw["on_url"] = lambda u: (print(u), follow(u))
            return orig(*a, **kw)
        autodesk_api.login = login_follow
        try:
            with redirect_stdout(out):
                self.assertEqual(cli.main(["-c", str(config), "autodesk-login", "--no-browser", "--timeout", "20"]), 0)
                self.assertEqual(cli.main(["-c", str(config), "autodesk-sync", "--dry-run"]), 0)
                cli.main(["-c", str(config), "collect"])
                cli.main(["-c", str(config), "collect"])                 # deuxième collecte : pas de nouvel appel
        finally:
            autodesk_api.login = orig
        text = out.getvalue()
        self.assertIn("context_id TEAM-1", text)
        self.assertIn("simulation, rien n'est écrit", text)
        self.assertEqual(text.count("Autodesk (API) : 4 ligne(s) d'usage"), 1)
        self.assertTrue((self.tmp / "data" / "autodesk_token.dpapi").is_file())   # jeton à côté de la configuration
        self.assertEqual(sum(1 for m, p in self.fake.calls if p == "/insights/v1/usage-queries"), 2)


if __name__ == "__main__":
    unittest.main()


class SetupWizardTest(Base):
    def answers(self, **kw):
        from heptagoneslm.setup.answers import Answers

        return Answers.from_dict({"autodesk_enabled": True, "autodesk_api_enabled": True,
                                  "autodesk_client_id": CLIENT_ID, "autodesk_callback_port": self.cfg.callback_port, **kw})

    def test_yaml_and_secret(self):
        a = self.answers(autodesk_client_secret="s3cret-autodesk", autodesk_context_id="TEAM-1")
        self.assertEqual(a.validate("autodesk"), [])
        cfg = a.config_dict()["autodesk"]
        self.assertEqual(cfg["import_dir"], "C:/HeptagonesLM/autodesk")
        self.assertEqual(cfg["api"]["client_secret"], "dpapi:C:/HeptagonesLM/secrets/autodesk_client_secret.dpapi")
        self.assertEqual((cfg["api"]["context_id"], cfg["api"]["token_file"]),
                         ("TEAM-1", "C:/HeptagonesLM/data/autodesk_token.dpapi"))
        self.assertNotIn("s3cret-autodesk", a.yaml_text())
        self.assertIn("s3cret-autodesk", a.secret_files().values())
        self.assertIsNone(a.check_config())
        self.assertEqual(self.answers(autodesk_api_enabled=False).config_dict()["autodesk"].get("api"), None)
        bad = self.answers(autodesk_client_id="x", autodesk_callback_port=80, autodesk_inactive_days=0)
        self.assertEqual({e["field"] for e in bad.validate("autodesk")},
                         {"autodesk_client_id", "autodesk_callback_port", "autodesk_inactive_days"})

    def test_real_login_code_path(self):
        from heptagoneslm.setup.system import System

        a = self.answers()
        entry = {**a.autodesk_api_entry(clear=True), "base_url": self.fake.url,
                 "token_file": str(self.tmp / "wizard_token.json")}
        teams = System.autodesk_login(System.__new__(System), entry, follow)
        self.assertEqual(teams[0]["contextId"], "TEAM-1")
        self.assertTrue((self.tmp / "wizard_token.json").is_file())

    def test_wizard_endpoints_in_simulation(self):
        from heptagoneslm.setup.server import SetupApp
        from heptagoneslm.setup.system import SimulatedSystem

        app = SetupApp(SimulatedSystem(delay=0), token="t")
        self.assertEqual(app.start_autodesk_login(self.answers(autodesk_client_id=""))["status"], "error")
        state = app.start_autodesk_login(self.answers())
        for _ in range(100):
            if state["status"] != "running":
                break
            time.sleep(0.05)
            state = app.autodesk_state()
        self.assertEqual(state["status"], "done")
        self.assertEqual([t["reporting"] for t in state["teams"]], [True, False])
