import email
import tempfile
import unittest
from datetime import date, datetime, timedelta
from email import policy

from heptagoneslm.alerts import check_and_notify, evaluate
from heptagoneslm.config import ConfigError, parse_config
from heptagoneslm.models import EventType, LicenseEvent
from heptagoneslm.service import CollectionService
from heptagoneslm.storage.repository import Repository
from tests.helpers import make_db
from tests.test_collection import co, status_at

NOW = datetime(2026, 9, 29, 10, 0)


def alert_config(**alerts):
    raw_alerts = {"recipients": ["dsi@entreprise.local"], **alerts}
    return parse_config({
        "database": {"connection_string": "sqlite:///:memory:"},
        "servers": [{"name": "AUTODESK", "port_at_host": "27000@licsrv1"},
                    {"name": "ANSYS", "port_at_host": "1055@licsrv2"}],
        "products": [{"pattern": "87545ACD_2024_*", "product": "AutoCAD 2024"}],
        "web": {"public_url": "https://heptagoneslm.entreprise.local"},
        "email": {"smtp_host": "smtp.entreprise.local", "sender": "HeptagonesLM <h@entreprise.local>",
                  "alerts": raw_alerts},
    })


class Outbox:
    def __init__(self, fail=False):
        self.messages, self.fail = [], fail

    def __call__(self, msg, cfg):
        if self.fail:
            raise OSError("SMTP injoignable")
        self.messages.append(email.message_from_bytes(bytes(msg), policy=policy.default))

    def last_text(self):
        return self.messages[-1].get_body(("plain",)).get_content()


class AlertConfigTest(unittest.TestCase):
    def test_defaults(self):
        cfg = alert_config().email.alerts
        self.assertEqual(cfg.types, ["collection", "denial", "saturation", "expiry"])
        self.assertEqual(cfg.repeat_hours, 24)

    def test_validation(self):
        with self.assertRaises(ConfigError):
            alert_config(recipients=[])
        with self.assertRaises(ConfigError):
            alert_config(types=["collection", "meteo"])
        with self.assertRaises(ConfigError):
            alert_config(repeat_hours=0)
        with self.assertRaises(ConfigError):   # alertes sans serveur SMTP
            parse_config({"database": {"connection_string": "sqlite:///:memory:"}, "servers": [],
                          "email": {"alerts": {"recipients": ["a@b.fr"]}}})

    def test_absent_is_disabled(self):
        cfg = parse_config({"database": {"connection_string": "sqlite:///:memory:"}, "servers": []})
        self.assertIsNone(cfg.email.alerts)
        self.assertEqual(check_and_notify(cfg, make_db()).status, "DISABLED")


class AlertTest(unittest.TestCase):
    def setUp(self):
        self.cfg = alert_config()
        self.db = make_db()
        self.svc = CollectionService(self.cfg, self.db)
        self.ids = self.svc.sync_servers()
        self.repo = Repository(self.db)
        self.outbox = Outbox()

    def run_(self, when, status):
        with self.db.transaction():
            rid = self.repo.start_run(self.ids["AUTODESK"], "lmstat", when)
            self.repo.finish_run(rid, status, 0, "Serveur 27000@licsrv1 injoignable" if status == "ERROR" else None)
            rid = self.repo.start_run(self.ids["ANSYS"], "lmstat", when)
            self.repo.finish_run(rid, "OK", 0, None)

    def check(self, when):
        return check_and_notify(self.cfg, self.db, now=when, sender=self.outbox)

    def check_ok(self, when):
        """Collecte normale puis évaluation (évite l'alerte « collecte arrêtée »)."""
        self.run_(when, "OK")
        return self.check(when)

    def test_collection_failure_then_recovery(self):
        self.run_(NOW, "ERROR")
        r = self.check(NOW)
        self.assertEqual(r.status, "SENT")
        self.assertEqual([a.title for a in r.alerts], ["Collecte en échec : AUTODESK"])
        msg = self.outbox.messages[-1]
        self.assertEqual(msg["To"], "dsi@entreprise.local")
        self.assertEqual(msg["Importance"], "High")
        self.assertIn("injoignable", self.outbox.last_text())
        self.assertIn("https://heptagoneslm.entreprise.local/", self.outbox.last_text())

        # toujours en échec 5 min plus tard : pas de nouveau message
        self.run_(NOW + timedelta(minutes=5), "ERROR")
        self.assertEqual(self.check(NOW + timedelta(minutes=5)).status, "NONE")
        # rappel après repeat_hours
        later = NOW + timedelta(hours=25)
        self.run_(later, "ERROR")
        self.assertEqual(self.check(later).status, "SENT")
        # rétablissement : message « rétabli », une seule fois
        ok = later + timedelta(minutes=5)
        self.run_(ok, "OK")
        r = self.check(ok)
        self.assertEqual([a.severity for a in r.alerts], ["resolved"])
        self.assertEqual(r.alerts[0].title, "Collecte rétablie : AUTODESK")
        self.assertEqual(self.check(ok + timedelta(minutes=5)).status, "NONE")
        self.assertEqual(len(self.outbox.messages), 3)
        self.assertEqual(self.db.scalar("SELECT COUNT(*) FROM report_delivery WHERE subscription = 'Alertes'"), 3)

    def test_stale_collection(self):
        self.run_(NOW - timedelta(hours=3), "OK")
        r = self.check(NOW)
        self.assertEqual([a.title for a in r.alerts], ["Collecte arrêtée"])
        self.assertIn("3 h", r.alerts[0].detail)

    def test_smtp_failure_keeps_alert_for_next_run(self):
        self.run_(NOW, "ERROR")
        self.outbox.fail = True
        self.assertEqual(self.check(NOW).status, "ERROR")
        self.outbox.fail = False
        self.assertEqual(self.check(NOW + timedelta(minutes=5)).status, "SENT")
        self.assertEqual(self.db.scalar("SELECT status FROM report_delivery ORDER BY id"), "ERROR")

    def add_denials(self, when, users):
        events = [LicenseEvent(when + timedelta(seconds=i), EventType.DENIED, "87545ACD_2024_0F", u, f"PC-{u}",
                               "adskflex", detail="Licensed number of users already reached.")
                  for i, u in enumerate(users)]
        with self.db.transaction():
            self.svc.apply_events(self.ids["AUTODESK"], events, "log", track_sessions=False)

    def test_denials_are_sent_once_then_grouped(self):
        self.run_(NOW, "OK")
        self.add_denials(NOW - timedelta(minutes=3), ["jdoe", "jdoe", "asmith"])
        r = self.check(NOW)
        self.assertEqual([a.title for a in r.alerts], ["3 refus de licence : AutoCAD 2024"])
        self.assertIn("jdoe (2), asmith", r.alerts[0].detail)
        # rien de nouveau
        self.assertEqual(self.check(NOW + timedelta(minutes=5)).status, "NONE")
        # nouveaux refus pendant le délai de répétition : mis en attente puis regroupés
        self.add_denials(NOW + timedelta(minutes=10), ["bmartin"])
        self.assertEqual(self.check(NOW + timedelta(minutes=15)).status, "NONE")
        self.add_denials(NOW + timedelta(hours=2), ["cdurand"])
        r = self.check_ok(NOW + timedelta(hours=25))
        self.assertEqual([a.title for a in r.alerts], ["2 refus de licence : AutoCAD 2024"])

    def test_old_denials_ignored_on_first_activation(self):
        self.run_(NOW, "OK")
        self.add_denials(NOW - timedelta(hours=5), ["jdoe"])
        self.assertEqual(self.check(NOW).status, "NONE")

    def test_saturation(self):
        self.run_(NOW, "OK")
        full = status_at(NOW - timedelta(minutes=2), [co("u1", "1", NOW - timedelta(hours=1)),
                                                     co("u2", "2", NOW - timedelta(hours=1))], issued=2)
        with self.db.transaction():
            self.svc.apply_status(self.ids["AUTODESK"], full, "lmstat", track_sessions=True)
        r = self.check(NOW)
        self.assertEqual([a.title for a in r.alerts], ["Plus aucun jeton disponible : AutoCAD 2024 (87545ACD_2024_0F)"])
        self.assertIn("2/2", r.alerts[0].detail)
        self.assertEqual(self.check(NOW + timedelta(minutes=5)).status, "NONE")
        # jeton libéré puis nouvelle saturation : l'alerte est réarmée
        free = status_at(NOW + timedelta(minutes=8), [co("u1", "1", NOW - timedelta(hours=1))], issued=2)
        with self.db.transaction():
            self.svc.apply_status(self.ids["AUTODESK"], free, "lmstat", track_sessions=True)
        self.assertEqual(self.check(NOW + timedelta(minutes=10)).status, "NONE")
        again = status_at(NOW + timedelta(minutes=13), [co("u1", "1", NOW - timedelta(hours=1)),
                                                       co("u3", "3", NOW + timedelta(minutes=12))], issued=2)
        with self.db.transaction():
            self.svc.apply_status(self.ids["AUTODESK"], again, "lmstat", track_sessions=True)
        self.assertEqual(self.check(NOW + timedelta(minutes=15)).status, "SENT")

    def set_expiry(self, days):
        fid = self.svc._feature_id(self.ids["AUTODESK"], "87545ACD_2024_0F", NOW)
        with self.db.transaction():
            self.repo.set_feature_expiry(fid, date(2026, 9, 29) + timedelta(days=days))

    def test_expiry_stages_sent_once_each(self):
        self.run_(NOW, "OK")
        self.set_expiry(20)
        r = self.check(NOW)
        self.assertEqual([a.title for a in r.alerts], ["Expiration dans 20 j : AutoCAD 2024"])
        self.assertEqual(r.alerts[0].severity, "warning")
        self.assertEqual(self.check_ok(NOW + timedelta(hours=30)).status, "NONE")   # pas de rappel
        self.set_expiry(5)
        r = self.check_ok(NOW + timedelta(hours=31))
        self.assertEqual(r.alerts[0].severity, "critical")
        self.set_expiry(-1)
        self.assertEqual(self.check_ok(NOW + timedelta(hours=32)).alerts[0].title, "Licence expirée : AutoCAD 2024")

    def test_filters_and_types(self):
        self.cfg = alert_config(types=["expiry"], servers=["ANSYS"])
        self.run_(NOW, "ERROR")
        self.set_expiry(3)
        to_send, resolved, _ = evaluate(self.cfg, self.db, NOW)
        self.assertEqual((to_send, resolved), ([], []))

    def test_dry_run_writes_eml_without_state(self):
        self.run_(NOW, "ERROR")
        with tempfile.TemporaryDirectory() as tmp:
            r = check_and_notify(self.cfg, self.db, now=NOW, dry_run_dir=tmp, sender=self.outbox)
            self.assertEqual(r.status, "DRY-RUN")
            self.assertTrue(r.path.exists())
        self.assertEqual(self.outbox.messages, [])
        self.assertEqual(self.db.scalar("SELECT COUNT(*) FROM alert_state"), 0)
        self.assertEqual(self.check(NOW).status, "SENT")


class CliTest(unittest.TestCase):
    def test_collect_then_check_alerts(self):
        import contextlib
        import io
        import os

        import yaml

        from heptagoneslm.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            cfg = {
                "database": {"connection_string": f"sqlite:///{tmp}/h.db"},
                "lmutil_path": os.path.join(tmp, "absent-lmutil"),
                "servers": [{"name": "AUTODESK", "port_at_host": "27000@licsrv1"}],
                "email": {"smtp_host": "127.0.0.1", "smtp_port": 1, "sender": "h@entreprise.local",
                          "timeout_s": 2, "alerts": {"recipients": ["dsi@entreprise.local"]}},
            }
            path = os.path.join(tmp, "h.yaml")
            with open(path, "w", encoding="utf-8") as fh:
                yaml.safe_dump(cfg, fh)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["-c", path, "init-db"]), 0)
                # lmutil absent : collecte en échec ; SMTP injoignable : alerte journalisée en erreur
                self.assertEqual(main(["-c", path, "collect"]), 2)
                self.assertEqual(main(["-c", path, "check-alerts", "--dry-run", tmp]), 0)
            self.assertIn("Alertes : ERROR", out.getvalue())
            self.assertIn("Collecte en échec : AUTODESK", out.getvalue())
            self.assertTrue([f for f in os.listdir(tmp) if f.startswith("alertes_")])


if __name__ == "__main__":
    unittest.main()
