"""26.3.3 : fermeture de l'assistant (bouton « Quitter », arrêt automatique) et fenêtre console masquée."""

import json
import threading
import unittest
import urllib.error
import urllib.request

import yaml

from heptagoneslm import launcher
from heptagoneslm.config import parse_config
from heptagoneslm.setup.answers import Answers
from heptagoneslm.setup.installer import ANSWERS_FILE
from heptagoneslm.setup.server import SetupApp, make_server
from heptagoneslm.setup.system import SetupError
from tests.test_setup import answers, ready_system
from tests.test_update import customize, install


class QuitTest(unittest.TestCase):
    def setUp(self):
        self.httpd, self.app = make_server(ready_system(), 0, token="jeton")
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def req(self, path, body=None):
        h = {"Cookie": "heptagoneslm_setup=jeton"}
        if body is not None:
            h.update({"Content-Type": "application/json", "X-HeptagonesLM-Setup": "1"})
        data = json.dumps(body).encode() if body is not None else None
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base + path, data=data, headers=h)) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_ping_keeps_wizard_alive(self):
        self.app.last_seen = 0
        self.assertEqual(self.req("/api/ping"), (200, {"ok": True}))
        self.assertGreater(self.app.last_seen, 0)

    def test_quit_refused_while_installing(self):
        class Running:
            status = "running"
        self.app.installer = Running()
        code, body = self.req("/api/shutdown", {})
        self.assertEqual(code, 409)
        self.assertTrue(body["busy"])
        self.app.installer = None
        self.assertEqual(self.req("/api/shutdown", {}), (200, {"stopped": True}))
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())                   # serveur de l'assistant arrêté

    def test_busy(self):
        self.assertFalse(self.app.busy())


class ConsoleTest(unittest.TestCase):
    def test_hide_console_is_windows_only(self):
        import os
        if os.name != "nt":
            self.assertFalse(launcher.hide_own_console())
            launcher.show_console()                                    # sans effet, sans erreur



class AutodeskOnExistingTest(unittest.TestCase):
    """Licences Autodesk ajoutées à une installation existante, sans réinstaller ni ressaisir de mot de passe."""

    def setUp(self):
        self.s, self.a = ready_system(), answers()
        install(self.s, self.a)
        customize(self.s, self.a)                                    # products, sites… saisis à la main
        self.app = SetupApp(self.s, token="t")

    def configure(self, **fields):
        self.app.start_autodesk_config(Answers(**fields))
        self.app.installer.wait(30)
        st = self.app.installer.state()
        self.assertEqual(st["status"], "done", st["error"])
        return st

    def test_context_has_no_secret(self):
        ctx = self.app.autodesk_context()
        self.assertEqual(ctx["answers"]["install_dir"], self.a.install_dir)
        self.assertNotIn("service_password", ctx["answers"])

    def test_add_portal_exports_and_api(self):
        calls = len(self.s.calls)
        st = self.configure(autodesk_enabled=True, autodesk_api_enabled=True, autodesk_client_id="AbCdEf123456",
                            autodesk_client_secret="s3cret-autodesk", autodesk_context_id="TEAM-1")
        self.assertEqual([x["id"] for x in st["steps"]],
                         ["prepare", "secrets", "acl", "autodesk_config", "start", "collect"])
        text = self.s.read_text(self.a.config_path)
        cfg = yaml.safe_load(text)
        parse_config(cfg)
        self.assertEqual(cfg["products"][0]["product"], "AutoCAD")             # réglages à la main intacts
        self.assertEqual(cfg["reporting"]["currency"], "CHF")
        self.assertEqual(cfg["autodesk"]["api"]["client_id"], "AbCdEf123456")
        self.assertTrue(cfg["autodesk"]["api"]["client_secret"].startswith("dpapi:"))
        self.assertNotIn("s3cret-autodesk", text)
        self.assertTrue(self.s.exists(self.a.path("secrets", "autodesk_client_secret.dpapi")))
        saved = json.loads(self.s.read_text(self.a.path(ANSWERS_FILE)))
        self.assertTrue(saved["autodesk_api_enabled"])
        self.assertNotIn("autodesk_client_secret", saved)
        later = " ".join(self.s.calls[calls:])
        self.assertNotIn("register_task", later)                               # tâches, IIS, base : inchangés
        self.assertNotIn("configure_iis_site", later)
        self.assertIn("rapport de configuration Autodesk", self.app.installer.report_text())

    def test_disable_keeps_manual_costs_out_and_rest_intact(self):
        self.configure(autodesk_enabled=True)
        cfg = yaml.safe_load(self.s.read_text(self.a.config_path))
        self.assertIn("autodesk", cfg)
        self.configure(autodesk_enabled=False)
        cfg = yaml.safe_load(self.s.read_text(self.a.config_path))
        self.assertNotIn("autodesk", cfg)
        self.assertEqual(cfg["sites"][0]["site"], "Lyon")

    def test_invalid_client_id_refused(self):
        with self.assertRaises(SetupError):
            self.app.start_autodesk_config(Answers(autodesk_enabled=True, autodesk_api_enabled=True,
                                                   autodesk_client_id="x"))


if __name__ == "__main__":
    unittest.main()
