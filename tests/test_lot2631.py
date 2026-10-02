"""Lot 26.3.1 : vérification du serveur accélérée (lectures parallèles, progression)."""

import json
import threading
import time
import unittest
import urllib.request

from heptagoneslm.setup import iis
from heptagoneslm.setup.checks import PROBES, run_checks
from heptagoneslm.setup.server import make_server
from heptagoneslm.setup.system import SimulatedSystem
from tests.test_setup import answers, ready_system


class SlowSystem(SimulatedSystem):
    """Chaque lecture dure 0,3 s : enchaînées, elles prendraient plus d'une seconde."""

    def os_info(self):
        time.sleep(0.3)
        return super().os_info()

    def iis_state(self):
        time.sleep(0.3)
        return super().iis_state()

    def certificates(self):
        time.sleep(0.3)
        return super().certificates()

    def find_lmutil(self):
        time.sleep(0.3)
        return super().find_lmutil()

    def odbc_drivers(self):
        time.sleep(0.3)
        return super().odbc_drivers()


class ParallelChecksTest(unittest.TestCase):
    def test_reads_run_in_parallel_with_progress(self):
        events, timings = [], {}
        t0 = time.monotonic()
        checks = run_checks(SlowSystem(delay=0), answers(), progress=lambda *e: events.append(e), timings=timings)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 1.0, f"lectures enchaînées ({elapsed:.2f} s)")
        self.assertEqual(set(timings), {"os", "lmutil", "odbc", "iis", "certs"})
        running = [e[0] for e in events if e[2] == "running"]
        done = [e[0] for e in events if e[2] == "done"]
        self.assertEqual(set(running), set(timings))
        self.assertEqual(set(done), set(timings))
        self.assertTrue(all(e[1] == PROBES[e[0]] for e in events))
        self.assertIn("os", {c.id for c in checks})

    def test_same_results_as_before(self):
        ids = [c.id for c in run_checks(ready_system(), answers(), final=True)]
        for expected in ("os", "powershell", "domain", "admin", "disk", "account", "odbc", "lmutil", "port",
                         "feature:Web-Server", "module:rewrite", "certs"):
            self.assertIn(expected, ids)

    def test_read_failure_reported_on_its_check(self):
        class BrokenIis(SimulatedSystem):
            def iis_state(self):
                raise RuntimeError("WinRM indisponible")
        checks = {c.id: c for c in run_checks(BrokenIis(delay=0), answers())}
        self.assertEqual(checks["iis"].status, "error")
        self.assertIn("WinRM indisponible", checks["iis"].detail)
        self.assertEqual(checks["os"].status, "ok")                 # les autres contrôles restent valides

    def test_os_info_read_once(self):
        calls = []

        class Counting(SimulatedSystem):
            def _read(self):
                calls.append(1)
                time.sleep(0.1)
                return {"caption": "x"}

            def os_info(self):
                return self.cached("os_info", self._read)

        s = Counting(delay=0)
        threads = [threading.Thread(target=s.os_info) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(calls), 1)

    def test_iis_absent_short_circuits_windows_feature(self):
        script = iis.state_script()
        self.assertLess(script.index("W3SVC"), script.index("Get-WindowsFeature"))
        self.assertIn("$kind = 'absent'", script)
        # IIS présent : requête CIM ciblée avant Get-WindowsFeature (plus d'une minute et demie sur Server 2025)
        self.assertLess(script.index("Win32_OptionalFeature"), script.index("Get-WindowsFeature -Name"))

    def test_http_auth_status_script(self):
        from heptagoneslm.setup.system import WindowsSystem

        seen = {}
        w = WindowsSystem.__new__(WindowsSystem)
        w.ps_json = lambda script, **kw: seen.update(script=script, **kw) or {"code": 401}
        self.assertEqual(w.http_auth_status("https://h/acces", "DOM\\bob", "s3cret"), 401)
        script = seen["script"]
        self.assertNotIn("ConvertTo-SecureString", script)   # module Security parfois impossible à charger
        self.assertIn("$user = 'DOM\\bob'", script)
        self.assertIn("IndexOf([char]92)", script)
        self.assertNotIn("s3cret", script)                   # secret transmis par variable d'environnement
        self.assertEqual(seen["secret"], "s3cret")


class ChecksJobTest(unittest.TestCase):
    def test_start_and_poll(self):
        httpd, app = make_server(SlowSystem(delay=0), 0, token="jeton-chk")
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        hdr = {"Cookie": "heptagoneslm_setup=jeton-chk"}

        def call(path, body=None):
            h = dict(hdr)
            data = None
            if body is not None:
                h.update({"Content-Type": "application/json", "X-HeptagonesLM-Setup": "1"})
                data = json.dumps(body).encode()
            with urllib.request.urlopen(urllib.request.Request(base + path, data=data, headers=h)) as r:
                return json.loads(r.read())
        try:
            self.assertEqual(call("/api/checks/state")["status"], "idle")
            first = call("/api/checks/start", {"answers": answers().to_dict(secrets=True), "final": False})
            self.assertEqual(first["status"], "running")
            seen_running = False
            for _ in range(50):
                st = call("/api/checks/state")
                if st["status"] == "running":
                    seen_running = seen_running or any(s["state"] == "running" for s in st["steps"])
                    time.sleep(0.05)
                    continue
                break
            self.assertEqual(st["status"], "done")
            self.assertTrue(seen_running)
            self.assertEqual({s["state"] for s in st["steps"]}, {"done"})
            self.assertIn("checks", st)
            self.assertIn("iis", st["timings"])
            self.assertGreater(st["blocking"], 0)                  # IIS absent dans la simulation
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
