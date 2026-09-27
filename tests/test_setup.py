"""Assistant d'installation : réponses, IIS, contrôles, installation, vérification, serveur, mode silencieux."""

import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import yaml

from freelm.config import parse_config
from freelm.setup import iis
from freelm.setup.answers import DB_PASSWORD_VAR, Answers
from freelm.setup.checks import apply_fix, blocking, run_checks
from freelm.setup.installer import Installer, build_plan
from freelm.setup.server import make_server
from freelm.setup.system import SimulatedSystem, TaskSpec, WindowsSystem, make_system
from freelm.setup.unattended import run as run_unattended
from freelm.setup.verify import verify

THUMB = "3F2A9C4D8E1B7A6F5C0D2E9B8A7F6C5D4E3B2A19"


def answers(**over) -> Answers:
    base = {
        "service_account": "ENTREPRISE\\svc_freelm", "service_password": "Secret-1",
        "db_server": "SQLSRV01", "lmutil_path": r"C:\Program Files\Autodesk\Network License Manager\lmutil.exe",
        "servers": [{"name": "AUTODESK", "port_at_host": "27000@licsrv-adsk"},
                    {"name": "ANSYS", "port_at_host": "1055@licsrv-ansys", "collectors": ["lmstat", "log"],
                     "log_path": r"\\licsrv-ansys\logs$\license.log"}],
        "public_host": "freelm.entreprise.local", "iis_cert_thumbprint": THUMB,
        "access_group": "ENTREPRISE\\GG_FreeLM_Lecteurs",
    }
    base.update(over)
    return Answers.from_dict(base)


def ready_system() -> SimulatedSystem:
    s = SimulatedSystem(delay=0)
    s.features = {f[0]: True for f in iis.FEATURES}
    s.modules = {k: True for k in iis.MODULES}
    return s


class AnswersTest(unittest.TestCase):
    def test_valid_configurations_for_every_mode(self):
        extra = {"https": {"tls_cert": "C:/c.pem", "tls_key": "C:/k.pem", "ldap_servers": "ldaps://dc01.entreprise.local",
                           "base_dn": "DC=entreprise,DC=local", "upn_suffix": "entreprise.local"}}
        for mode in ("iis", "https", "local"):
            a = answers(web_mode=mode, **extra.get(mode, {}))
            self.assertEqual(a.validate(), [], mode)
            cfg = parse_config(yaml.safe_load(a.yaml_text()))       # chargeur réel de FreeLM
            self.assertEqual(len(cfg.servers), 2)
            self.assertEqual(cfg.web.auth.mode, {"iis": "header", "https": "ldap", "local": "none"}[mode])
        iis_cfg = parse_config(answers().config_dict())
        self.assertEqual(iis_cfg.web.host, "127.0.0.1")               # jamais exposé derrière IIS
        self.assertEqual(iis_cfg.web.public_url, "https://freelm.entreprise.local")
        self.assertEqual(iis_cfg.servers[1].sessions_from, "log")

    def test_validation_errors_by_step(self):
        a = Answers()
        fields = {e["field"] for e in a.validate("location")}
        self.assertIn("service_account", fields)
        self.assertEqual({e["field"] for e in a.validate("licenses")} >= {"servers", "lmutil_path"}, True)
        self.assertIn("servers.0.port_at_host", {e["field"] for e in answers(servers=[{"name": "X", "port_at_host": "licsrv"}]).validate("licenses")})
        self.assertIn("service_account", {e["field"] for e in answers(service_account="NT AUTHORITY\\SYSTEM").validate("location")})
        self.assertEqual(answers(service_account="ENTREPRISE\\gmsa_freelm$", service_password="").validate("location"), [])
        self.assertIn("service_password", {e["field"] for e in answers(service_password="").validate("location")})
        self.assertIn("iis_cert_thumbprint", {e["field"] for e in answers(iis_cert_thumbprint="abc").validate("web")})
        self.assertIn("smtp_security", {e["field"] for e in answers(email_enabled=True, smtp_host="smtp.x.fr", smtp_user="u",
                                                                     smtp_security="none", sender="a@x.fr",
                                                                     recipients=["b@x.fr"]).validate("email")})

    def test_secrets_never_written(self):
        a = answers(db_auth="sql", db_user="freelm", db_password="Pwd-SQL", email_enabled=True, smtp_host="smtp.x.fr",
                    smtp_user="u", smtp_password="Pwd-SMTP", sender="FreeLM <freelm@x.fr>", recipients="dsi@x.fr")
        text = a.yaml_text()
        for secret in ("Secret-1", "Pwd-SQL", "Pwd-SMTP"):
            self.assertNotIn(secret, text)
        self.assertIn("${" + DB_PASSWORD_VAR + "}", a.connection_string())
        self.assertEqual(set(a.machine_secrets()), {"FREELM_DB_PASSWORD", "FREELM_SMTP_PASSWORD"})
        self.assertEqual(a.to_dict()["db_password"], "********")

    def test_sqlite_mode(self):
        a = answers(db_mode="sqlite", db_server="")
        self.assertEqual(a.validate("database"), [])
        self.assertEqual(a.connection_string(), "sqlite:///C:/FreeLM/data/freelm.db")


class IisTest(unittest.TestCase):
    def test_web_config(self):
        xml = iis.web_config(answers(web_port=8800))
        self.assertIn('url="http://127.0.0.1:8800/{R:1}"', xml)
        self.assertIn('name="HTTP_X_REMOTE_USER" value="{LOGON_USER}"', xml)
        self.assertIn('roles="ENTREPRISE\\GG_FreeLM_Lecteurs"', xml)
        self.assertNotIn("<authorization>", iis.web_config(answers(access_group="")))
        import xml.dom.minidom
        xml.dom.minidom.parseString(iis.web_config(answers(access_group='ENT\\a"b&c')))    # échappement XML

    def test_powershell_quoting(self):
        self.assertEqual(iis.ps_quote("it's"), "'it''s'")
        script = iis.configure_site_script(answers(iis_site="Free'; Remove-Item C:\\ -Recurse; '"), r"C:\FreeLM\iis")
        self.assertIn("$site = 'Free''; Remove-Item C:\\ -Recurse; '''", script)


class WindowsScriptsTest(unittest.TestCase):
    """Les scripts PowerShell sont générés sans jamais contenir de mot de passe."""

    def setUp(self):
        self.calls = []
        self.sys = WindowsSystem()
        from freelm.setup.system import Result
        self.sys.ps = lambda script, secret=None, timeout=600: (self.calls.append((script, secret)), Result(0, "{}"))[1]

    def test_register_task_password_outside_script(self):
        spec = TaskSpec("FreeLM - Collecte", r"C:\FreeLM\FreeLM.exe", '-c "C:\\FreeLM\\freelm.yaml" collect', r"C:\FreeLM", "repeat", 5)
        self.sys.register_task(spec, "ENTREPRISE\\svc_freelm", "P@ss'w0rd")
        script, secret = self.calls[-1]
        self.assertNotIn("P@ss", script)
        self.assertEqual(secret, "P@ss'w0rd")
        self.assertIn("-Password $env:FREELM_SETUP_SECRET", script)
        self.assertIn("-RepetitionInterval (New-TimeSpan -Minutes 5)", script)
        self.sys.register_task(TaskSpec("FreeLM - Interface web", "x.exe", "web", "C:\\", "startup", permanent=True),
                               "ENTREPRISE\\gmsa_freelm$", "")
        script, _ = self.calls[-1]
        self.assertIn("-LogonType Password", script)
        self.assertIn("-RestartCount 999", script)

    def test_machine_env_secret(self):
        import os
        self.sys.set_machine_env("FREELM_TEST_VAR", "valeur-secrète")
        script, secret = self.calls[-1]
        self.assertNotIn("valeur-secrète", script)
        self.assertEqual(secret, "valeur-secrète")
        os.environ.pop("FREELM_TEST_VAR", None)

    def test_make_system_requires_windows_or_simulation(self):
        import os
        if os.name != "nt":
            from freelm.setup.system import SetupError
            with self.assertRaises(SetupError):
                make_system(False)
        self.assertTrue(make_system(True).simulated)


class ChecksTest(unittest.TestCase):
    def test_iis_missing_then_fixed(self):
        s = SimulatedSystem(delay=0)
        a = answers()
        errors = blocking(run_checks(s, a))
        self.assertEqual({c.fix for c in errors}, {"install_iis", "install_rewrite", "install_arr"})
        for fix in ("install_iis", "install_rewrite", "install_arr"):
            apply_fix(s, fix)
        self.assertEqual(blocking(run_checks(s, a)), [])

    def test_iis_not_required_in_local_mode(self):
        checks = run_checks(SimulatedSystem(delay=0), answers(web_mode="local"))
        self.assertEqual(blocking(checks), [])
        self.assertEqual(next(c for c in checks if c.id == "iis").status, "info")

    def test_not_admin_is_blocking(self):
        class NoAdmin(SimulatedSystem):
            def is_admin(self):
                return False
        self.assertIn("admin", {c.id for c in blocking(run_checks(NoAdmin(delay=0), answers(web_mode="local")))})


class InstallerTest(unittest.TestCase):
    def test_full_install_and_verification(self):
        s = ready_system()
        a = answers(db_auth="sql", db_user="freelm", db_password="Pwd-SQL")
        inst = Installer(s, a)
        self.assertIn("secrets", [st.id for st in inst.steps])
        inst.start(); inst.wait(30)
        st = inst.state()
        self.assertEqual(st["status"], "done", st["error"])
        self.assertEqual(st["percent"], 100.0)
        self.assertEqual({t for t in s.tasks}, {"FreeLM - Collecte", "FreeLM - Rapport mensuel", "FreeLM - Purge",
                                                "FreeLM - Interface web"})
        self.assertIn('"C:\\FreeLM\\freelm.yaml" collect', s.tasks["FreeLM - Collecte"]["spec"])
        written = s.read_text(a.config_path)
        parse_config(yaml.safe_load(written))
        self.assertNotIn("Pwd-SQL", written)
        self.assertIn('roles="ENTREPRISE\\GG_FreeLM_Lecteurs"', s.read_text(a.path("iis", "web.config")))
        self.assertTrue(any(rights == "M" and path.endswith("logs") for path, _, rights in s.acl))
        journal = inst.report_text()
        for secret in ("Secret-1", "Pwd-SQL"):
            self.assertNotIn(secret, journal)
        v = verify(s, a)
        self.assertTrue(v["success"], [c for c in v["checks"] if c["status"] == "error"])

    def test_failure_then_resume(self):
        s = ready_system()
        s.fail_once = {"register_task:FreeLM - Purge"}
        inst = Installer(s, answers())
        inst.start(); inst.wait(30)
        self.assertEqual(inst.status, "failed")
        failed = [st for st in inst.steps if st.status == "failed"]
        self.assertEqual(failed[0].id, "tasks")
        self.assertIn("Purge", inst.error)
        self.assertLess(inst.percent, 100)
        calls_before = len(s.calls)
        inst.start(resume=True); inst.wait(30)
        self.assertEqual(inst.status, "done")
        self.assertNotIn("db_create", s.calls[calls_before:])            # reprise sans rejouer le début

    def test_plan_depends_on_mode(self):
        local = [st.id for st in build_plan(answers(web_mode="local"))]
        self.assertNotIn("iis_site", local)
        self.assertNotIn("firewall", local)
        self.assertNotIn("secrets", local)
        self.assertIn("iis_prereq", [st.id for st in build_plan(answers())])

    def test_verification_before_install_reports_errors(self):
        v = verify(ready_system(), answers())
        self.assertFalse(v["success"])


class SetupServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.system = ready_system()
        cls.httpd, cls.app = make_server(cls.system, 0, token="jeton-test")
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def req(self, path, body=None, cookie=True, headers=None):
        h = {"Cookie": "freelm_setup=jeton-test"} if cookie else {}
        if body is not None:
            h.update({"Content-Type": "application/json", "X-FreeLM-Setup": "1"})
        h.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base + path, data=data, headers=h)) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_security(self):
        self.assertEqual(self.req("/", cookie=False)[0], 403)
        self.assertEqual(self.req("/api/info", cookie=False)[0], 403)
        self.assertEqual(self.req("/api/info", headers={"Cookie": "freelm_setup=faux"})[0], 403)
        self.assertEqual(self.req("/api/info", headers={"Host": "attaquant.example"})[0], 421)
        # POST sans l'en-tête dédié (formulaire intersites) : refusé
        status, _ = self.req("/api/checks", None, headers={"Content-Type": "application/json"})
        self.assertIn(status, (403, 404))
        r = urllib.request.Request(self.base + "/api/checks", data=b"{}", headers={"Cookie": "freelm_setup=jeton-test"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(r)
        self.assertEqual(ctx.exception.code, 403)
        # le jeton de l'adresse est échangé contre un cookie HttpOnly
        opener = urllib.request.build_opener(type("NoRedirect", (urllib.request.HTTPRedirectHandler,), {
            "redirect_request": lambda *a, **k: None}))
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            opener.open(self.base + "/?token=jeton-test")
        self.assertEqual(ctx.exception.code, 303)
        self.assertIn("HttpOnly", ctx.exception.headers["Set-Cookie"])

    def test_full_flow(self):
        status, body = self.req("/api/info")
        info = json.loads(body)
        self.assertTrue(info["simulated"])
        self.assertEqual(info["defaults"]["iis_cert_thumbprint"], THUMB)
        a = answers().to_dict(secrets=True)
        self.assertEqual(json.loads(self.req("/api/validate", {"answers": a, "step": "location"})[1])["errors"], [])
        test = json.loads(self.req("/api/test", {"kind": "lmstat", "answers": a, "port_at_host": "1055@licsrv-ansys"})[1])
        self.assertEqual(test["status"], "ok")
        preview = json.loads(self.req("/api/preview", {"answers": a})[1])
        self.assertIn("FreeLM - Collecte", [t["name"] for t in preview["plan"]["tasks"]])
        self.assertNotIn("Secret-1", json.dumps(preview))
        self.assertEqual(self.req("/api/install", {"answers": a})[0], 200)
        for _ in range(200):
            st = json.loads(self.req("/api/progress?since=0")[1])
            if st["status"] != "running":
                break
            threading.Event().wait(0.05)
        self.assertEqual(st["status"], "done", st.get("error"))
        v = json.loads(self.req("/api/verify", {})[1])
        self.assertTrue(v["success"])
        status, report = self.req("/api/report")
        self.assertIn(b"Statut : done", report)
        self.assertNotIn(b"Secret-1", report)


class UnattendedTest(unittest.TestCase):
    def write(self, data) -> str:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(data, tmp)
        tmp.close()
        return tmp.name

    def test_success_with_auto_fix_and_env_secret(self):
        import os
        os.environ["FREELM_TEST_PWD"] = "Secret-ENV"
        data = answers().to_dict(secrets=True) | {"service_password": "${FREELM_TEST_PWD}", "auto_fix": True}
        out = io.StringIO()
        code = run_unattended(SimulatedSystem(delay=0), self.write(data), out)
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("Installation terminée", out.getvalue())
        self.assertNotIn("Secret-ENV", out.getvalue())

    def test_blocking_prerequisites_without_auto_fix(self):
        out = io.StringIO()
        self.assertEqual(run_unattended(SimulatedSystem(delay=0), self.write(answers().to_dict(secrets=True)), out), 1)
        self.assertIn("Prérequis bloquants", out.getvalue())

    def test_invalid_answers(self):
        out = io.StringIO()
        self.assertEqual(run_unattended(SimulatedSystem(delay=0), self.write({"servers": []}), out), 1)
        self.assertIn("Réponses invalides", out.getvalue())
