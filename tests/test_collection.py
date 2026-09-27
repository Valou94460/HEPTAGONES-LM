import unittest
from datetime import datetime, timedelta

from freelm.collectors.flexlm.logfile import FlexlmLogParser
from freelm.models import Checkout, FeatureStatus, ServerStatus
from freelm.service import CollectionService
from freelm.storage.schema import render_script
from tests.helpers import FIXTURES, make_config, make_db

T0 = datetime(2024, 9, 23, 10, 0)


def status_at(when, checkouts, issued=10, vendor_status="UP"):
    return ServerStatus(
        port_at_host="27000@licsrv1",
        queried_at=when,
        server_up=True,
        vendors={"adskflex": vendor_status},
        features=[
            FeatureStatus(
                name="87545ACD_2024_0F",
                issued=issued,
                in_use=sum(c.count for c in checkouts),
                vendor="adskflex",
                checkouts=checkouts,
            )
        ],
    )


def co(user, handle, start, count=1):
    return Checkout(feature="87545ACD_2024_0F", user=user, host=f"PC-{user}", handle=handle, start_time=start, count=count)


class LmstatSessionTest(unittest.TestCase):
    def setUp(self):
        self.cfg = make_config()
        self.db = make_db()
        self.svc = CollectionService(self.cfg, self.db)
        self.server_id = self.svc.sync_servers()["AUTODESK"]

    def apply(self, status):
        with self.db.transaction():
            self.svc.apply_status(self.server_id, status, "lmstat", track_sessions=True)

    def sessions(self):
        return self.db.query_dicts(
            "SELECT u.username, s.checkout_time, s.checkin_time, s.close_reason, s.license_count "
            "FROM license_session s JOIN app_user u ON u.id = s.user_id ORDER BY s.id"
        )

    def test_session_lifecycle(self):
        a = co("alice", "1", T0 - timedelta(minutes=30))
        b = co("bob", "2", T0 - timedelta(minutes=5), count=2)
        self.apply(status_at(T0, [a, b]))
        self.apply(status_at(T0 + timedelta(minutes=5), [a, b]))
        self.apply(status_at(T0 + timedelta(minutes=10), [a]))  # bob a rendu son jeton

        rows = self.sessions()
        self.assertEqual(len(rows), 2)
        alice, bob = rows
        self.assertIsNone(alice["checkin_time"])
        self.assertEqual(alice["checkout_time"], T0 - timedelta(minutes=30))
        # fin estimée au milieu de l'intervalle entre les deux sondages
        self.assertEqual(bob["checkin_time"], T0 + timedelta(minutes=7, seconds=30))
        self.assertEqual(bob["close_reason"], "not_seen")
        self.assertEqual(bob["license_count"], 2)

    def test_same_user_new_handle_is_new_session(self):
        self.apply(status_at(T0, [co("alice", "1", T0)]))
        self.apply(status_at(T0 + timedelta(minutes=5), [co("alice", "7", T0 + timedelta(minutes=4))]))
        rows = self.sessions()
        self.assertEqual(len(rows), 2)
        self.assertIsNotNone(rows[0]["checkin_time"])
        self.assertIsNone(rows[1]["checkin_time"])

    def test_vendor_down_keeps_sessions_open(self):
        self.apply(status_at(T0, [co("alice", "1", T0)]))
        down = status_at(T0 + timedelta(minutes=5), [])
        down.features = []
        down.vendors = {"adskflex": "The desired vendor daemon is down"}
        self.apply(down)
        self.assertIsNone(self.sessions()[0]["checkin_time"])

    def test_snapshots_and_hourly(self):
        self.apply(status_at(T0, [co("alice", "1", T0)]))
        self.apply(status_at(T0 + timedelta(minutes=5), [co("alice", "1", T0), co("bob", "2", T0)]))
        self.apply(status_at(T0 + timedelta(minutes=10), []))
        self.assertEqual(self.db.scalar("SELECT COUNT(*) FROM usage_snapshot"), 3)
        row = self.db.query_dicts("SELECT * FROM usage_hourly")[0]
        self.assertEqual((row["samples"], row["sum_in_use"], row["max_in_use"], row["min_in_use"], row["issued"]),
                         (3, 3, 2, 0, 10))
        product = self.db.scalar("SELECT product FROM feature")
        self.assertEqual(product, "AutoCAD 2024")

    def test_run_journal_on_error(self):
        # lmutil inexistant : l'échec est journalisé, sans exception
        self.cfg.lmutil_path = "/nonexistent/lmutil"
        results = self.svc.collect_all()
        self.assertEqual(results[0].status, "ERROR")
        self.assertEqual(self.db.scalar("SELECT status FROM collection_run"), "ERROR")


class LogSessionTest(unittest.TestCase):
    def setUp(self):
        self.cfg = make_config(collectors=["lmstat", "log"], sessions_from="log", log_path="x.log")
        self.db = make_db()
        self.svc = CollectionService(self.cfg, self.db)
        self.server_id = self.svc.sync_servers()["AUTODESK"]
        with open(FIXTURES / "flexlm_debug.log", encoding="utf-8") as fh:
            self.events = FlexlmLogParser().parse_lines(fh)

    def test_sessions_and_events_from_log(self):
        # application en deux lots pour vérifier la reprise des sessions ouvertes
        with self.db.transaction():
            self.svc.apply_events(self.server_id, self.events[:3], "log", True)
        with self.db.transaction():
            self.svc.apply_events(self.server_id, self.events[3:], "log", True)

        rows = self.db.query_dicts(
            "SELECT f.name, u.username, s.checkout_time, s.checkin_time, s.close_reason, s.license_count "
            "FROM license_session s JOIN feature f ON f.id = s.feature_id "
            "JOIN app_user u ON u.id = s.user_id ORDER BY s.checkout_time"
        )
        self.assertEqual(len(rows), 4)
        jdoe_acad, jdoe_matlab, jean, asmith = rows
        self.assertEqual(jdoe_acad["checkin_time"], datetime(2024, 9, 23, 9, 12, 5))
        self.assertEqual((jdoe_matlab["close_reason"], jdoe_matlab["license_count"]), ("inactive", 2))
        self.assertEqual(jean["username"], "jean dupont")
        self.assertEqual(jean["checkin_time"], datetime(2024, 9, 24, 0, 10))
        self.assertEqual((asmith["checkin_time"], asmith["close_reason"]),
                         (datetime(2024, 9, 24, 18, 0), "server_down"))

        # reprise d'historique plus ancien : first_seen recule (sinon exclu des rapports)
        with self.db.transaction():
            self.svc.apply_events(self.server_id, [self.events[0].__class__(
                event_time=datetime(2024, 1, 5, 9, 0), event_type=self.events[0].event_type,
                feature="87545ACD_2024_0F", user="old", host="pc", vendor="adskflex")], "log", True)
        self.assertEqual(self.db.scalar("SELECT first_seen FROM feature WHERE name = '87545ACD_2024_0F'"),
                         datetime(2024, 1, 5, 9, 0))

        counts = dict(self.db.query("SELECT event_type, COUNT(*) FROM license_event GROUP BY event_type"))
        self.assertEqual(counts, {"DENIED": 2, "QUEUED": 1, "UNSUPPORTED": 1, "SERVER_DOWN": 1})


class SchemaTest(unittest.TestCase):
    def test_mssql_script_renders(self):
        script = render_script("mssql")
        self.assertIn("IDENTITY(1,1)", script)
        self.assertIn("CREATE OR ALTER VIEW dbo.v_session_detail", script)
        self.assertNotIn("{", script.replace("{d}", ""))

    def test_views_query(self):
        db = make_db()
        self.assertEqual(db.query("SELECT * FROM v_session_detail"), [])
        self.assertEqual(db.query("SELECT * FROM v_usage_hourly"), [])
        self.assertEqual(db.query("SELECT * FROM v_denial"), [])


if __name__ == "__main__":
    unittest.main()
