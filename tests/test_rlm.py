import os
import stat
import sys
import tempfile
import unittest
from datetime import date, datetime

from heptagoneslm.collectors import get_collector_class
from heptagoneslm.collectors.rlm import RlmstatCollector, parse_rlmstat
from heptagoneslm.config import ConfigError, parse_config
from heptagoneslm.service import CollectionService
from tests.helpers import FIXTURES, make_db

REF = datetime(2026, 9, 23, 10, 0)


def rlm_config(**server):
    entry = {"name": "FOUNDRY", "port_at_host": "5053@licsrv3", "license_type": "rlm", **server}
    return parse_config({"database": {"connection_string": "sqlite:///:memory:"}, "servers": [entry]})


class RlmstatParserTest(unittest.TestCase):
    def setUp(self):
        self.status = parse_rlmstat((FIXTURES / "rlmstat_sample.txt").read_text(encoding="utf-8"),
                                    "5053@licsrv3", queried_at=REF)
        self.by_name = {f.name: f for f in self.status.features}

    def test_server_and_isv(self):
        self.assertTrue(self.status.server_up)
        self.assertEqual(self.status.server_version, "14.2")
        self.assertEqual(self.status.vendors, {"foundry": "UP", "maxon": "DOWN"})

    def test_pools_are_summed_by_license(self):
        self.assertEqual(list(self.by_name), ["nuke_i", "nukex_i", "nuke_r"])
        nuke = self.by_name["nuke_i"]
        self.assertEqual((nuke.issued, nuke.in_use), (12, 3))       # deux pools : 10 + 2
        self.assertEqual(nuke.expires, date(2026, 10, 15))          # échéance la plus proche
        self.assertEqual(nuke.vendor, "foundry")
        self.assertEqual((self.by_name["nukex_i"].issued, self.by_name["nukex_i"].in_use), (5, 5))
        self.assertIsNone(self.by_name["nukex_i"].expires)          # permanent
        self.assertIsNone(self.by_name["nuke_r"].issued)            # UNCOUNTED

    def test_checkouts(self):
        cos = self.by_name["nuke_i"].checkouts
        self.assertEqual([(c.user, c.host, c.count, c.handle) for c in cos],
                         [("jdoe", "PC123", 1, "41"), ("asmith", "PC456", 2, "81")])
        self.assertEqual(cos[0].start_time, datetime(2026, 9, 23, 8, 15))
        other = self.by_name["nukex_i"].checkouts
        self.assertEqual([c.user for c in other], ["j.martin", "bdurand"])
        self.assertEqual(other[0].start_time, datetime(2026, 9, 22, 17, 48))
        # somme des jetons empruntés = jetons utilisés annoncés par le pool
        for f in self.status.features:
            self.assertEqual(sum(c.count for c in f.checkouts), f.in_use)

    def test_server_down(self):
        status = parse_rlmstat("rlmutil v14.2\nError connecting to \"rlm\" server\nConnection refused\n",
                               "5053@nowhere", queried_at=REF)
        self.assertFalse(status.server_up)
        self.assertTrue(status.errors)


class RlmConfigTest(unittest.TestCase):
    def test_defaults_by_type(self):
        cfg = rlm_config()
        srv = cfg.servers[0]
        self.assertEqual((srv.collectors, srv.sessions_from), (["rlmstat"], "rlmstat"))
        self.assertEqual(cfg.rlmutil_path, "rlmutil")
        self.assertIs(get_collector_class("rlm", "rlmstat"), RlmstatCollector)

    def test_validation(self):
        with self.assertRaises(ConfigError):
            rlm_config(license_type="dsls")
        with self.assertRaises(ConfigError):
            rlm_config(collectors=["lmstat"], sessions_from="lmstat")

    def test_command(self):
        cfg = rlm_config(rlmutil_path="C:/RLM/rlmutil.exe")
        self.assertEqual(RlmstatCollector(cfg.servers[0], cfg).command(),
                         ["C:/RLM/rlmutil.exe", "rlmstat", "-a", "-c", "5053@licsrv3"])


class RlmProbeTest(unittest.TestCase):
    def test_wizard_probe(self):
        from heptagoneslm.setup.probes import test_lmstat
        from heptagoneslm.setup.system import SimulatedSystem

        r = test_lmstat(SimulatedSystem(delay=0), r"C:\RLM\rlmutil.exe", "5053@licsrv-rlm", "rlm")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["message"], "2 licences, 3 / 14 jetons utilisés")
        self.assertTrue(r["details"][0].startswith("RLM 14.2"))
        self.assertEqual(test_lmstat(SimulatedSystem(delay=0), "", "5053@x", "rlm")["message"],
                         "Chemin de rlmutil.exe non renseigné")


@unittest.skipIf(sys.platform == "win32", "faux rlmutil en script shell")
class RlmCollectionTest(unittest.TestCase):
    """Collecte complète avec un faux rlmutil : sessions, jetons et échéances en base."""

    def test_collect_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "rlmutil")
            with open(fake, "w", encoding="utf-8") as fh:
                fh.write(f"#!/bin/sh\ncat '{FIXTURES / 'rlmstat_sample.txt'}'\n")
            os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
            cfg = rlm_config(rlmutil_path=fake)
            db = make_db()
            results = CollectionService(cfg, db).collect_all()
        self.assertEqual([(r.collector, r.status, r.items) for r in results], [("rlmstat", "OK", 3)])
        self.assertEqual(db.scalar("SELECT license_type FROM license_server"), "rlm")
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM license_session WHERE checkin_time IS NULL"), 4)
        self.assertEqual(db.scalar("SELECT issued FROM usage_snapshot s JOIN feature f ON f.id = s.feature_id "
                                   "WHERE f.name = 'nuke_i'"), 12)
        self.assertEqual(str(db.scalar("SELECT expires_on FROM feature WHERE name = 'nuke_i'"))[:10], "2026-10-15")


if __name__ == "__main__":
    unittest.main()
