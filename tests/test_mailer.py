import email
import importlib.util
import socketserver
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta
from email import policy

from heptagoneslm.config import ConfigError, Subscription, load_config, parse_config
from heptagoneslm.mailer import deliver, period_label
from heptagoneslm.simulation.demo import run_demo
from heptagoneslm.storage import Database

HAS_OPENPYXL = importlib.util.find_spec("openpyxl") is not None
MONDAY = datetime(2024, 9, 23)


class FakeSMTPHandler(socketserver.StreamRequestHandler):
    """Serveur SMTP minimal : enregistre les messages reçus."""

    def handle(self):
        self.wfile.write(b"220 fake ESMTP\r\n")
        rcpt, buf, in_data = [], [], False
        while True:
            line = self.rfile.readline()
            if not line:
                break
            if in_data:
                if line in (b".\r\n", b".\n"):
                    in_data = False
                    self.server.messages.append((rcpt, b"".join(buf)))
                    rcpt, buf = [], []
                    self.wfile.write(b"250 OK\r\n")
                else:
                    buf.append(line[1:] if line.startswith(b"..") else line)
                continue
            cmd = line.strip().upper()
            if cmd.startswith(b"EHLO"):
                self.wfile.write(b"250-fake\r\n250-8BITMIME\r\n250 SMTPUTF8\r\n")
            elif cmd.startswith(b"RCPT"):
                rcpt.append(line.strip().split(b":", 1)[1].decode())
                self.wfile.write(b"250 OK\r\n")
            elif cmd == b"DATA":
                in_data = True
                self.wfile.write(b"354 go\r\n")
            elif cmd == b"QUIT":
                self.wfile.write(b"221 bye\r\n")
                break
            else:
                self.wfile.write(b"250 OK\r\n")


class LabelTest(unittest.TestCase):
    def test_period_label(self):
        self.assertEqual(period_label(datetime(2026, 9, 1), datetime(2026, 10, 1)), "septembre 2026")
        self.assertEqual(period_label(datetime(2026, 12, 1), datetime(2027, 1, 1)), "décembre 2026")
        self.assertEqual(period_label(datetime(2026, 9, 21), datetime(2026, 9, 28)), "du 21/09/2026 au 27/09/2026")

    def test_config_validation(self):
        base = {"database": {"connection_string": "sqlite:///:memory:"}, "servers": []}
        for bad in ({"subscriptions": [{"name": "A", "recipients": ["a@b"]}]},                      # pas de smtp_host
                    {"smtp_host": "s", "subscriptions": [{"name": "A", "recipients": []}]},
                    {"smtp_host": "s", "subscriptions": [{"name": "A", "recipients": ["a@b"], "period": "hier"}]},
                    {"smtp_host": "s", "security": "tls13"}):
            with self.assertRaises(ConfigError, msg=bad):
                parse_config({**base, "email": bad})


class DeliveryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        # Octobre 2024 simulé ; rapport du « mois précédent » vu depuis le 1er novembre.
        demo = run_demo(cls.tmp.name, days=31, end=datetime(2024, 11, 1), make_report=False)
        cls.smtp = socketserver.ThreadingTCPServer(("127.0.0.1", 0), FakeSMTPHandler)
        cls.smtp.messages = []
        threading.Thread(target=cls.smtp.serve_forever, daemon=True).start()
        raw = load_config(demo.config_path)
        cls.cfg = parse_config({
            "database": {"connection_string": raw.database.connection_string},
            "servers": [{"name": s.name, "port_at_host": s.port_at_host} for s in raw.servers],
            "web": {"public_url": "https://heptagoneslm.test.local:8443"},
            "email": {
                "smtp_host": "127.0.0.1", "smtp_port": cls.smtp.server_address[1], "security": "none",
                "sender": "HeptagonesLM <heptagoneslm@test.local>",
                "subscriptions": [
                    {"name": "Direction", "recipients": ["dsi@test.local", "achats@test.local"],
                     "attach_excel": HAS_OPENPYXL},
                    {"name": "Simulation", "recipients": ["simu@test.local"], "servers": ["ANSYS"],
                     "attach_excel": False},
                ],
            },
        })
        cls.db = Database(raw.database.connection_string)
        cls.today = date(2024, 11, 1)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.smtp.shutdown()
        cls.smtp.server_close()
        cls.tmp.cleanup()

    def setUp(self):
        self.smtp.messages.clear()
        with self.db.transaction():
            self.db.execute("DELETE FROM report_delivery")

    def sub(self, name):
        return next(s for s in self.cfg.email.subscriptions if s.name == name)

    def test_send_and_content(self):
        r = deliver(self.cfg, self.db, self.sub("Direction"), today=self.today)
        self.assertEqual(r.status, "SENT", r.detail)
        rcpt, raw = self.smtp.messages[0]
        self.assertEqual(sorted(rcpt), ["<achats@test.local>", "<dsi@test.local>"])
        msg = email.message_from_bytes(raw, policy=policy.default)
        self.assertEqual(msg["Subject"], "HeptagonesLM — Utilisation des licences octobre 2024 — Direction")
        html = msg.get_body(("html",)).get_content()
        self.assertIn("Utilisation des licences — octobre 2024", html)
        self.assertIn("AutoCAD 2024", html)
        self.assertIn("https://heptagoneslm.test.local:8443/#type=summary", html)
        self.assertIn("start=2024-10-01", html)
        self.assertIn("Heures saturées", msg.get_body(("plain",)).get_content())
        attachments = [p.get_filename() for p in msg.iter_attachments()]
        self.assertEqual(attachments, ["HeptagonesLM_Direction_20241001_20241031.xlsx"] if HAS_OPENPYXL else [])

    def test_filtered_subscription(self):
        deliver(self.cfg, self.db, self.sub("Simulation"), today=self.today)
        msg = email.message_from_bytes(self.smtp.messages[0][1], policy=policy.default)
        html = msg.get_body(("html",)).get_content()
        self.assertIn("Ansys", html)
        self.assertNotIn("AutoCAD", html)
        self.assertIn("serveurs : ANSYS", html)

    def test_skip_sent_once_per_period(self):
        first = deliver(self.cfg, self.db, self.sub("Simulation"), today=self.today, skip_sent=True)
        again = deliver(self.cfg, self.db, self.sub("Simulation"), today=self.today + timedelta(days=1), skip_sent=True)
        self.assertEqual((first.status, again.status), ("SENT", "SKIPPED"))
        self.assertEqual(len(self.smtp.messages), 1)
        # le mois suivant, nouvel envoi
        nxt = deliver(self.cfg, self.db, self.sub("Simulation"), today=date(2024, 12, 2), skip_sent=True)
        self.assertEqual(nxt.status, "SENT")

    def test_dry_run_writes_eml_without_sending(self):
        with tempfile.TemporaryDirectory() as out:
            r = deliver(self.cfg, self.db, self.sub("Simulation"), today=self.today, dry_run_dir=out)
            self.assertEqual(r.status, "DRY-RUN")
            self.assertTrue(r.path.read_bytes().startswith(b"Subject:") or b"Subject:" in r.path.read_bytes())
        self.assertEqual(self.smtp.messages, [])
        self.assertEqual(self.db.scalar("SELECT COUNT(*) FROM report_delivery"), 0)

    def test_smtp_error_recorded(self):
        def broken(msg, cfg):
            raise ConnectionRefusedError("SMTP injoignable")

        r = deliver(self.cfg, self.db, self.sub("Simulation"), today=self.today, sender=broken)
        self.assertEqual(r.status, "ERROR")
        self.assertEqual(self.db.query("SELECT status, message FROM report_delivery"), [("ERROR", "SMTP injoignable")])
        # un échec n'empêche pas l'envoi suivant avec --skip-sent
        self.assertEqual(deliver(self.cfg, self.db, self.sub("Simulation"), today=self.today, skip_sent=True).status, "SENT")

    def test_override_recipients(self):
        deliver(self.cfg, self.db, self.sub("Direction"), today=self.today, override_to=["moi@test.local"])
        self.assertEqual(self.smtp.messages[0][0], ["<moi@test.local>"])

    def test_adhoc_subscription(self):
        r = deliver(self.cfg, self.db, Subscription(name="Test", recipients=["x@test.local"], attach_excel=False),
                    today=self.today, period="last-week")
        self.assertEqual(r.status, "SENT")


if __name__ == "__main__":
    unittest.main()
