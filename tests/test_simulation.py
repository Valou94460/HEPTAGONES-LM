import contextlib
import importlib.util
import io
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta

from freelm.collectors.flexlm.lmstat import parse_lmstat
from freelm.collectors.flexlm.logfile import FlexlmLogParser
from freelm.models import EventType
from freelm.simulation import fake_lmutil
from freelm.simulation.demo import run_demo
from freelm.simulation.model import DEFAULT_SCENARIO, day_activity, log_lines, render_lmstat
from freelm.storage import Database

HAS_OPENPYXL = importlib.util.find_spec("openpyxl") is not None
MONDAY = datetime(2024, 9, 23)


class SimulationModelTest(unittest.TestCase):
    def test_deterministic(self):
        srv = DEFAULT_SCENARIO[0]
        t = MONDAY.replace(hour=10)
        self.assertEqual(render_lmstat(srv, t), render_lmstat(srv, t))

    def test_capacity_never_exceeded_and_parsable(self):
        for srv in DEFAULT_SCENARIO:
            t = MONDAY
            while t < MONDAY + timedelta(days=7):
                status = parse_lmstat(render_lmstat(srv, t), srv.port_at_host, queried_at=t)
                self.assertTrue(status.server_up)
                for feat in status.features:
                    self.assertLessEqual(feat.in_use, feat.issued)
                    self.assertEqual(feat.in_use, sum(c.count for c in feat.checkouts))
                t += timedelta(minutes=30)

    def test_weekday_busier_than_weekend(self):
        srv = DEFAULT_SCENARIO[0]
        weekday = sum(len(day_activity(srv, f, date(2024, 9, 23))[0]) for f in srv.features)
        sunday = sum(len(day_activity(srv, f, date(2024, 9, 29))[0]) for f in srv.features)
        self.assertGreater(weekday, sunday)

    def test_log_matches_model(self):
        ansys = next(s for s in DEFAULT_SCENARIO if s.with_log)
        start = MONDAY - timedelta(seconds=1)
        lines = [" 0:00:00 (lmgrd) TIMESTAMP 9/22/2024"] + log_lines(ansys, start, MONDAY + timedelta(days=2))
        events = FlexlmLogParser().parse_lines(lines)
        expected = sum(len(day_activity(ansys, f, MONDAY.date() + timedelta(days=d))[0])
                       for f in ansys.features for d in range(2))
        self.assertEqual(sum(e.event_type == EventType.OUT for e in events), expected)
        self.assertEqual(sum(e.event_type == EventType.IN for e in events), expected)

    def test_fake_lmutil(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(fake_lmutil.main(["lmstat", "-a", "-c", "27000@licsrv-mlm"]), 0)
        self.assertIn("Users of MATLAB:", out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(fake_lmutil.main(["lmstat", "-a", "-c", "1@nowhere"]), 1)


class DemoRunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.end = MONDAY + timedelta(days=2)  # lundi -> mercredi 00:00
        cls.result = run_demo(cls.tmp.name, days=2, end=cls.end, make_report=HAS_OPENPYXL)
        cls.db = Database(f"sqlite:///{os.path.join(cls.tmp.name, 'freelm_demo.db')}")

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.tmp.cleanup()

    def model_sessions(self, srv):
        return sum(len(day_activity(srv, f, MONDAY.date() + timedelta(days=d))[0])
                   for f in srv.features for d in range(2))

    def test_sessions_match_model(self):
        for srv in DEFAULT_SCENARIO:
            source = "log" if srv.with_log else "lmstat"
            n = self.db.scalar(
                "SELECT COUNT(*) FROM license_session s JOIN feature f ON f.id = s.feature_id "
                "JOIN license_server v ON v.id = f.server_id WHERE v.name = ? AND s.source = ?",
                [srv.name, source],
            )
            self.assertEqual(n, self.model_sessions(srv), srv.name)
        # toutes les sessions sont fermées à 23h30 : aucune ne reste ouverte
        self.assertEqual(self.db.scalar("SELECT COUNT(*) FROM license_session WHERE checkin_time IS NULL"), 0)

    def test_log_sessions_exact_durations(self):
        ansys = next(s for s in DEFAULT_SCENARIO if s.with_log)
        expected = sum((s.end - s.start).total_seconds() * s.count
                       for f in ansys.features for d in range(2)
                       for s in day_activity(ansys, f, MONDAY.date() + timedelta(days=d))[0])
        summaries = [s for s in self.result.data.summaries if s.server == "ANSYS"]
        self.assertAlmostEqual(sum(s.usage_hours for s in summaries) * 3600, expected, delta=1)

    def test_live_config_written(self):
        from freelm.config import load_config

        cfg = load_config(self.result.config_path)
        self.assertEqual([s.name for s in cfg.servers], ["AUTODESK", "ANSYS", "MATLAB"])
        self.assertTrue(os.path.exists(cfg.lmutil_path))

    @unittest.skipUnless(HAS_OPENPYXL, "openpyxl non installé")
    def test_report_generated(self):
        self.assertTrue(self.result.report_path.exists())


if __name__ == "__main__":
    unittest.main()
