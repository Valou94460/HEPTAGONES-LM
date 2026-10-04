"""26.3.3 : page Administration de l'interface (serveurs de licences, import des exports Autodesk)."""

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import yaml

from heptagoneslm.config import load_config
from heptagoneslm.simulation.demo import run_demo
from heptagoneslm.web import admin
from heptagoneslm.web.server import make_server

CSV = ("Nom,Adresse e-mail,Produit,Date d'attribution,Dernière utilisation,Jours d'utilisation\n"
       "Jean Martin,jean.martin@entreprise.fr,AutoCAD,01/09/2026,30/09/2026,12\n"
       "Paul Durand,paul.durand@entreprise.fr,Revit,01/09/2026,,0\n").encode("utf-8")


class AdminModuleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        result = run_demo(self.tmp.name, days=2, end=datetime(2024, 9, 25), make_report=False)
        self.path = str(result.config_path)
        self.backups = str(Path(self.tmp.name) / "logs")

    def tearDown(self):
        self.tmp.cleanup()

    def names(self):
        return [s["name"] for s in yaml.safe_load(Path(self.path).read_text(encoding="utf-8"))["servers"]]

    def test_add_update_delete(self):
        before = self.names()
        admin.save_server(self.path, {"name": "SIEMENS", "port_at_host": "28000@licsrv-nx"}, None, self.backups)
        self.assertEqual(self.names(), before + ["SIEMENS"])
        admin.save_server(self.path, {"name": "NX", "port_at_host": "28001@licsrv-nx", "license_type": "rlm",
                                      "enabled": False}, "SIEMENS", self.backups)
        raw = yaml.safe_load(Path(self.path).read_text(encoding="utf-8"))
        nx = next(s for s in raw["servers"] if s["name"] == "NX")
        self.assertEqual((nx["port_at_host"], nx["license_type"], nx["enabled"]), ("28001@licsrv-nx", "rlm", False))
        load_config(self.path)                                                  # toujours valide
        self.assertTrue(list(Path(self.backups).glob("heptagoneslm.yaml.*.bak")))
        admin.delete_server(self.path, "NX", self.backups)
        self.assertEqual(self.names(), before)

    def test_tool_paths_cannot_be_set(self):
        admin.save_server(self.path, {"name": "X1", "port_at_host": "1@h", "lmutil_path": "C:/evil.exe"}, None, None)
        raw = yaml.safe_load(Path(self.path).read_text(encoding="utf-8"))
        self.assertNotIn("lmutil_path", next(s for s in raw["servers"] if s["name"] == "X1"))

    def test_invalid_input(self):
        for bad in ({"name": "A B", "port_at_host": "1@h"}, {"name": "A", "port_at_host": "h:1"},
                    {"name": "A", "port_at_host": "1@h & calc"}, {"name": "A", "port_at_host": "1@h", "license_type": "x"}):
            with self.assertRaises(admin.AdminError):
                admin.save_server(self.path, bad, None, None)
        with self.assertRaises(admin.AdminError):
            admin.save_server(self.path, {"name": self.names()[0], "port_at_host": "1@h"}, None, None)   # doublon

    def test_cannot_delete_last_server(self):
        names = self.names()
        for n in names[1:]:
            admin.delete_server(self.path, n, None)
        with self.assertRaises(admin.AdminError):
            admin.delete_server(self.path, names[0], None)

    def test_costs(self):
        self.assertEqual(admin.save_costs(self.path, {"Vault Professional": "1 250,50", "Revit": ""}, self.backups), 1)
        cfg = load_config(self.path)
        self.assertEqual(cfg.autodesk.annual_costs, {"Vault Professional": 1250.5})
        listed = {c["product"]: c["cost"] for c in admin.list_costs(self.path, ["Revit"])["costs"]}
        self.assertEqual(listed, {"Revit": None, "Vault Professional": 1250.5})
        with self.assertRaises(admin.AdminError):
            admin.save_costs(self.path, {"Vault": "beaucoup"}, None)

    def test_test_server_with_simulator(self):
        r = admin.test_server(self.path, {"name": "T", "port_at_host": "27000@licsrv-adsk"})   # faux lmutil de la démo
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["features"])
        self.assertFalse(admin.test_server(self.path, {"name": "T", "port_at_host": "1@inconnu"})["ok"])


class AdminRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        result = run_demo(cls.tmp.name, days=2, end=datetime(2024, 9, 25), make_report=False)
        cls.path = str(result.config_path)
        raw = yaml.safe_load(Path(cls.path).read_text(encoding="utf-8"))
        raw.setdefault("web", {})["demo"] = False          # installation réelle (pas de démonstration)
        Path(cls.path).write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
        cls.cfg = load_config(cls.path)
        cls.httpd = make_server(cls.cfg, port=0, config_path=cls.path)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def req(self, path, body=None, headers=None, raw=None):
        h = {"X-HeptagonesLM": "1", **(headers or {})}
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        if body is not None:
            h["Content-Type"] = "application/json"
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base + path, data=data, headers=h)) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_meta_and_list(self):
        self.assertTrue(self.req("/api/meta")[1]["admin"])                       # poste local : administrateur
        code, body = self.req("/api/admin/servers")
        self.assertEqual(code, 200)
        self.assertIn("AUTODESK", [s["name"] for s in body["servers"]])

    def test_save_requires_custom_header(self):
        code, _ = self.req("/api/admin/servers", {"action": "save", "server": {"name": "Z", "port_at_host": "1@z"}},
                           headers={"X-HeptagonesLM": "0"})
        self.assertEqual(code, 403)

    def test_save_and_test_routes(self):
        code, body = self.req("/api/admin/servers", {"action": "test", "server": {"name": "T", "port_at_host": "27000@licsrv-adsk"}})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        code, body = self.req("/api/admin/servers", {"action": "save", "server": {"name": "R1", "port_at_host": "5053@rlm1",
                                                                                   "license_type": "rlm"}})
        self.assertEqual(code, 200, body)
        code, body = self.req("/api/admin/servers", {"action": "save", "server": {"name": "bad name", "port_at_host": "1@x"}})
        self.assertEqual(code, 422)
        self.req("/api/admin/servers", {"action": "delete", "original": "R1"})

    def test_costs_route_reloads_configuration(self):
        code, body = self.req("/api/admin/costs", {"costs": {"Vault Professional": "900"}})
        self.assertEqual(code, 200, body)
        self.assertEqual(self.httpd.RequestHandlerClass.config.autodesk.annual_costs.get("Vault Professional"), 900.0)
        code, body = self.req("/api/admin/costs")
        self.assertIn("Vault Professional", [c["product"] for c in body["costs"]])

    def test_autodesk_upload(self):
        code, body = self.req("/api/admin/autodesk-import", raw=CSV,
                              headers={"X-Filename": urllib.parse.quote("usage report.csv"),
                                       "Content-Type": "application/octet-stream"})
        self.assertEqual(code, 200, body)
        self.assertEqual((body["status"], body["rows"]), ("importé", 2))
        code, body = self.req("/api/admin/autodesk-import", raw=b"MZ", headers={"X-Filename": "outil.exe"})
        self.assertEqual(code, 422)


class AdminRoleTest(unittest.TestCase):
    def test_reader_is_refused(self):
        from heptagoneslm.web.roles import Access
        from heptagoneslm.web.server import HeptagonesLMHandler
        h = HeptagonesLMHandler.__new__(HeptagonesLMHandler)
        h.config_path, h.config = "x.yaml", type("C", (), {"web": type("W", (), {"demo": False})()})()
        self.assertFalse(h._is_admin(Access("reader")))
        self.assertTrue(h._is_admin(Access("admin")))
        self.assertFalse(h._is_admin(None))


if __name__ == "__main__":
    unittest.main()
