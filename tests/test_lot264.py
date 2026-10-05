"""26.4 : prévision, rapport de renouvellement, refacturation par projet, rapports planifiés sur mesure."""

import email
import io
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta
from email import policy
from pathlib import Path

import yaml

from heptagoneslm import projects
from heptagoneslm.config import ConfigError, load_config, parse_config
from heptagoneslm.reporting.dataset import period_bounds
from heptagoneslm.storage.repository import Repository
from heptagoneslm.web import admin, api, forecast, renewal
from tests.helpers import make_db

try:
    import openpyxl  # noqa: F401
    HAS_OPENPYXL = True
except ImportError:  # pragma: no cover
    HAS_OPENPYXL = False

START = datetime(2025, 6, 2)            # un lundi


def weekly(fn, weeks):
    return [(START.date() + timedelta(days=7 * w), fn(w)) for w in range(weeks)]


class FitTest(unittest.TestCase):
    def test_linear_trend_is_recovered(self):
        f = forecast.fit(weekly(lambda w: 5 + 0.25 * w, 30))
        self.assertTrue(f.trend)
        self.assertFalse(f.seasonal)
        self.assertAlmostEqual(f.b, 0.25, places=6)
        mid, low, high = f.predict(START.date() + timedelta(days=7 * 40))
        self.assertAlmostEqual(mid, 15.0, places=6)
        self.assertAlmostEqual(low, high, places=6)          # série parfaite : fourchette nulle

    def test_short_history(self):
        self.assertIsNone(forecast.fit(weekly(lambda w: 3, 3)))
        f = forecast.fit(weekly(lambda w: 3 + w, 5))          # moins de 8 semaines : pas de tendance
        self.assertFalse(f.trend)
        self.assertEqual(f.b, 0.0)

    def test_monthly_seasonality_from_one_year(self):
        f = forecast.fit(weekly(lambda w: 10 + (4 if (START.date() + timedelta(days=7 * w)).month == 8 else 0), 60))
        self.assertTrue(f.seasonal)
        self.assertGreater(f.predict(date(2026, 8, 3))[0], f.predict(date(2026, 10, 5))[0] + 3)

    def test_interval_widens_with_noise(self):
        noisy = forecast.fit(weekly(lambda w: 10 + (3 if w % 2 else -3), 20))
        mid, low, high = noisy.predict(START.date() + timedelta(days=7 * 25))
        self.assertGreater(high - low, 5)
        self.assertLessEqual(low, mid)

    def test_weekly_peaks_need_three_days(self):
        days = {date(2026, 9, 7): 4, date(2026, 9, 8): 6, date(2026, 9, 9): 5, date(2026, 9, 14): 9}
        self.assertEqual(forecast.weekly_peaks(days), [(date(2026, 9, 7), 6)])

    def test_add_months(self):
        self.assertEqual(forecast.add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(forecast.add_months(date(2026, 11, 15), 3), date(2027, 2, 15))


def growing_db(weeks=70, issued=10, denials=0, product="CAD Pro"):
    """Une feature dont le pic croît de 0,1 jeton par semaine."""
    db = make_db()
    repo = Repository(db)
    sid = repo.upsert_server("SRV", "flexlm", "27000@x", True)
    end = START + timedelta(days=7 * weeks)
    fid = repo.get_or_create_feature(sid, "CAD", START, product=product)
    db.execute("UPDATE feature SET last_seen = ?", [end])
    rows = []
    for d in range(7 * weeks):
        day = START + timedelta(days=d)
        if day.weekday() < 5:
            peak = min(issued, int(round(5 + 0.1 * (d // 7))))
            rows += [(fid, day.replace(hour=h), 12, peak * 6, peak, 0, issued, 0) for h in (9, 14)]
    db.executemany("INSERT INTO usage_hourly (feature_id, hour_start, samples, sum_in_use, max_in_use, min_in_use, "
                   "issued, max_queued) VALUES (?,?,?,?,?,?,?,?)", rows)
    for i in range(denials):
        db.insert("license_event", {"server_id": sid, "feature_id": fid, "event_time": end - timedelta(days=1, hours=i),
                                    "event_type": "DENIED", "license_count": 1})
    return db, end


def params(end, **extra):
    p = api.parse_params({"start": [(end - timedelta(days=30)).date().isoformat()],
                          "end": [(end - timedelta(days=1)).date().isoformat()]})
    for k, v in extra.items():
        setattr(p, k, v)
    return p


class ForecastReportTest(unittest.TestCase):
    def test_growth_needs_more_tokens(self):
        db, end = growing_db(issued=20)
        r = forecast.report_forecast(db, params(end, costs={"CAD Pro": 1000.0}))
        row = r["rows"][0]
        self.assertEqual((row["product"], row["issued"], row["weeks"]), ("CAD Pro", 20, 70))
        self.assertAlmostEqual(row["trend"], 0.43, delta=0.02)
        self.assertGreater(row["needed_12"], row["needed_3"])
        self.assertLessEqual(row["needed_3"], row["needed_6"])
        self.assertEqual(row["gap"], row["needed_12"] - 20)
        self.assertEqual(row["impact"], row["gap"] * 1000)
        self.assertTrue(row["advice"].startswith("Prévoir +") if row["gap"] > 0 else row["advice"])
        self.assertEqual(r["chart"]["xType"], "month")
        self.assertEqual(len(r["chart"]["x"]), 24)
        for col in r["columns"]:
            self.assertIn(col["key"], row)

    def test_insufficient_history(self):
        db, end = growing_db(weeks=2)
        row = forecast.report_forecast(db, params(end))["rows"][0]
        self.assertIsNone(row["needed_12"])
        self.assertIn("Historique insuffisant", row["advice"])

    def test_denials_mean_at_least_one_more(self):
        db, end = growing_db(weeks=20, issued=30, denials=3)
        row = forecast.report_forecast(db, params(end))["rows"][0]
        self.assertGreaterEqual(row["gap"], 1)


class RenewalTest(unittest.TestCase):
    def test_license_file_expiry(self):
        db, end = growing_db(issued=20)
        db.execute("UPDATE feature SET expires_on = ?", [(end + timedelta(days=60)).date()])
        r = renewal.report_renewal(db, params(end, costs={"CAD Pro": 500.0}))
        row = r["rows"][0]
        reference = min(date.today(), (end - timedelta(days=1)).date())       # fin de la période, au plus aujourd'hui
        self.assertEqual((row["source"], row["days_left"]),
                         ("Fichier de licence", ((end + timedelta(days=60)).date() - reference).days))
        self.assertGreaterEqual(row["proposal"], row["forecast"] or 0)
        self.assertEqual(row["impact"], row["delta"] * 500)
        self.assertIn("Renouveler", row["advice"])

    def test_contract_date_from_configuration(self):
        db, end = growing_db(weeks=12, issued=40)            # pic ~6 sur 40 jetons : excédent
        p = params(end, renewals={"CAD Pro": (end + timedelta(days=200)).date()})
        row = renewal.report_renewal(db, p)["rows"][0]
        self.assertEqual(row["source"], "Contrat (configuration)")
        self.assertLess(row["delta"], 0)
        self.assertIn("au lieu de 40", row["advice"])

    def test_outside_window(self):
        db, end = growing_db(weeks=12)
        db.execute("UPDATE feature SET expires_on = ?", [(end + timedelta(days=500)).date()])
        r = renewal.report_renewal(db, params(end))
        self.assertEqual(r["rows"], [])
        self.assertTrue(any("Aucune échéance" in n for n in r["notes"]))

    def test_denials_never_reduce(self):
        db, end = growing_db(weeks=12, issued=40, denials=2)
        db.execute("UPDATE feature SET expires_on = ?", [(end + timedelta(days=30)).date()])
        row = renewal.report_renewal(db, params(end))["rows"][0]
        self.assertGreaterEqual(row["delta"], 1)


class ProjectsTest(unittest.TestCase):
    def test_csv_aliases_and_separators(self):
        self.assertEqual(projects.read_projects_csv("SamAccountName;Projet\nJDupont;A12\n;x\n".encode("utf-8-sig")),
                         [("jdupont", "A12")])
        self.assertEqual(projects.read_projects_csv(b'"username","project"\n"bob","B7"\n'), [("bob", "B7")])
        with self.assertRaises(ValueError):
            projects.read_projects_csv(b"nom;service\na;b\n")

    def test_import_and_resolver_priority(self):
        db = make_db()
        r = projects.import_projects(db, "p.csv", b"utilisateur;projet\nalice;A12\nbob;B7\nalice;C3\n")
        self.assertEqual((r["users"], r["projects"]), (2, 2))
        rules = [projects.ProjectRule("Atelier", hosts=["ATL-*"]), projects.ProjectRule("BE", departments=["Bureau d'études"])]
        res = projects.resolver(db, rules)
        self.assertEqual(res.of("ALICE", "ATL-01"), "C3")                 # import CSV d'abord
        self.assertEqual(res.of("carol", "atl-07"), "Atelier")
        self.assertEqual(res.of("dan", "PC1", "bureau d'études"), "BE")
        self.assertIsNone(res.of("eve", "PC2", "Compta"))
        projects.import_projects(db, "q.csv", b"user;project\nzoe;Z\n", replace=True)
        self.assertEqual(projects.resolver(db, None).assigned, {"zoe": "Z"})

    def test_config_rules(self):
        base = {"database": {"connection_string": "sqlite:///:memory:"}, "servers": []}
        cfg = parse_config({**base, "projects": [{"project": "A12", "hosts": "CAO-A12-*", "users": ["j*"]}]})
        self.assertEqual((cfg.projects[0].project, cfg.projects[0].hosts), ("A12", ["CAO-A12-*"]))
        for bad in ([{"hosts": ["x"]}], [{"project": "A"}], {"project": "A"}):
            with self.assertRaises(ConfigError):
                parse_config({**base, "projects": bad})


class DemoReportsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from heptagoneslm.simulation.demo import run_demo

        cls.tmp = tempfile.TemporaryDirectory()
        cls.result = run_demo(cls.tmp.name, days=14, end=datetime(2024, 10, 1), make_report=False)
        cls.cfg = load_config(cls.result.config_path)
        from heptagoneslm.storage import Database

        cls.db = Database(cls.cfg.database.connection_string)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.tmp.cleanup()

    def p(self, **query):
        q = {"start": ["2024-09-17"], "end": ["2024-09-30"], **{k: [v] for k, v in query.items()}}
        return api.params_from_config(q, self.cfg)

    def test_breakdown_by_project(self):
        projects.import_projects(self.db, "p.csv", b"user;project\n" + "\n".join(
            f"{u};Projet {'A' if i % 2 else 'B'}" for i, u in enumerate(
                r[0] for r in self.db.query("SELECT username FROM app_user"))).encode())
        r = api.run_report(self.db, "breakdown", self.p(dimension="project"))
        keys = {row["key"] for row in r["rows"]}
        self.assertTrue({"Projet A", "Projet B"} <= keys, keys)
        self.assertIn("cost", [c["key"] for c in r["columns"]])
        m = api.run_report(self.db, "matrix", self.p(dimension="project"))
        self.assertTrue({"Projet A", "Projet B"} <= {row["key"] for row in m["rows"]})

    def test_forecast_and_renewal_reports(self):
        f = api.run_report(self.db, "forecast", self.p())
        self.assertTrue(f["rows"])
        self.assertTrue(all(r["weeks"] <= 3 for r in f["rows"]))          # 14 jours simulés : historique insuffisant
        self.assertTrue(all("Historique insuffisant" in r["advice"] for r in f["rows"]))
        r = api.run_report(self.db, "renewal", self.p())
        self.assertTrue(r["rows"])                               # échéances de la démonstration
        self.assertTrue(all(row["days_left"] >= -renewal.PAST_DAYS for row in r["rows"]))

    def test_named_renewal_from_autodesk_renewals(self):
        p = self.p()
        nd_products = [r["product"] for r in api.run_report(self.db, "named", p)["rows"]]
        p = replace(p, named_renewals={"*": date(2024, 12, 31)})
        rows = [r for r in renewal.report_renewal(self.db, p)["rows"] if r["server"] == "Autodesk Cloud"]
        self.assertTrue(rows and nd_products)
        self.assertTrue(all(r["source"] == "Abonnement (configuration)" for r in rows))

    @unittest.skipUnless(HAS_OPENPYXL, "openpyxl requis")
    def test_custom_subscription_dry_run(self):
        from heptagoneslm import mailer

        raw = yaml.safe_load(Path(self.result.config_path).read_text(encoding="utf-8"))
        raw.setdefault("web", {})["public_url"] = "https://heptagoneslm.test.local"     # lien vers la 1re vue
        raw["email"] = {"smtp_host": "127.0.0.1", "security": "none", "subscriptions": [
            {"name": "Achats", "recipients": "achats@test.local", "period": "last-month",
             "views": ["forecast", "renewal", "breakdown"], "dimension": "project", "servers": ["AUTODESK"]}]}
        cfg = parse_config(raw)
        out = Path(self.tmp.name) / "eml"
        r = mailer.deliver(cfg, self.db, cfg.email.subscriptions[0], today=date(2024, 10, 1), dry_run_dir=out)
        self.assertEqual(r.status, "DRY-RUN", r.detail)
        msg = email.message_from_bytes(r.path.read_bytes(), policy=policy.default)
        html = msg.get_body(("html",)).get_content()
        for title in ("Prévision du besoin", "Rapport de renouvellement", "Répartition par projet"):
            self.assertIn(title, html)
        self.assertIn("type=forecast", html)
        att = next(msg.iter_attachments())
        wb = openpyxl.load_workbook(io.BytesIO(att.get_content()))
        self.assertEqual(len(wb.sheetnames), 3)


class SubscriptionConfigTest(unittest.TestCase):
    base = {"database": {"connection_string": "sqlite:///:memory:"}, "servers": []}

    def test_views_and_dimension(self):
        cfg = parse_config({**self.base, "email": {"smtp_host": "s", "subscriptions": [
            {"name": "A", "recipients": ["a@b.fr"], "views": ["Forecast", "renewal"], "users": ["JDupont"],
             "period": "last-quarter"}]}})
        sub = cfg.email.subscriptions[0]
        self.assertEqual((sub.views, sub.users, sub.dimension, sub.period),
                         (["forecast", "renewal"], ["jdupont"], "department", "last-quarter"))
        for bad in ({"views": ["live"]}, {"views": ["summary"], "dimension": "planete"}):
            with self.assertRaises(ConfigError):
                parse_config({**self.base, "email": {"smtp_host": "s", "subscriptions": [
                    {"name": "A", "recipients": ["a@b.fr"], **bad}]}})

    def test_views_are_reports(self):
        from heptagoneslm.config import SUBSCRIPTION_DIMENSIONS, SUBSCRIPTION_VIEWS

        self.assertTrue(set(SUBSCRIPTION_VIEWS) <= set(api.REPORTS))
        self.assertEqual(set(SUBSCRIPTION_DIMENSIONS), set(api.DIMENSIONS))

    def test_last_quarter(self):
        self.assertEqual(period_bounds("last-quarter", date(2026, 10, 4)), (datetime(2026, 7, 1), datetime(2026, 10, 1)))
        self.assertEqual(period_bounds("last-quarter", date(2026, 2, 10)), (datetime(2025, 10, 1), datetime(2026, 1, 1)))

    def test_renewal_dates(self):
        cfg = parse_config({**self.base, "products": [{"pattern": "A*", "product": "A", "renewal": "2027-03-31"}],
                            "autodesk": {"renewals": {"Vault*": date(2027, 1, 15)}}})
        self.assertEqual(cfg.product_renewals(), {"A": date(2027, 3, 31)})
        self.assertEqual(cfg.autodesk.renewals, {"Vault*": date(2027, 1, 15)})
        with self.assertRaises(ConfigError):
            parse_config({**self.base, "products": [{"pattern": "A*", "product": "A", "renewal": "31/03/2027"}]})


class AdminSubscriptionsTest(unittest.TestCase):
    def setUp(self):
        from heptagoneslm.simulation.demo import run_demo

        self.tmp = tempfile.TemporaryDirectory()
        result = run_demo(self.tmp.name, days=2, end=datetime(2024, 9, 25), make_report=False)
        self.path = str(result.config_path)

    def tearDown(self):
        self.tmp.cleanup()

    def set_smtp(self):
        raw = yaml.safe_load(Path(self.path).read_text(encoding="utf-8"))
        raw["email"] = {"smtp_host": "smtp.test.local"}
        Path(self.path).write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")

    def test_requires_smtp(self):
        self.assertFalse(admin.list_subscriptions(self.path)["smtp"])
        with self.assertRaises(admin.AdminError):
            admin.save_subscription(self.path, {"name": "A", "recipients": "a@b.fr"}, None, None)

    def test_save_edit_delete(self):
        self.set_smtp()
        admin.save_subscription(self.path, {"name": "Achats", "recipients": "achats@test.fr; dsi@test.fr",
                                            "period": "last-quarter", "views": ["forecast", "renewal", "inconnue"],
                                            "dimension": "project", "servers": ["AUTODESK"], "attach_excel": True}, None, None)
        sub = load_config(self.path).email.subscriptions[0]
        self.assertEqual((sub.recipients, sub.views, sub.dimension, sub.servers),
                         (["achats@test.fr", "dsi@test.fr"], ["forecast", "renewal"], "project", ["AUTODESK"]))
        admin.save_subscription(self.path, {"name": "Achats T", "recipients": "a@test.fr"}, "Achats", None)
        listed = admin.list_subscriptions(self.path)
        self.assertEqual([s["name"] for s in listed["subscriptions"]], ["Achats T"])
        self.assertIn({"key": "forecast", "label": "Prévision"}, listed["views"])
        with self.assertRaises(admin.AdminError):
            admin.save_subscription(self.path, {"name": "X", "recipients": "pas-une-adresse"}, None, None)
        admin.delete_subscription(self.path, "Achats T", None)
        self.assertEqual(load_config(self.path).email.subscriptions, [])

    def test_autodesk_renewals(self):
        admin.save_costs(self.path, {"Vault": "900"}, None, {"Vault": "2027-01-31", "Revit": ""})
        cfg = load_config(self.path)
        self.assertEqual(cfg.autodesk.renewals, {"Vault": date(2027, 1, 31)})
        listed = {c["product"]: c["renewal"] for c in admin.list_costs(self.path, [])["costs"]}
        self.assertEqual(listed["Vault"], "2027-01-31")
        with self.assertRaises(admin.AdminError):
            admin.save_costs(self.path, {}, None, {"Vault": "fin janvier"})


if __name__ == "__main__":
    unittest.main()
