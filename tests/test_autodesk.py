"""Licences Autodesk « utilisateur nommé » : import des exports du portail Autodesk Account."""

import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime
from pathlib import Path

from heptagoneslm import autodesk
from heptagoneslm.config import ConfigError, parse_config
from heptagoneslm.storage import Database, create_schema
from heptagoneslm.web import api
from tests.helpers import make_db

# Export « Usage report » (présentation 2026 : colonnes « Product name », « Days used »…)
PORTAIL = (
    "Product name,User email,First name,Last name,Seat assignment,Assigned date,Last used date,Days used,Version\n"
    "AutoCAD,Jean.Dupont@exemple.fr,Jean,Dupont,Assigned,2025-01-10,2026-09-28,85,2026\n"
    "AutoCAD,marie.curie@exemple.fr,Marie,Curie,Assigned,2025-03-02,2026-06-15,12,2026\n"
    "Revit,paul.martin@exemple.fr,Paul,Martin,Assigned,2026-02-01,,0,2026\n"
    "Revit,jean.dupont@exemple.fr,Jean,Dupont,Assigned,2025-05-05,2026-09-30,40,2026\n"
    "Civil 3D,ancien@exemple.fr,Ancien,Salarié,Unassigned,2024-01-01,2025-01-01,3,2025\n"
    ",,,,,,,,\n"
    "Total,,,,,,,,\n"
)

# Export en français, séparateur « ; », dates jj/mm/aaaa, lignes de titre avant l'en-tête, Windows-1252
FRANCAIS = (
    "Rapport d'utilisation Autodesk\n"
    "Équipe : Bureau d'études\n"
    "\n"
    "Nom du produit;Adresse e-mail;Nom complet;Date d'affectation;Dernière utilisation;Jours d'utilisation\n"
    "Inventor Professional;luc.besson@exemple.fr;Luc Besson;15/03/2025;20/08/2026;30\n"
    "AutoCAD LT;eve.durand@exemple.fr;Ève Durand;01/09/2026;;0\n"
).encode("cp1252")


def write(folder: Path, name: str, content) -> Path:
    path = folder / name
    path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
    return path


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_portal_export(self):
        rep = autodesk.parse_report(write(self.tmp, "usage_report_20261001.csv", PORTAIL))
        self.assertEqual(rep.report_date, date(2026, 10, 1))      # date tirée du nom du fichier
        self.assertEqual(len(rep.rows), 5)
        self.assertEqual(rep.skipped, 1)                          # ligne « Total » sans e-mail (les vides sont écartés)
        first = rep.rows[0]
        self.assertEqual((first.product, first.email, first.name), ("AutoCAD", "jean.dupont@exemple.fr", "Jean Dupont"))
        self.assertEqual((first.assigned_date, first.last_used, first.days_used), (date(2025, 1, 10), date(2026, 9, 28), 85))
        self.assertIsNone(rep.rows[2].last_used)
        self.assertEqual(rep.columns["email"], "User email")

    def test_ambiguous_dates_follow_unambiguous_ones(self):
        text = "Product name,User email,Last used date\nRevit,a@exemple.fr,03/04/2026\nRevit,b@exemple.fr,03/25/2026\n"
        rep = autodesk.parse_report(write(self.tmp, "x.csv", text))
        self.assertEqual([r.last_used for r in rep.rows], [date(2026, 3, 4), date(2026, 3, 25)])
        french = "Produit;Courriel;Dernière utilisation\nRevit;a@exemple.fr;03/04/2026\n"
        self.assertEqual(autodesk.parse_report(write(self.tmp, "y.csv", french)).rows[0].last_used, date(2026, 4, 3))

    def test_french_export_with_title_rows(self):
        rep = autodesk.parse_report(self.tmp / "export.csv", FRANCAIS, today=date(2026, 9, 30))
        self.assertEqual(rep.report_date, date(2026, 9, 30))
        self.assertEqual([(r.product, r.email, r.name) for r in rep.rows],
                         [("Inventor Professional", "luc.besson@exemple.fr", "Luc Besson"),
                          ("AutoCAD LT", "eve.durand@exemple.fr", "Ève Durand")])
        self.assertEqual(rep.rows[0].assigned_date, date(2025, 3, 15))
        self.assertEqual(rep.rows[0].last_used, date(2026, 8, 20))

    def test_snake_case_and_report_date_column(self):
        text = ("report_date\tproduct_name\tuser_email\tdays_inactive\tdays_used\n"
                "10/02/2026\tMaya\ta@exemple.fr\t45\t2\n")
        rep = autodesk.parse_report(write(self.tmp, "x.txt", text))
        self.assertEqual(rep.report_date, date(2026, 10, 2))      # colonnes en anglais : format américain mm/jj
        self.assertEqual(rep.rows[0].days_inactive, 45)

    def test_dates(self):
        self.assertEqual(autodesk.parse_date("2026-09-28T10:00:00Z"), date(2026, 9, 28))
        self.assertEqual(autodesk.parse_date("28/09/2026"), date(2026, 9, 28))
        self.assertEqual(autodesk.parse_date("09/28/2026"), date(2026, 9, 28))
        self.assertEqual(autodesk.parse_date("28.09.26"), date(2026, 9, 28))
        self.assertEqual(autodesk.parse_date("10/02/2026"), date(2026, 2, 10))
        self.assertEqual(autodesk.parse_date("10/02/2026", dayfirst=False), date(2026, 10, 2))
        self.assertEqual(autodesk.parse_date("Sep 28, 2026"), date(2026, 9, 28))
        for empty in ("", None, "Never", "-", "n/a"):
            self.assertIsNone(autodesk.parse_date(empty))

    def test_unrecognized_file(self):
        with self.assertRaisesRegex(autodesk.ImportError_, "Usage report"):
            autodesk.parse_report(write(self.tmp, "x.csv", "a,b\n1,2\n"))
        with self.assertRaisesRegex(autodesk.ImportError_, "Aucune ligne"):
            autodesk.parse_report(write(self.tmp, "y.csv", "Product name,User email\n"))

    @unittest.skipIf(importlib.util.find_spec("openpyxl") is None, "openpyxl non installé")
    def test_xlsx_export(self):
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(["Usage report"])
        ws.append(["Product name", "User email", "Last used date", "Days used"])
        ws.append(["Revit", "a@exemple.fr", datetime(2026, 9, 1), 4])
        buf = io.BytesIO()
        wb.save(buf)
        rep = autodesk.parse_report(self.tmp / "usage_2026-09-30.xlsx", buf.getvalue())
        self.assertEqual((rep.rows[0].product, rep.rows[0].last_used, rep.report_date),
                         ("Revit", date(2026, 9, 1), date(2026, 9, 30)))


class ImportTest(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        self.tmp = Path(tempfile.mkdtemp())
        self.file = write(self.tmp, "usage_report_20261001.csv", PORTAIL)

    def test_import_is_idempotent(self):
        r = autodesk.import_report(self.db, self.file)
        self.assertEqual((r["status"], r["rows"], r["report_date"]), ("importé", 5, "2026-10-01"))
        self.assertEqual(autodesk.import_report(self.db, self.file)["status"], "déjà importé")
        self.assertEqual(autodesk.import_report(self.db, self.file, force=True)["status"], "importé")
        self.assertEqual(self.db.scalar("SELECT COUNT(*) FROM named_seat"), 5)
        self.assertEqual(self.db.scalar("SELECT COUNT(*) FROM named_import"), 1)

    def test_same_report_date_replaces_and_dedupes(self):
        autodesk.import_report(self.db, self.file)
        newer = PORTAIL.replace("2026-06-15,12", "2026-09-29,13") + "AutoCAD,marie.curie@exemple.fr,,,,,2026-01-01,1,\n"
        autodesk.import_report(self.db, write(self.tmp, "usage_report_2026-10-01_v2.csv", newer))
        rows = self.db.query("SELECT last_used FROM named_seat WHERE email = 'marie.curie@exemple.fr'")
        self.assertEqual(len(rows), 1)                             # doublon (produit, utilisateur) fusionné
        self.assertEqual(autodesk.parse_date(rows[0][0]), date(2026, 9, 29))   # la plus récente l'emporte
        self.assertEqual(self.db.scalar("SELECT COUNT(*) FROM named_seat"), 5)

    def test_statuses_and_costs(self):
        autodesk.import_report(self.db, self.file)
        report_date, rows = autodesk.seats(self.db, 30, {"autocad*": 2300, "Revit": 3100})
        self.assertEqual(report_date, date(2026, 10, 1))
        got = {(r["product"], r["email"]): (r["status"], r["inactive_for"], r["recoverable_cost"]) for r in rows}
        self.assertEqual(got, {
            ("Revit", "paul.martin@exemple.fr"): ("never", None, 3100),
            ("AutoCAD", "marie.curie@exemple.fr"): ("inactive", 108, 2300),
            ("AutoCAD", "jean.dupont@exemple.fr"): ("active", 3, None),
            ("Revit", "jean.dupont@exemple.fr"): ("active", 1, None),
            ("Civil 3D", "ancien@exemple.fr"): ("inactive", 638, None),
        })
        self.assertEqual(rows[0]["status"], "never")              # à traiter en premier
        _, only = autodesk.seats(self.db, 200, products=["revit"])
        self.assertEqual({r["status"] for r in only}, {"never", "active"})

    def test_unassigned_and_days_inactive(self):
        row = {"unassigned_date": "2026-09-01", "days_used": 3}
        self.assertEqual(autodesk.seat_status(row, date(2026, 10, 1), 30), ("unassigned", None))
        self.assertEqual(autodesk.seat_status({"days_inactive": 31, "days_used": 4}, date(2026, 10, 1), 30),
                         ("inactive", 31))
        self.assertEqual(autodesk.seat_status({"days_used": 0}, date(2026, 10, 1), 30), ("never", None))

    def test_folder_import(self):
        write(self.tmp, "illisible.csv", "a,b\n1,2\n")
        write(self.tmp, "~$verrou.xlsx", b"")
        write(self.tmp, "notes.pdf", b"%PDF")
        results = {r["file"]: r["status"] for r in autodesk.import_folder(self.db, self.tmp)}
        self.assertEqual(results, {"illisible.csv": "erreur", "usage_report_20261001.csv": "importé"})
        again = {r["file"]: r["status"] for r in autodesk.import_folder(self.db, self.tmp)}
        self.assertEqual(again["usage_report_20261001.csv"], "déjà importé")
        self.assertEqual(autodesk.import_folder(self.db, self.tmp / "absent"), [])


class ReportTest(unittest.TestCase):
    def params(self, **kw):
        p = api.parse_params({})
        p.named_costs = {"AutoCAD": 2300.0, "Revit": 3100.0}
        for k, v in kw.items():
            setattr(p, k, v)
        return p

    def test_report(self):
        db = make_db()
        tmp = Path(tempfile.mkdtemp())
        empty = api.run_report(db, "named", self.params())
        self.assertEqual((empty["rows"], empty["description"]), ([], "Aucun export importé"))
        self.assertIn("import-autodesk", empty["notes"][0])
        autodesk.import_report(db, write(tmp, "usage_report_20261001.csv", PORTAIL))
        res = api.run_report(db, "named", self.params())
        kpis = {k["label"]: k["value"] for k in res["kpis"]}
        self.assertEqual(kpis, {"Licences affectées": 5, "Utilisateurs": 4, "Actives": 2, "Inactives (≥ 30 j)": 2,
                                "Jamais utilisées": 1, "Coût annuel récupérable": 5400.0})
        self.assertEqual(res["rows"][0]["status_label"], "Jamais utilisée")
        self.assertEqual(res["chart"]["items"][0], {"label": "AutoCAD", "value": 1})
        self.assertEqual(any("il y a" in n for n in res["notes"]), (date.today() - date(2026, 10, 1)).days > 45)
        self.assertIn("named", api.REPORTS)
        csv = api.export_csv(res).decode("utf-8-sig")
        self.assertIn("Jamais utilisée;Revit;paul.martin@exemple.fr", csv)
        scoped = api.run_report(db, "named", self.params(scoped=True))
        self.assertEqual(scoped["rows"], [])

    def test_report_on_old_schema(self):
        db = Database("sqlite:///:memory:")
        res = api.run_report(db, "named", self.params())
        self.assertIn("init-db", res["notes"][0])


class ConfigAndCliTest(unittest.TestCase):
    def test_config(self):
        base = {"database": {"connection_string": "sqlite:///:memory:"},
                "servers": [{"name": "A", "port_at_host": "27000@a"}]}
        self.assertEqual(parse_config(base).autodesk.inactive_days, 30)
        cfg = parse_config({**base, "autodesk": {"import_dir": "C:/exports", "inactive_days": 60,
                                                  "annual_costs": {"AutoCAD*": "2300"}}})
        self.assertEqual((cfg.autodesk.import_dir, cfg.autodesk.inactive_days, cfg.autodesk.annual_costs),
                         ("C:/exports", 60, {"AutoCAD*": 2300.0}))
        for bad in ({"inactive_days": 0}, {"annual_costs": ["x"]}, {"annual_costs": {"x": "cher"}}, "oui"):
            with self.assertRaises(ConfigError):
                parse_config({**base, "autodesk": bad})

    def test_cli_import_and_collect(self):
        from heptagoneslm.cli import main

        tmp = Path(tempfile.mkdtemp())
        exports = tmp / "exports"
        exports.mkdir()
        db_file = tmp / "h.db"
        config = tmp / "h.yaml"
        config.write_text(
            f"database:\n  connection_string: sqlite:///{db_file.as_posix()}\n"
            "servers:\n  - name: A\n    port_at_host: 27000@hote-inexistant\n    collectors: [lmstat]\n"
            f"lmutil_path: {(tmp / 'absent-lmutil').as_posix()}\n"
            f"autodesk:\n  import_dir: {exports.as_posix()}\n", encoding="utf-8")
        self.assertEqual(main(["-c", str(config), "init-db"]), 0)
        export = write(tmp, "usage_report_20261001.csv", PORTAIL)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["-c", str(config), "import-autodesk", str(export)]), 0)
            self.assertEqual(main(["-c", str(config), "import-autodesk", str(export)]), 0)
        self.assertIn("5 licence(s) nommée(s), rapport du 2026-10-01", out.getvalue())
        self.assertIn("déjà importé", out.getvalue())
        self.assertEqual(main(["-c", str(config), "import-autodesk", str(tmp / "absent.csv")]), 2)

        write(exports, "usage_report_20261101.csv", PORTAIL.replace("85,2026", "86,2026"))
        out = io.StringIO()
        with redirect_stdout(out):
            main(["-c", str(config), "collect"])                  # serveur injoignable : l'import a lieu quand même
        self.assertIn("Autodesk : usage_report_20261101.csv importé 5 licence(s)", out.getvalue())
        with Database(f"sqlite:///{db_file.as_posix()}") as db:
            create_schema(db)
            self.assertEqual(autodesk.latest_report_date(db), date(2026, 11, 1))


if __name__ == "__main__":
    unittest.main()
