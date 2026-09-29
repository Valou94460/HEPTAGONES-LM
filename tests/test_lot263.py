"""Emprunts, réservations (named user), multi-sites et licences inutilisées."""

import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from heptagoneslm.collectors.flexlm.lmstat import parse_lmstat
from heptagoneslm.config import ConfigError, load_config, parse_config
from heptagoneslm.service import CollectionService
from heptagoneslm.simulation.demo import run_demo
from heptagoneslm.storage import Database, create_schema
from heptagoneslm.web.server import make_server
from tests.helpers import FIXTURES, make_db

REF = datetime(2024, 9, 23, 10, 12)
MONDAY = datetime(2024, 9, 23)


def status():
    return parse_lmstat((FIXTURES / "lmstat_borrow_reservations.txt").read_text(encoding="utf-8"), "27000@licsrv-mlm", REF)


def cfg(**extra):
    return parse_config({"database": {"connection_string": "sqlite:///:memory:"},
                         "servers": [{"name": "MATLAB", "port_at_host": "27000@licsrv-mlm"}],
                         "sites": [{"pattern": "PC*", "site": "Paris"}, {"pattern": "laptop-*", "site": "Nomades"}],
                         **extra})


class ParserTest(unittest.TestCase):
    def test_borrow_and_reservations(self):
        feats = {f.name: f for f in status().features}
        matlab = feats["MATLAB"]
        self.assertEqual([(c.user, c.borrowed) for c in matlab.checkouts],
                         [("jdoe", False), ("lmartin", True), ("cdurand", False)])
        self.assertEqual([(r.kind, r.name, r.count) for r in matlab.reservations],
                         [("USER", "cdurand", 1), ("GROUP", "simulation", 2)])
        self.assertEqual((matlab.issued, matlab.in_use), (5, 3))      # les réservations ne faussent pas les totaux
        self.assertEqual([(r.kind, r.name) for r in feats["Simulink"].reservations], [("HOST", "CAO-WS01")])


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.cfg, self.db = cfg(), make_db()
        self.svc = CollectionService(self.cfg, self.db)
        self.sid = self.svc.sync_servers()["MATLAB"]

    def apply(self, st):
        with self.db.transaction():
            self.svc.apply_status(self.sid, st, "lmstat", track_sessions=True)

    def test_borrowed_sessions_sites_and_reservations(self):
        self.apply(status())
        rows = self.db.query_dicts("SELECT u.username, s.borrowed, w.site FROM license_session s "
                                   "JOIN app_user u ON u.id = s.user_id JOIN workstation w ON w.id = s.workstation_id "
                                   "ORDER BY u.username")
        self.assertEqual([(r["username"], bool(r["borrowed"]), r["site"]) for r in rows],
                         [("cdurand", False, "Paris"), ("jdoe", False, "Paris"), ("lmartin", True, "Nomades")])
        res = self.db.query("SELECT kind, name, reserved FROM feature_reservation ORDER BY kind, name")
        self.assertEqual(res, [("GROUP", "simulation", 2), ("HOST", "cao-ws01", 1), ("USER", "cdurand", 1)])
        # sondage suivant : réservation supprimée du fichier d'options -> retirée
        st = status()
        st.features[0].reservations = st.features[0].reservations[:1]
        st.features[1].reservations = []
        self.apply(st)
        self.assertEqual(self.db.query("SELECT kind, name FROM feature_reservation"), [("USER", "cdurand")])

    def test_site_rules_reapplied_to_history(self):
        self.apply(status())
        self.svc.config = cfg(sites=[{"pattern": "*", "site": "Lyon"}])
        self.assertEqual(self.svc.apply_product_mapping(), 3)
        self.assertEqual({r[0] for r in self.db.query("SELECT site FROM workstation")}, {"Lyon"})

    def test_site_config_validation(self):
        with self.assertRaises(ConfigError):
            cfg(sites=[{"pattern": "PC*"}])
        self.assertIsNone(cfg().resolve_site("srv-01"))


class MigrationTest(unittest.TestCase):
    def test_existing_database_gets_new_column_and_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ancienne.db"
            con = sqlite3.connect(path)            # base v5 : license_session sans colonne borrowed
            con.execute("CREATE TABLE license_session (id INTEGER PRIMARY KEY AUTOINCREMENT, feature_id INT NOT NULL, "
                        "user_id INT NOT NULL, workstation_id INT NOT NULL, display TEXT, handle TEXT, "
                        "license_count INT NOT NULL DEFAULT 1, checkout_time DATETIME2 NOT NULL, checkin_time DATETIME2, "
                        "last_seen DATETIME2 NOT NULL, source TEXT NOT NULL, close_reason TEXT)")
            con.execute("INSERT INTO license_session (feature_id, user_id, workstation_id, checkout_time, last_seen, source) "
                        "VALUES (1, 1, 1, '2024-09-23 08:00:00', '2024-09-23 09:00:00', 'lmstat')")
            con.commit()
            con.close()
            db = Database(f"sqlite:///{path}")
            create_schema(db)
            self.assertEqual(db.scalar("SELECT borrowed FROM license_session"), 0)
            self.assertEqual(db.scalar("SELECT COUNT(*) FROM feature_reservation"), 0)
            db.close()


class NotMigratedTest(unittest.TestCase):
    def test_reservations_view_without_table(self):
        from heptagoneslm.web import api, insights

        db = make_db()
        db.execute("DROP TABLE feature_reservation")
        r = insights.report_reservations(db, api.parse_params({}))
        self.assertEqual(r["rows"], [])
        self.assertIn("init-db", r["notes"][0])


class DemoRelaunchTest(unittest.TestCase):
    def test_demo_relaunch_on_same_database(self):
        """La relance remplace les données (réservations comprises) sans violer les clés étrangères."""
        with tempfile.TemporaryDirectory() as tmp:
            end, url = MONDAY + timedelta(days=2), f"sqlite:///{Path(tmp) / 'partagee.db'}"   # base conservée, comme SQL Server
            run_demo(tmp, days=2, end=end, make_report=False, connection_string=url)
            run_demo(tmp, days=2, end=end, make_report=False, connection_string=url)
            db = Database(url)
            try:
                self.assertEqual(db.scalar("PRAGMA foreign_key_check"), None)
                self.assertEqual(db.scalar("SELECT COUNT(*) FROM feature_reservation"), 2)
            finally:
                db.close()


class DemoReportsTest(unittest.TestCase):
    """Nouvelles vues sur la démonstration, via le serveur HTTP réel."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        result = run_demo(cls.tmp.name, days=7, end=MONDAY + timedelta(days=7), make_report=False)
        cls.httpd = make_server(load_config(result.config_path), port=0)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def get(self, path, **query):
        q = urllib.parse.urlencode({"start": "2024-09-16", "end": "2024-09-29", **query}, doseq=True)
        with urllib.request.urlopen(f"{self.base}{path}?{q}") as r:
            return json.loads(r.read())

    def test_unused_licences(self):
        r = self.get("/api/report", type="unused")
        self.assertEqual([(x["product"], x["issued"], x["annual_cost"], x["last_used"]) for x in r["rows"]],
                         [("Maya 2024", 2, 4200.0, None)])
        self.assertEqual(r["kpis"][2]["value"], 4200.0)

    def test_reservations(self):
        r = self.get("/api/report", type="reservations")
        kinds = {x["kind"]: x for x in r["rows"]}
        self.assertEqual(set(kinds), {"Groupe d'utilisateurs", "Utilisateur"})
        group, user = kinds["Groupe d'utilisateurs"], kinds["Utilisateur"]
        self.assertEqual((group["beneficiary"], group["reserved"], group["rate"]), ("simulation", 2, None))
        self.assertIsNotNone(user["rate"])
        self.assertIn(user["advice"], ("Réservation utilisée", "Peu utilisée : envisager de libérer la réservation"))

    def test_sites(self):
        meta = self.get("/api/meta")
        self.assertEqual(meta["sites"], ["Lyon", "Paris", "Toulouse"])
        by_site = self.get("/api/report", type="breakdown", dimension="site")
        labels = {row.get("key") or row.get("label") for row in by_site["rows"]}
        self.assertTrue({"Paris", "Lyon", "Toulouse"} <= labels, labels)
        paris = self.get("/api/report", type="sessions", site="Paris")
        self.assertTrue(paris["rows"])
        self.assertEqual({x["site"] for x in paris["rows"]}, {"Paris"})

    def test_borrowed_licences(self):
        sessions = self.get("/api/report", type="sessions", product="MATLAB")
        self.assertIn("Oui", {x["borrowed"] for x in sessions["rows"]})
        self.assertTrue({x["borrowed"] for x in sessions["rows"]} <= {"Oui", ""})
        live = self.get("/api/report", type="live")
        self.assertIn("Jetons empruntés (hors ligne)", [k["label"] for k in live["kpis"]])


if __name__ == "__main__":
    unittest.main()
