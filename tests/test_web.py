import importlib.util
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

from freelm.config import load_config
from freelm.simulation.demo import run_demo
from freelm.storage import Database, Repository
from freelm.users import import_users, read_users_csv
from freelm.web import api
from freelm.web.server import make_server

HAS_OPENPYXL = importlib.util.find_spec("openpyxl") is not None
MONDAY = datetime(2024, 9, 23)


class ParamsTest(unittest.TestCase):
    def test_defaults_last_30_days(self):
        p = api.parse_params({}, today=date(2024, 9, 30))
        self.assertEqual(p.start, datetime(2024, 9, 1))
        self.assertEqual(p.end, datetime(2024, 10, 1))  # fin incluse -> lendemain exclu

    def test_lists_and_validation(self):
        p = api.parse_params({"server": ["A", "B"], "user": ["J.Doe"], "start": ["2024-01-01"], "end": ["2024-01-31"]})
        self.assertEqual(p.servers, ["A", "B"])
        self.assertEqual(p.users, ["j.doe"])
        for bad in ({"start": ["2024-02-01"], "end": ["2024-01-01"]}, {"start": ["x"]},
                    {"granularity": ["year"]}, {"dimension": ["planet"]}):
            with self.assertRaises(api.ParamError):
                api.parse_params(bad)


class WebServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        result = run_demo(cls.tmp.name, days=7, end=MONDAY + timedelta(days=7), make_report=False)
        cls.cfg = load_config(result.config_path)
        cls.httpd = make_server(cls.cfg, port=0)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.period = "start=2024-09-16&end=2024-09-29"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def get_json(self, path):
        status, _, body = self.get(path)
        return status, json.loads(body)

    def test_static_files(self):
        for path, ctype in (("/", "text/html"), ("/app.js", "text/javascript"), ("/style.css", "text/css")):
            status, headers, _ = self.get(path)
            self.assertEqual(status, 200)
            self.assertTrue(headers["Content-Type"].startswith(ctype), headers["Content-Type"])
        self.assertEqual(self.get("/../freelm.demo.yaml")[0], 404)

    def test_meta(self):
        status, meta = self.get_json("/api/meta")
        self.assertEqual(status, 200)
        self.assertEqual(meta["servers"], ["ANSYS", "AUTODESK", "MATLAB"])
        self.assertIn("Simulation & calcul", meta["departments"])
        self.assertEqual(len(meta["collections"]), 3)  # un collecteur « simulation » par serveur
        self.assertIn("summary", meta["reports"])

    def test_all_reports(self):
        for rtype in api.REPORTS:
            status, r = self.get_json(f"/api/report?type={rtype}&{self.period}&dimension=department")
            self.assertEqual(status, 200, (rtype, r))
            self.assertTrue(r["rows"], rtype)
            for col in r["columns"]:  # chaque colonne annoncée est présente dans les lignes
                self.assertIn(col["key"], r["rows"][0], (rtype, col["key"]))

    def test_filters(self):
        _, all_ = self.get_json(f"/api/report?type=sessions&{self.period}")
        _, one = self.get_json(f"/api/report?type=sessions&{self.period}&server=ANSYS")
        self.assertTrue(0 < len(one["rows"]) < len(all_["rows"]))
        self.assertEqual({r["server"] for r in one["rows"]}, {"ANSYS"})
        user = one["rows"][0]["username"]
        _, mine = self.get_json(f"/api/report?type=sessions&{self.period}&user={user}")
        self.assertEqual({r["username"] for r in mine["rows"]}, {user})
        _, prod = self.get_json(f"/api/report?type=timeseries&{self.period}&product=AutoCAD%202024&granularity=hour")
        self.assertEqual({r["product"] for r in prod["rows"]}, {"AutoCAD 2024"})
        self.assertEqual(len(prod["chart"]["series"]), 1)

    def test_timeseries_granularities(self):
        counts = {}
        for g in api.GRANULARITIES:
            _, r = self.get_json(f"/api/report?type=timeseries&{self.period}&granularity={g}&metric=utilization")
            counts[g] = len(r["chart"]["x"])
        self.assertGreater(counts["hour"], counts["day"])
        self.assertGreaterEqual(counts["day"], counts["week"])
        self.assertGreaterEqual(counts["week"], counts["month"])

    def test_csv_export(self):
        status, headers, body = self.get(f"/api/export?type=breakdown&format=csv&{self.period}&dimension=department")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        text = body.decode("utf-8-sig")
        self.assertTrue(text.startswith("Service;Heures;Part (%)"))
        self.assertIn(",", text.splitlines()[1])  # décimale française

    def test_json_export(self):
        status, _, body = self.get(f"/api/export?type=summary&format=json&{self.period}")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["type"], "summary")

    def test_errors(self):
        self.assertEqual(self.get("/api/report?type=nope")[0], 400)
        self.assertEqual(self.get("/api/report?start=2024-13-01")[0], 400)
        self.assertEqual(self.get("/api/export?type=summary&format=pdf")[0], 400)
        self.assertEqual(self.get("/api/unknown")[0], 404)

    @unittest.skipUnless(HAS_OPENPYXL, "openpyxl non installé")
    def test_xlsx_exports(self):
        from io import BytesIO

        from openpyxl import load_workbook

        for rtype in list(api.REPORTS) + ["full"]:
            status, headers, body = self.get(f"/api/export?type={rtype}&format=xlsx&{self.period}")
            self.assertEqual(status, 200, rtype)
            wb = load_workbook(BytesIO(body))
            self.assertGreater(wb.worksheets[0].max_row, 4, rtype)


class UsersImportTest(unittest.TestCase):
    def test_ad_export_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "u.csv")
            with open(path, "w", encoding="utf-8-sig") as fh:
                fh.write('"SamAccountName","DisplayName","Department"\n"JDoe","John Doe","CAO"\n"asmith","Anna Smith",""\n')
            users = read_users_csv(path)
            self.assertEqual(users[0], {"username": "JDoe", "display_name": "John Doe", "department": "CAO"})
            db = Database("sqlite:///:memory:")
            from freelm.storage import create_schema

            create_schema(db)
            repo = Repository(db)
            repo.get_or_create_user("jdoe")
            self.assertEqual(import_users(repo, users), (1, 1))
            self.assertEqual(db.query("SELECT username, display_name, department FROM app_user ORDER BY username"),
                             [("asmith", "Anna Smith", None), ("jdoe", "John Doe", "CAO")])


if __name__ == "__main__":
    unittest.main()
