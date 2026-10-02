"""Étape « Tickets ITSM » de l'assistant : configuration, secrets chiffrés (DPAPI), ticket de test réel."""

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import yaml

from heptagoneslm import vault
from heptagoneslm.config import parse_config
from heptagoneslm.itsm import ItsmError, make_connector, sync
from heptagoneslm.setup import probes
from heptagoneslm.setup.installer import Installer, build_plan
from heptagoneslm.setup.server import make_server
from heptagoneslm.setup.verify import verify
from tests.fake_servicenow import CA_FILE, PASSWORD, USER, FakeServiceNow
from tests.test_setup import answers, ready_system

SNOW_SECRET = "Pwd-ITSM-ne-doit-jamais-apparaitre"


def itsm_answers(url="https://snow.entreprise.local", **over):
    base = {"itsm_enabled": True, "itsm_type": "servicenow", "itsm_url": url, "itsm_user": USER,
            "itsm_password": SNOW_SECRET, "itsm_assignment_group": "Support licences"}
    base.update(over)
    return answers(**base)


class ItsmAnswersTest(unittest.TestCase):
    def test_disabled_by_default(self):
        a = answers()
        self.assertFalse(a.itsm_enabled)
        self.assertNotIn("alerts", a.config_dict())
        self.assertEqual(a.secret_files(), {})
        self.assertEqual(a.validate("itsm"), [])

    def test_servicenow_config_references_encrypted_secret(self):
        a = itsm_answers()
        self.assertEqual(a.validate(), [])
        text = a.yaml_text()
        self.assertNotIn(SNOW_SECRET, text)
        self.assertIn("dpapi:C:/HeptagonesLM/secrets/itsm_password.dpapi", text)
        self.assertIsNone(a.check_config())
        cfg = parse_config(yaml.safe_load(text))
        snow = cfg.alerts.itsm[0]
        self.assertEqual((snow.type, snow.username, snow.assignment_group, snow.caller),
                         ("servicenow", USER, "Support licences", USER))
        self.assertEqual(cfg.alerts.recipients, [])              # tickets sans e-mail
        self.assertEqual(a.secret_files(), {r"C:\HeptagonesLM\secrets\itsm_password.dpapi": SNOW_SECRET})
        self.assertEqual(a.to_dict()["itsm_password"], "********")

    def test_email_alerts_and_tickets_together(self):
        a = itsm_answers(email_enabled=True, smtp_host="smtp.entreprise.local", sender="h@entreprise.local",
                         recipients=["dsi@entreprise.local"])
        alerts = a.config_dict()["alerts"]
        self.assertEqual(alerts["recipients"], ["dsi@entreprise.local"])
        self.assertEqual(alerts["itsm"][0]["type"], "servicenow")

    def test_every_tool(self):
        jira = itsm_answers("https://entreprise.atlassian.net", itsm_type="jira", itsm_project="lic",
                            itsm_user="svc@entreprise.fr")
        glpi = itsm_answers("https://glpi.entreprise.local/apirest.php", itsm_type="glpi", itsm_app_token="APP-SECRET")
        hook = itsm_answers("https://entreprise.webhook.office.com/x", itsm_type="webhook", itsm_password="")
        for a in (jira, glpi, hook):
            self.assertEqual(a.validate(), [], a.itsm_type)
            self.assertIsNone(a.check_config(), a.itsm_type)
            self.assertNotIn(SNOW_SECRET, a.yaml_text())
            self.assertNotIn("APP-SECRET", a.yaml_text())
        self.assertEqual(jira.itsm_entry()["project"], "LIC")
        self.assertEqual(set(glpi.itsm_entry()) >= {"app_token", "user_token"}, True)
        self.assertEqual(len(glpi.secret_files()), 2)
        self.assertEqual(hook.secret_files(), {})
        self.assertEqual(hook.itsm_entry()["format"], "teams")

    def test_validation(self):
        def fields(**over):
            return {e["field"] for e in itsm_answers(**over).validate("itsm")}
        self.assertIn("itsm_url", fields(itsm_url="http://snow.entreprise.local"))   # identifiants en clair
        self.assertIn("itsm_url", fields(itsm_url=""))
        self.assertEqual(fields(itsm_user="", itsm_password=""), {"itsm_user", "itsm_password"})
        self.assertIn("itsm_project", fields(itsm_type="jira", itsm_project="pas une clé"))
        self.assertEqual(fields(itsm_type="glpi", itsm_url="https://glpi.local", itsm_app_token=""),
                         {"itsm_url", "itsm_app_token"})
        self.assertIn("itsm_min_severity", fields(itsm_min_severity="tout"))
        self.assertIn("itsm_type", fields(itsm_type="remedy"))

    def test_ca_file_passed_to_connector(self):
        a = itsm_answers(itsm_ca_file=r"C:\HeptagonesLM\certs\ac.pem")
        self.assertEqual(parse_config(a.config_dict()).alerts.itsm[0].ca_file, "C:/HeptagonesLM/certs/ac.pem")


class VaultTest(unittest.TestCase):
    def test_plain_values_unchanged(self):
        self.assertIsNone(vault.resolve(None))
        self.assertEqual(vault.resolve("clair"), "clair")
        self.assertTrue(vault.is_reference("DPAPI:C:/x.dpapi"))
        self.assertEqual(vault.reference(r"C:\H\secrets\a.dpapi"), "dpapi:C:/H/secrets/a.dpapi")

    def test_missing_file(self):
        with self.assertRaisesRegex(vault.SecretError, "introuvable"):
            vault.resolve("dpapi:" + os.path.join(tempfile.gettempdir(), "absent-heptagoneslm.dpapi"))

    @unittest.skipUnless(os.name == "nt", "DPAPI : Windows uniquement")
    def test_dpapi_round_trip_on_windows(self):
        blob = vault.protect("Mot de passe é€ 123")
        self.assertNotIn("Mot de passe".encode("utf-16-le"), blob)
        self.assertNotIn(b"Mot de passe", blob)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.dpapi")
            Path(path).write_bytes(blob)
            self.assertEqual(vault.resolve(vault.reference(path)), "Mot de passe é€ 123")
            Path(path).write_bytes(blob[:-4] + b"xxxx")                     # fichier altéré
            with self.assertRaisesRegex(vault.SecretError, "indéchiffrable"):
                vault.resolve(vault.reference(path))

    @unittest.skipIf(os.name == "nt", "hors Windows")
    def test_dpapi_unavailable_elsewhere(self):
        with self.assertRaises(vault.SecretError):
            vault.protect("x")

    def test_connector_decrypts_only_in_memory(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "itsm_password.dpapi")
            Path(path).write_bytes(b"chiffre")
            cfg = parse_config(itsm_answers().config_dict()).alerts.itsm[0]
            cfg.password = vault.reference(path)
            with mock.patch.object(vault, "unprotect", return_value=SNOW_SECRET) as unprotect:
                conn = make_connector(cfg, None)
            unprotect.assert_called_once_with(b"chiffre")
            self.assertEqual(conn.cfg.password, SNOW_SECRET)
            self.assertTrue(cfg.password.startswith("dpapi:"))             # configuration inchangée

    def test_unreadable_secret_is_reported_not_raised(self):
        cfg = parse_config(itsm_answers().config_dict())
        with self.assertRaisesRegex(ItsmError, "introuvable"):
            make_connector(cfg.alerts.itsm[0], cfg)
        result = sync(cfg, mock.MagicMock(query_dicts=lambda *a: []), None, [], [], [])
        self.assertEqual(len(result.errors), 1)
        self.assertIn("Secret introuvable", result.errors[0])


class ItsmInstallTest(unittest.TestCase):
    def test_install_writes_encrypted_secret_and_verifies_it(self):
        s, a = ready_system(), itsm_answers()
        self.assertIn("secrets", [st.id for st in build_plan(a)])
        inst = Installer(s, a)
        inst.start(); inst.wait(30)
        self.assertEqual(inst.status, "done", inst.error)
        stored = s._real(r"C:\HeptagonesLM\secrets\itsm_password.dpapi").read_bytes()
        self.assertNotIn(SNOW_SECRET.encode(), stored)
        self.assertIn((a.secrets_dir, "SYSTEM, Administrateurs, ENTREPRISE\\svc_heptagoneslm", "secrets"), s.acl)
        self.assertNotIn(SNOW_SECRET, s.read_text(a.config_path))
        self.assertNotIn(SNOW_SECRET, inst.report_text())
        self.assertIn("DPAPI", inst.report_text())
        checks = {c["id"]: c for c in verify(s, a)["checks"]}
        self.assertEqual(checks[r"secret:C:\HeptagonesLM\secrets\itsm_password.dpapi"]["status"], "ok")

    def test_verification_flags_missing_secret(self):
        s, a = ready_system(), itsm_answers()
        checks = {c["id"]: c for c in verify(s, a)["checks"]}
        self.assertEqual(checks[r"secret:C:\HeptagonesLM\secrets\itsm_password.dpapi"]["status"], "error")


class ItsmTicketTestButton(unittest.TestCase):
    """Bouton « Ouvrir un ticket de test » contre un faux ServiceNow HTTPS (authentification Basic)."""

    @classmethod
    def setUpClass(cls):
        cls.snow = FakeServiceNow().start()

    @classmethod
    def tearDownClass(cls):
        cls.snow.shutdown()
        cls.snow.server_close()

    def a(self, **over):
        base = {"itsm_url": self.snow.url, "itsm_password": PASSWORD, "itsm_ca_file": CA_FILE}
        base.update(over)
        return itsm_answers(**base)

    def test_ticket_opened_commented_and_resolved(self):
        before = len(self.snow.incidents)
        r = probes.test_itsm(ready_system(), self.a())
        self.assertEqual(r["status"], "ok", r)
        self.assertEqual(len(self.snow.incidents), before + 1)
        inc = list(self.snow.incidents.values())[-1]
        self.assertEqual(inc["state"], "6")                                  # résolu
        self.assertEqual(inc["short_description"], "[HeptagonesLM] Test de connexion HeptagonesLM")
        self.assertEqual((inc["assignment_group"], inc["caller_id"]), ("Support licences", USER))
        self.assertEqual(len(inc["work_notes"]), 1)
        self.assertIn(inc["number"], r["details"][0])
        self.assertIn("résolution : OK", r["details"])

    def test_wrong_password(self):
        r = probes.test_itsm(ready_system(), self.a(itsm_password="mauvais"))
        self.assertEqual(r["status"], "error")
        self.assertIn("HTTP 401", r["message"])
        self.assertIn("refusé", r["details"][0])

    def test_unknown_certificate_authority(self):
        r = probes.test_itsm(ready_system(), self.a(itsm_ca_file=""))
        self.assertEqual(r["status"], "error")
        self.assertIn("Certificat", r["details"][0])

    def test_incomplete_form(self):
        r = probes.test_itsm(ready_system(), self.a(itsm_user=""))
        self.assertEqual(r["status"], "error")
        self.assertIn("Compte technique requis", r["details"])
        self.assertEqual(probes.test_itsm(ready_system(), answers())["status"], "error")

    def test_through_setup_server(self):
        httpd, app = make_server(ready_system(), 0, token="jeton-itsm")
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            def post(path, body):
                req = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}{path}",
                                             data=json.dumps(body).encode(), headers={
                                                 "Cookie": "heptagoneslm_setup=jeton-itsm", "X-HeptagonesLM-Setup": "1",
                                                 "Content-Type": "application/json"})
                with urllib.request.urlopen(req) as resp:
                    return json.loads(resp.read())
            data = self.a().to_dict(secrets=True)
            self.assertEqual(post("/api/validate", {"answers": data, "step": "itsm"})["errors"], [])
            r = post("/api/test", {"kind": "itsm", "answers": data})
            self.assertEqual(r["status"], "ok", r)
            preview = post("/api/preview", {"answers": data})
            self.assertNotIn(PASSWORD, json.dumps(preview))
            self.assertIn("Stockage des secrets", preview["plan"]["steps"])
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
