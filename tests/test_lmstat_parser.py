import unittest
from datetime import datetime

from freelm.collectors.flexlm.lmstat import parse_lmstat
from tests.helpers import FIXTURES


class LmstatParserTest(unittest.TestCase):
    def setUp(self):
        text = (FIXTURES / "lmstat_sample.txt").read_text(encoding="utf-8")
        self.status = parse_lmstat(text, "27000@licsrv1")
        self.features = {f.name: f for f in self.status.features}

    def test_header_and_server(self):
        self.assertEqual(self.status.queried_at, datetime(2024, 9, 23, 10, 12))
        self.assertTrue(self.status.server_up)
        self.assertEqual(self.status.server_version, "11.16.2")
        self.assertEqual(self.status.vendors, {"adskflex": "UP", "MLM": "UP"})

    def test_feature_totals(self):
        acad = self.features["87545ACD_2024_0F"]
        self.assertEqual((acad.issued, acad.in_use), (10, 3))
        self.assertEqual(acad.vendor, "adskflex")
        self.assertEqual(acad.version, "1.000")
        self.assertEqual((self.features["87048INVPROSA_2024_0F"].issued,
                          self.features["87048INVPROSA_2024_0F"].in_use), (1, 1))
        self.assertEqual(self.features["MATLAB"].in_use, 0)

    def test_uncounted_and_error(self):
        self.assertIsNone(self.features["Simulink"].issued)
        self.assertIsNone(self.features["Simulink"].error)
        self.assertIn("unsupported", self.features["OLD_FEATURE"].error)

    def test_checkouts(self):
        jdoe, asmith = self.features["87545ACD_2024_0F"].checkouts
        self.assertEqual((jdoe.user, jdoe.host, jdoe.display, jdoe.handle), ("jdoe", "PC123", "PC123", "1302"))
        self.assertEqual(jdoe.start_time, datetime(2024, 9, 23, 8, 15))
        self.assertEqual(jdoe.count, 1)
        self.assertEqual(asmith.count, 2)

    def test_user_with_space_and_queue(self):
        inv = self.features["87048INVPROSA_2024_0F"]
        self.assertEqual(len(inv.checkouts), 1)
        self.assertEqual(inv.checkouts[0].user, "Jean Dupont")
        self.assertEqual(inv.checkouts[0].host, "CAO-WS01")
        self.assertEqual(inv.checkouts[0].start_time, datetime(2024, 9, 22, 23, 40))
        self.assertEqual(inv.queued, 1)

    def test_year_rollover(self):
        text = (
            "Users of F:  (Total of 2 licenses issued;  Total of 1 license in use)\n"
            "    u h h (v1) (srv/27000 1), start Tue 12/31 17:00\n"
        )
        status = parse_lmstat(text, queried_at=datetime(2025, 1, 2, 9, 0))
        self.assertEqual(status.features[0].checkouts[0].start_time, datetime(2024, 12, 31, 17, 0))

    def test_server_down(self):
        text = (
            "License server status: 27000@nowhere\n"
            "lmgrd is not running: Cannot connect to license server system. (-15,570:10061 \"WinSock: Connection refused\")\n"
        )
        status = parse_lmstat(text)
        self.assertFalse(status.server_up)
        self.assertTrue(status.errors)


if __name__ == "__main__":
    unittest.main()
