"""Recette sur le site client (lot 26.3.1) : comparaison en direct, collecte planifiée, contrôles, PV."""

import importlib.util
import io
import json
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from xml.dom import minidom

from heptagoneslm.config import ServerConfig
from heptagoneslm.setup import recette
from heptagoneslm.setup.installer import ANSWERS_FILE, Installer
from heptagoneslm.setup.pv import build_pv
from heptagoneslm.setup.server import make_server
from tests.test_setup import answers, ready_system

FIX = Path(__file__).resolve().parent / "fixtures"
FLEX = (FIX / "lmstat_sample.txt").read_text(encoding="utf-8")
RLM = (FIX / "rlmstat_sample.txt").read_text(encoding="utf-8")
SRV = ServerConfig(name="ADSK", port_at_host="27000@licsrv1")


def installed_system(**over):
    s = ready_system()
    a = answers(db_mode="sqlite", email_enabled=True, smtp_host="smtp.entreprise.local", sender="h@entreprise.local",
                recipients=["dsi@entreprise.local"], **over)
    inst = Installer(s, a)
    inst.start()
    inst.wait(60)
    assert inst.status == "done", inst.error
    return s, a


class ReadTotalsTest(unittest.TestCase):
    def test_flexlm_summary_lines(self):
        t = recette.read_totals(FLEX)
        self.assertEqual(t["87545ACD_2024_0F"], {"issued": 10, "in_use": 3})
        self.assertEqual(t["87048INVPROSA_2024_0F"], {"issued": 1, "in_use": 1})
        self.assertEqual(t["Simulink"], {"issued": None, "in_use": 0})       # non compté
        self.assertNotIn("OLD_FEATURE", t)                                   # erreur du serveur : ignorée

    def test_rlm_pools_summed_by_license(self):
        t = recette.read_totals(RLM, "rlm")
        self.assertEqual(t["nuke_i"], {"issued": 12, "in_use": 3})           # deux pools (v2024 + v2023)
        self.assertEqual(t["nukex_i"], {"issued": 5, "in_use": 5})
        self.assertEqual(t["nuke_r"]["issued"], None)

    def test_agrees_with_parser(self):
        parsed = {f.name: f for f in recette.parse_output(FLEX, SRV).features}
        for name, tot in recette.read_totals(FLEX).items():
            self.assertEqual((parsed[name].issued, parsed[name].in_use), (tot["issued"], tot["in_use"]))


class CompareTest(unittest.TestCase):
    def setUp(self):
        self.live = recette.read_totals(FLEX)
        self.users = recette.live_sessions(recette.parse_output(FLEX, SRV))

    def recorded(self, **changes):
        feats = {k: dict(v) for k, v in self.live.items()}
        for name, vals in changes.items():
            if vals is None:
                feats.pop(name)
            else:
                feats[name].update(vals)
        return {"time": datetime.now(), "features": feats, "sessions": dict(self.users)}

    def test_identical(self):
        status, rows, details = recette.compare(self.live, self.users, self.recorded(), True)
        self.assertEqual(status, "ok")
        self.assertTrue(all(r[-1] == "identique" for r in rows))
        self.assertIn("identiques", details[-1])

    def test_issued_mismatch_is_an_error(self):
        status, rows, _ = recette.compare(self.live, self.users, self.recorded(MATLAB={"issued": 4}), True)
        self.assertEqual(status, "error")
        self.assertIn("jetons émis différents", next(r for r in rows if r[0] == "MATLAB")[-1])

    def test_feature_not_recorded_is_an_error(self):
        status, rows, _ = recette.compare(self.live, self.users, self.recorded(MATLAB=None), True)
        self.assertEqual(status, "error")
        self.assertIn("non enregistrée", next(r for r in rows if r[0] == "MATLAB")[-1])

    def test_usage_drift_is_a_warning(self):
        status, rows, _ = recette.compare(self.live, self.users, self.recorded(**{"87545ACD_2024_0F": {"in_use": 2}}), True)
        self.assertEqual(status, "warning")

    def test_missing_user_reported(self):
        rec = self.recorded()
        rec["sessions"].pop(next(iter(rec["sessions"])))
        status, rows, details = recette.compare(self.live, self.users, rec, True)
        self.assertEqual(status, "warning")
        self.assertTrue(any("non vu" in d for d in details))
        self.assertTrue(any("sessions ouvertes" in r[-1] for r in rows))

    def test_sessions_from_log_not_compared(self):
        rec = self.recorded()
        rec["sessions"] = {}
        status, rows, details = recette.compare(self.live, self.users, rec, False)
        self.assertEqual(status, "ok")
        self.assertEqual({r[5] for r in rows}, {"—"})


class HistoryTest(unittest.TestCase):
    now = datetime(2026, 10, 2, 12, 0)

    def runs(self, hours=24, every=5, fail_at=(), skip=()):
        out, t = [], self.now - timedelta(hours=hours)
        i = 0
        while t <= self.now:
            if i not in skip:
                out.append({"collector": "lmstat", "started_at": t, "status": "ERROR" if i in fail_at else "OK",
                            "message": "Serveur injoignable" if i in fail_at else None})
            t += timedelta(minutes=every)
            i += 1
        return out

    def health(self, runs, first=None, hours=24):
        since = self.now - timedelta(hours=hours)
        return recette.collection_health(runs, since, self.now, 5, first or (runs[0]["started_at"] if runs else None), hours)

    def test_regular(self):
        status, detail, details = self.health(self.runs())
        self.assertEqual(status, "ok", details)
        self.assertIn("289 / 289", detail)

    def test_none(self):
        self.assertEqual(self.health([])[0], "error")

    def test_interruption(self):
        status, _, details = self.health(self.runs(skip=range(100, 110)))
        self.assertEqual(status, "warning")
        self.assertTrue(any("Interruption" in d for d in details))

    def test_occasional_failure_then_error_rate(self):
        self.assertEqual(self.health(self.runs(fail_at={50}))[0], "warning")
        self.assertEqual(self.health(self.runs(fail_at=set(range(0, 289, 5))))[0], "error")
        self.assertEqual(self.health(self.runs(fail_at={288}))[0], "error")         # dernière collecte en échec

    def test_recent_install(self):
        status, _, details = self.health(self.runs(hours=2), hours=24)
        self.assertEqual(status, "warning")
        self.assertTrue(any("refaire la recette" in d for d in details))


class AnonymizeTest(unittest.TestCase):
    def test_names_replaced_and_still_parseable(self):
        status = recette.parse_output(FLEX, SRV)
        text, mapping = recette.anonymize(FLEX, status, SRV.port_at_host)
        for name in ("jdoe", "asmith", "PC123", "CAO-WS01", "Jean Dupont"):
            self.assertNotIn(name, text)
        self.assertIn("licsrv1", text)                                       # serveur conservé
        again = recette.read_totals(text)
        self.assertEqual(again, recette.read_totals(FLEX))
        users = recette.live_sessions(recette.parse_output(text, SRV))
        self.assertEqual(sum(users.values()), sum(recette.live_sessions(status).values()))
        self.assertTrue(all(u.startswith("utilisateur") for _f, u, _h in users))

    def test_expected_summary(self):
        exp = recette.expected_summary(FLEX, SRV, datetime(2026, 10, 2, 10, 0), False, "ok")
        self.assertEqual(exp["features"]["87545ACD_2024_0F"], {"issued": 10, "in_use": 3, "sessions": 3})
        self.assertEqual(exp["users"], 3)
        self.assertEqual(exp["source"], "lmstat")


class RecetteRunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.system, cls.answers = installed_system()
        path = recette.find_installed(cls.system, cls.answers.install_dir)
        cls.installed = recette.load_installed(cls.system, path)
        events = []
        opts = recette.Options(hours=24, outsider_user="ENTREPRISE\\jmartin", outsider_password="x",
                               member_user="ENTREPRISE\\hlm_lecteur", member_password="y")
        cls.rrun = recette.RecetteRun(cls.system, cls.installed, opts, progress=lambda *e: events.append(e))
        cls.state = cls.rrun.run()
        cls.events = events
        cls.items = {i["id"]: i for i in cls.state["items"]}

    def test_installation_found(self):
        self.assertEqual(self.installed.config_path, r"C:\HeptagonesLM\heptagoneslm.yaml")
        self.assertEqual(self.installed.answers.web_mode, "iis")
        self.assertEqual(self.installed.answers.access_group, "ENTREPRISE\\GG_HeptagonesLM_Lecteurs")

    def test_live_comparison(self):
        for name in ("AUTODESK", "ANSYS"):
            item = self.items[f"srv:{name}"]
            self.assertEqual(item["status"], "ok", item)
            self.assertTrue(item["table"]["rows"])
            self.assertTrue(all(r[-1] == "identique" for r in item["table"]["rows"]))
        self.assertEqual(self.items["collecte"]["status"], "ok")

    def test_history_and_access(self):
        self.assertEqual(self.items["historique:AUTODESK"]["status"], "ok")
        self.assertRegex(self.items["historique:AUTODESK"]["detail"], r"^(28[89]) / \1 collectes réussies")
        self.assertEqual(self.items["refus"]["status"], "ok")
        self.assertEqual(self.items["acces"]["status"], "ok")
        self.assertIn(self.items["installation"]["status"], ("ok", "warning"))

    def test_confirmations(self):
        email = self.items["email"]
        self.assertEqual(email["status"], "warning")                       # à confirmer
        self.assertTrue(email["confirm"])
        item = self.rrun.confirm("email", True)
        self.assertEqual(item.status, "ok")
        self.assertEqual(self.rrun.confirm("email", False).status, "error")
        self.assertEqual(self.rrun.confirm("email", None).status, "warning")

    def test_evidence_saved(self):
        files = self.state["files"]
        for name in ("lmstat_AUTODESK.txt", "lmstat_AUTODESK.attendu.json", "recette.json"):
            self.assertIn(name, files)
        raw = self.system.read_text(self.state["folder"] + "\\lmstat_AUTODESK.txt")
        exp = json.loads(self.system.read_text(self.state["folder"] + "\\lmstat_AUTODESK.attendu.json"))
        self.assertTrue(exp["anonymized"])
        self.assertNotRegex(raw, r"\buser\d{2}\b")                         # noms simulés remplacés
        self.assertEqual(recette.read_totals(raw), {k: {"issued": v["issued"], "in_use": v["in_use"]}
                                                     for k, v in exp["features"].items()})
        z = zipfile.ZipFile(io.BytesIO(self.rrun.evidence_zip()))
        self.assertTrue(any(n.endswith("lmstat_AUTODESK.txt") for n in z.namelist()))

    @unittest.skipIf(importlib.util.find_spec("openpyxl") is None, "openpyxl absent")
    def test_excel_report(self):
        item = self.items["rapport"]
        self.assertEqual(item["auto_status"], "ok", item)
        self.assertIn("rapport_recette.xlsx", self.state["files"])
        data = self.system.read_bytes(self.state["folder"] + "\\rapport_recette.xlsx")
        self.assertIn("xl/workbook.xml", zipfile.ZipFile(io.BytesIO(data)).namelist())

    def test_progress_reported(self):
        done = {e[0] for e in self.events if e[2] == "done"}
        self.assertTrue({"collecte", "srv:AUTODESK", "historique", "installation", "refus", "email"} <= done)

    def test_pv_document(self):
        data = build_pv(self.rrun.state(), {"client": "Entreprise & Cie <Lyon>", "redacteur": "A. Intégrateur"})
        z = zipfile.ZipFile(io.BytesIO(data))
        self.assertIn("word/document.xml", z.namelist())
        for name in z.namelist():
            if name.endswith((".xml", ".rels")):
                minidom.parseString(z.read(name))                          # XML bien formé
        doc = z.read("word/document.xml").decode("utf-8")
        for text in ("Procès-verbal de recette", "Entreprise &amp; Cie &lt;Lyon&gt;", "AUTODESK (27000@licsrv-adsk)",
                     "Décision et signatures", "Validé par (client)", "identique"):
            self.assertIn(text, doc)


class NoAnswersFileTest(unittest.TestCase):
    def test_installations_before_2631(self):
        s, a = installed_system()
        s._real(a.path(ANSWERS_FILE)).unlink()                            # installé avant la 26.3.1
        inst = recette.load_installed(s, a.config_path)
        self.assertEqual(inst.answers.web_mode, "iis")
        self.assertEqual(inst.answers.public_host, "heptagoneslm.entreprise.local")
        self.assertEqual(inst.answers.access_group, "ENTREPRISE\\GG_HeptagonesLM_Lecteurs")   # lu dans web.config

    def test_answers_file_has_no_secret(self):
        s, a = installed_system(itsm_enabled=True, itsm_url="https://snow.entreprise.local", itsm_user="svc",
                                itsm_password="Pwd-ITSM-secret")
        text = s.read_text(a.path(ANSWERS_FILE))
        self.assertNotIn("Secret-1", text)
        self.assertNotIn("Pwd-ITSM-secret", text)
        self.assertNotIn("service_password", json.loads(text))


class RecetteServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.system, cls.answers = installed_system()
        cls.httpd, cls.app = make_server(cls.system, 0, token="jeton-rec")
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def call(self, path, body=None, raw=False):
        h = {"Cookie": "heptagoneslm_setup=jeton-rec"}
        data = None
        if body is not None:
            h.update({"Content-Type": "application/json", "X-HeptagonesLM-Setup": "1"})
            data = json.dumps(body).encode()
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base + path, data=data, headers=h)) as r:
                payload = r.read()
                return (r.status, payload) if raw else json.loads(payload)
        except urllib.error.HTTPError as e:
            return (e.code, e.read()) if raw else {"http": e.code}

    def wait(self):
        import time
        for _ in range(200):
            st = self.call("/api/recette/state")
            if st["status"] != "running":
                return st
            time.sleep(0.05)
        self.fail("recette trop longue")

    def test_full_flow(self):
        self.assertEqual(self.call("/api/info")["installed"], r"C:\HeptagonesLM\heptagoneslm.yaml")
        ctx = self.call("/api/recette/context", {})
        self.assertEqual([s["name"] for s in ctx["installed"]["servers"]], ["AUTODESK", "ANSYS"])
        self.assertTrue(ctx["installed"]["email"]["configured"])
        self.call("/api/recette/start", {"options": {"hours": 24}})
        st = self.wait()
        self.assertEqual(st["status"], "done", st)
        items = {i["id"]: i for i in st["recette"]["items"]}
        self.assertEqual(items["refus"]["status"], "warning")               # sans compte de test : à confirmer
        r = self.call("/api/recette/confirm", {"id": "refus", "confirmed": True})
        self.assertEqual(r["item"]["status"], "ok")
        self.assertEqual(self.call("/api/recette/state")["recette"]["items"][
            [i["id"] for i in st["recette"]["items"]].index("refus")]["status"], "ok")
        pv = self.call("/api/recette/pv", {"client": "Démo", "redacteur": "Moi", "valideur": "Client"})
        status, data = self.call(pv["url"], raw=True)
        self.assertEqual(status, 200)
        self.assertEqual(data[:2], b"PK")
        status, data = self.call("/api/recette/preuves.zip", raw=True)
        self.assertEqual(status, 200)
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        self.assertTrue(any(n.endswith(pv["file"]) for n in names))
        status, _ = self.call("/api/recette/file?name=..%5Cheptagoneslm.yaml", raw=True)
        self.assertEqual(status, 404)
        status, _ = self.call("/api/recette/file?name=inconnu.txt", raw=True)
        self.assertEqual(status, 404)
        self.assertEqual(self.call("/api/recette/confirm", {"id": "inexistant", "confirmed": True}).get("http"), 422)


if __name__ == "__main__":
    unittest.main()
