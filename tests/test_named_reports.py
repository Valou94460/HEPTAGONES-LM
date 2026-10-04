"""26.3.3 : licences Autodesk nommées (serveur « Autodesk Cloud ») dans toutes les rubriques."""

import tempfile
import unittest
from datetime import date, datetime, timedelta

from heptagoneslm.config import load_config
from heptagoneslm.simulation.demo import run_demo
from heptagoneslm.storage import Database
from heptagoneslm.web import api, insights, named

TODAY = date.today()


class NamedReportsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        result = run_demo(cls.tmp.name, days=3, make_report=False)
        cls.cfg = load_config(result.config_path)
        cls.db = Database(cls.cfg.database.connection_string)
        cls.db.__enter__()
        now = datetime.now().replace(microsecond=0)
        # usage quotidien : Vault Professional (2 utilisateurs) et une collection (1 utilisateur)
        monday = TODAY - timedelta(days=TODAY.weekday() + 7)
        with cls.db.transaction():
            cls.db.execute("INSERT INTO named_seat (vendor, product, email, display_name, last_used, days_used, report_date, "
                           "imported_at) VALUES ('autodesk', 'Vault Professional', 'a.martin@x.fr', 'A Martin', ?, 3, ?, ?)",
                           [monday, TODAY, now])
            cls.db.execute("INSERT INTO named_seat (vendor, product, email, display_name, last_used, days_used, report_date, "
                           "imported_at) VALUES ('autodesk', 'Vault Professional', 'b.durand@x.fr', 'B Durand', NULL, 0, ?, ?)",
                           [TODAY, now])
            for email, product, offset in (("a.martin@x.fr", "Vault Professional", 0), ("a.martin@x.fr", "Vault Professional", 1),
                                           ("c.petit@x.fr", "Vault Professional", 0),
                                           ("c.petit@x.fr", "Product Design & Manufacturing Collection", 2)):
                cls.db.execute("INSERT INTO named_usage_day (vendor, product, email, usage_date, imported_at) "
                               "VALUES ('autodesk', ?, ?, ?, ?)", [product, email, monday + timedelta(days=offset), now])
        cls.monday = monday

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.tmp.cleanup()

    def params(self, **query):
        q = {"start": [(self.monday - timedelta(days=3)).isoformat()], "end": [TODAY.isoformat()]}
        q.update({k: [v] if isinstance(v, str) else v for k, v in query.items()})
        return api.params_from_config(q, self.cfg)

    def named_rows(self, result):
        return {r["product"]: r for r in result["rows"] if r.get("server") == named.SERVER}

    def test_summary(self):
        rows = self.named_rows(api.report_summary(self.db, self.params()))
        vault = rows["Vault Professional"]
        self.assertEqual(vault["peak"], 2)                     # deux utilisateurs le lundi
        self.assertEqual(vault["users"], 2)
        self.assertEqual(vault["days"], 3)
        self.assertIsNone(vault["hours"])                      # aucune heure inventée
        self.assertIn("Product Design & Manufacturing Collection", rows)

    def test_product_filter_and_server_filter(self):
        rows = self.named_rows(api.report_summary(self.db, self.params(product="Vault Professional")))
        self.assertEqual(list(rows), ["Vault Professional"])
        self.assertEqual(self.named_rows(api.report_summary(self.db, self.params(server="AUTODESK"))), {})

    def test_timeseries_by_day(self):
        res = api.report_timeseries(self.db, self.params(granularity="day"))
        day = {r["period"]: r for r in res["rows"] if r["server"] == named.SERVER and r["product"] == "Vault Professional"}
        self.assertEqual(day[self.monday.isoformat()]["peak"], 2)
        hourly = api.report_timeseries(self.db, self.params(granularity="hour"))
        self.assertFalse(self.named_rows(hourly))
        self.assertTrue(any("journée" in n for n in hourly["notes"]))

    def test_breakdown_and_matrix(self):
        res = api.report_breakdown(self.db, self.params(dimension="user"))
        by_user = {r["key"]: r for r in res["rows"]}
        self.assertEqual(by_user["a.martin"]["days"], 2)
        self.assertEqual(by_user["c.petit"]["days"], 2)
        mat = api.report_matrix(self.db, self.params(dimension="user"))
        self.assertTrue(any(c["label"] == "Vault Professional (jours)" for c in mat["columns"]))

    def test_optimization_with_costs(self):
        p = self.params()
        p.named_costs = {"Vault*": 1000.0}
        rows = self.named_rows(insights.report_optimization(self.db, p))
        vault = rows["Vault Professional"]
        self.assertEqual(vault["issued"], 2)
        self.assertEqual(vault["surplus"], 1)                  # b.durand : jamais utilisée
        self.assertEqual(vault["savings"], 1000.0)
        self.assertIn("Récupérer 1 licence", vault["advice"])
        eff = self.named_rows(insights.report_efficiency(self.db, p))
        self.assertEqual(eff["Vault Professional"]["recommended"], 1)

    def test_meta_lists_named_products(self):
        meta = named.meta_extend(self.db, api.meta(self.db))
        self.assertIn(named.SERVER, meta["servers"])
        self.assertIn("Vault Professional", [p["name"] for p in meta["products"]])
        self.assertIn("a.martin", [u["username"] for u in meta["users"]])


if __name__ == "__main__":
    unittest.main()
