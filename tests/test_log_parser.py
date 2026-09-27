import os
import shutil
import tempfile
import unittest
from datetime import datetime

from heptagoneslm.collectors.flexlm.logfile import FlexlmLogParser, read_log_incremental
from heptagoneslm.models import EventType
from tests.helpers import FIXTURES


class LogParserTest(unittest.TestCase):
    def setUp(self):
        with open(FIXTURES / "flexlm_debug.log", encoding="utf-8") as fh:
            self.events = FlexlmLogParser().parse_lines(fh)

    def test_event_types(self):
        types = [e.event_type for e in self.events]
        self.assertEqual(types.count(EventType.OUT), 4)
        self.assertEqual(types.count(EventType.IN), 3)
        self.assertEqual(types.count(EventType.DENIED), 2)
        self.assertEqual(types.count(EventType.QUEUED), 1)
        self.assertEqual(types.count(EventType.UNSUPPORTED), 1)
        self.assertEqual(types[-1], EventType.SERVER_DOWN)

    def test_fields(self):
        first = self.events[0]
        self.assertEqual(first.event_time, datetime(2024, 9, 23, 8, 15, 40))
        self.assertEqual((first.feature, first.user, first.host, first.vendor),
                         ("87545ACD_2024_0F", "jdoe", "PC123", "adskflex"))
        matlab_out = self.events[1]
        self.assertEqual(matlab_out.count, 2)
        denied = next(e for e in self.events if e.event_type == EventType.DENIED)
        self.assertIn("Licensed number of users", denied.detail)

    def test_midnight_rollover_and_timestamp(self):
        jean = [e for e in self.events if e.user == "Jean Dupont"]
        self.assertEqual(jean[0].event_time, datetime(2024, 9, 23, 23, 50))
        self.assertEqual(jean[1].event_time, datetime(2024, 9, 24, 0, 10))
        self.assertEqual(self.events[-2].event_time, datetime(2024, 9, 24, 7, 30))
        self.assertEqual(self.events[-1].vendor, "adskflex")

    def test_undated_lines_skipped(self):
        parser = FlexlmLogParser()
        self.assertIsNone(parser.parse_line(' 8:15:40 (adskflex) OUT: "F" u@h'))
        self.assertEqual(parser.skipped_undated, 1)

    def test_dated_prefix(self):
        e = FlexlmLogParser().parse_line('9/23/2024 8:15:40 (adskflex) OUT: "F" u@h')
        self.assertEqual(e.event_time, datetime(2024, 9, 23, 8, 15, 40))


class IncrementalReadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "debug.log")
        with open(FIXTURES / "flexlm_debug.log", encoding="utf-8") as fh:
            self.lines = fh.readlines()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _write(self, lines, mode="w"):
        with open(self.path, mode, encoding="utf-8", newline="") as fh:
            fh.writelines(lines)

    def test_incremental_and_partial_line(self):
        self._write(self.lines[:8])
        ev1, state = read_log_incremental(self.path, {})
        self.assertEqual(len(ev1), 3)  # OUT, OUT, IN
        # ligne en cours d'écriture (sans saut de ligne) : non consommée
        self._write([self.lines[8].rstrip("\n")], mode="a")
        ev2, state = read_log_incremental(self.path, state)
        self.assertEqual(ev2, [])
        self._write(["\n"] + self.lines[9:], mode="a")
        ev3, state = read_log_incremental(self.path, state)
        total = len(FlexlmLogParser().parse_lines(self.lines))
        self.assertEqual(len(ev1) + len(ev3), total)
        # aucune relecture
        ev4, _ = read_log_incremental(self.path, state)
        self.assertEqual(ev4, [])

    def test_rotation_detected(self):
        self._write(self.lines)
        _, state = read_log_incremental(self.path, {})
        # nouveau fichier plus court (rotation / redémarrage de lmgrd)
        self._write([" 8:00:01 (lmgrd) TIMESTAMP 9/25/2024\n", ' 8:10:00 (adskflex) OUT: "X" a@b\n'])
        events, state = read_log_incremental(self.path, state)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_time, datetime(2024, 9, 25, 8, 10))


if __name__ == "__main__":
    unittest.main()
