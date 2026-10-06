"""26.5 : accueil « À traiter », visite guidée mémorisée par utilisateur, checklist de démarrage."""

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import yaml

from heptagoneslm.config import load_config
from heptagoneslm.simulation.demo import run_demo
from heptagoneslm.storage import Database
from heptagoneslm.storage.schema import SCHEMA_VERSION
from heptagoneslm.web import api, named, prefs
from heptagoneslm.web.server import make_server


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        result = run_demo(cls.tmp.name, days=14, end=datetime(2024, 10, 1), make_report=False)
        cls.path = str(result.config_path)
        raw = yaml.safe_load(Path(cls.path).read_text(encoding="utf-8"))
        raw.setdefault("web", {})["demo"] = False
        Path(cls.path).write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
        cls.cfg = load_config(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


class PrefsTest(Base):
    def setUp(self):
        self.db = Database(self.cfg.database.connection_string)

    def tearDown(self):
        self.db.close()

    def test_schema_version(self):
        self.assertGreaterEqual(SCHEMA_VERSION, 10)

    def test_onboarding_roundtrip(self):
        self.assertFalse(prefs.get(self.db, "Alice")["onboarding_done"])
        self.assertTrue(prefs.set_onboarding(self.db, "Alice", True)["onboarding_done"])
        self.assertTrue(prefs.get(self.db, "alice")["onboarding_done"])          # insensible à la casse
        self.assertFalse(prefs.get(self.db, "bob")["onboarding_done"])
        self.assertFalse(prefs.set_onboarding(self.db, "alice", False)["onboarding_done"])

    def test_checklist_shape(self):
        c = prefs.checklist(self.cfg, self.db, named.meta_extend(self.db, api.meta(self.db)))
        ids = [i["id"] for i in c["items"]]
        for k in ("servers", "costs", "renewals", "projects", "smtp"):
            self.assertIn(k, ids)
        required = [i for i in c["items"] if not i["optional"]]
        self.assertEqual(c["total"], len(required))
        self.assertEqual(c["done"], sum(1 for i in required if i["ok"]))


class RoutesTest(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.httpd = make_server(cls.cfg, port=0, config_path=cls.path)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        super().tearDownClass()

    def req(self, path, body=None, headers=None):
        h = {"X-HeptagonesLM": "1", **(headers or {})}
        data = json.dumps(body).encode() if body is not None else None
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base + path, data=data, headers=h)) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_prefs_routes(self):
        self.assertFalse(self.req("/api/prefs")[1]["onboarding_done"])
        self.assertEqual(self.req("/api/prefs", {"onboarding": "bidon"})[0], 400)
        self.assertEqual(self.req("/api/prefs", {"onboarding": "done"}, headers={"X-HeptagonesLM": "0"})[0], 403)
        self.assertTrue(self.req("/api/prefs", {"onboarding": "done"})[1]["onboarding_done"])
        self.assertTrue(self.req("/api/prefs")[1]["onboarding_done"])
        self.assertFalse(self.req("/api/prefs", {"onboarding": "reset"})[1]["onboarding_done"])

    def test_checklist_route(self):
        code, body = self.req("/api/admin/checklist")
        self.assertEqual(code, 200)
        self.assertTrue(body["items"])

    def test_dashboard_has_todo(self):
        code, body = self.req("/api/report?type=dashboard&start=2024-09-17&end=2024-09-30")
        self.assertEqual(code, 200)
        self.assertIsInstance(body["todo"], list)
        for t in body["todo"]:
            self.assertTrue({"severity", "title", "target", "label"} <= set(t))


if __name__ == "__main__":
    unittest.main()
