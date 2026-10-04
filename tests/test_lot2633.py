"""26.3.3 : fermeture de l'assistant (bouton « Quitter », arrêt automatique) et fenêtre console masquée."""

import json
import threading
import unittest
import urllib.error
import urllib.request

from heptagoneslm import launcher
from heptagoneslm.setup.server import make_server
from tests.test_setup import ready_system


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


if __name__ == "__main__":
    unittest.main()
