"""Authentification unique : SPN, zone Intranet, page /acces et vérification finale."""

import json
import os
import tempfile
import unittest

from heptagoneslm.setup import iis, sso
from heptagoneslm.setup.answers import Answers
from heptagoneslm.setup.checks import apply_fix, blocking, run_checks
from heptagoneslm.setup.installer import Installer
from heptagoneslm.setup.system import SetupError, SimulatedSystem
from heptagoneslm.setup.verify import verify
from heptagoneslm.web import access
from tests.test_auth import _ServerCase

DOMAIN = {"hostname": "SRV-HLM", "fqdn": "srv-hlm.entreprise.local", "part_of_domain": True}


def answers(**kw):
    base = {"install_dir": r"C:\HeptagonesLM", "service_account": "ENTREPRISE\\svc_hlm", "service_password": "x",
            "db_mode": "sqlite", "lmutil_path": r"C:\lmutil.exe", "servers": [{"name": "A", "port_at_host": "1@a"}],
            "web_mode": "iis", "public_host": "heptagoneslm.entreprise.local", "iis_cert_thumbprint": "AB" * 20,
            "access_group": "ENTREPRISE\\GG_HLM"}
    base.update(kw)
    return Answers.from_dict(base)


class FakeAD(SimulatedSystem):
    def __init__(self, owners=None, error=None, policies=None):
        super().__init__(delay=0)
        self.owners, self.error, self.policies = owners or [], error, policies

    def spn_owners(self, spn):
        if self.error:
            raise SetupError(self.error)
        return self.owners

    def browser_policies(self):
        return self.policies if self.policies is not None else {"zones": [], "allowlist": {}}


class SpnTest(unittest.TestCase):
    def test_server_name_is_covered_by_host_spn(self):
        c = sso.spn_check(FakeAD(), answers(public_host="srv-hlm.entreprise.local"), DOMAIN)
        self.assertEqual(c.status, "ok")

    def test_missing_spn_offers_fix_and_command(self):
        c = sso.spn_check(FakeAD(), answers(), DOMAIN)
        self.assertEqual((c.status, c.fix), ("warning", "register_spn"))
        self.assertEqual(c.command, "setspn -S HTTP/heptagoneslm.entreprise.local SRV-HLM")

    def test_spn_on_computer_account(self):
        self.assertEqual(sso.spn_check(FakeAD(["SRV-HLM$"]), answers(), DOMAIN).status, "ok")

    def test_spn_on_other_account_is_an_error_with_commands(self):
        c = sso.spn_check(FakeAD(["SVC_OLD"]), answers(), DOMAIN)
        self.assertEqual(c.status, "error")
        self.assertIn("setspn -D HTTP/heptagoneslm.entreprise.local SVC_OLD", c.command)
        self.assertIn("setspn -S HTTP/heptagoneslm.entreprise.local SRV-HLM", c.command)
        dup = sso.spn_check(FakeAD(["SRV-HLM$", "SRV-OLD$"]), answers(), DOMAIN)
        self.assertIn("doublon", dup.detail)
        self.assertNotIn("setspn -S", dup.command)

    def test_not_in_domain_and_query_failure(self):
        self.assertEqual(sso.spn_check(FakeAD(), answers(), {"part_of_domain": False}).status, "info")
        c = sso.spn_check(FakeAD(error="annuaire injoignable"), answers(), DOMAIN)
        self.assertEqual(c.status, "warning")
        self.assertTrue(c.command.startswith("setspn -S"))

    def test_only_for_iis(self):
        self.assertIsNone(sso.spn_check(FakeAD(), answers(web_mode="https", tls_cert="c", tls_key="k"), DOMAIN))

    def test_wrong_spn_never_blocks_installation(self):
        class Wrong(FakeAD):
            def os_info(self):
                return {**super().os_info(), **DOMAIN}
        checks = run_checks(Wrong(["SVC_OLD"]), answers(), final=True)
        spn = [c for c in checks if c.id == "spn"][0]
        self.assertEqual(spn.status, "warning")
        self.assertNotIn("spn", {c.id for c in blocking(checks)})

    def test_register_spn_fix(self):
        s = SimulatedSystem(delay=0)
        message = apply_fix(s, "register_spn", answers())
        self.assertIn("HTTP/heptagoneslm.entreprise.local", message)
        self.assertEqual(s.spn_owners("HTTP/heptagoneslm.entreprise.local"), ["SRV-HEPTAGONESLM$"])
        with self.assertRaises(SetupError):
            apply_fix(s, "register_spn", answers(public_host=""))


class ZoneTest(unittest.TestCase):
    def test_zone_patterns(self):
        host = "heptagoneslm.entreprise.local"
        for p in ("https://heptagoneslm.entreprise.local", "*.entreprise.local", "https://*.entreprise.local",
                  "*://*.entreprise.local", "heptagoneslm.entreprise.local"):
            self.assertTrue(sso.zone_pattern_matches(p, host), p)
        for p in ("http://heptagoneslm.entreprise.local", "*.autre.local", "entreprise.local"):
            self.assertFalse(sso.zone_pattern_matches(p, host), p)

    def test_allowlist(self):
        host = "heptagoneslm.entreprise.local"
        self.assertTrue(sso.allowlist_matches("*.entreprise.local,intranet", host))
        self.assertTrue(sso.allowlist_matches("heptagoneslm.entreprise.local", host))
        self.assertFalse(sso.allowlist_matches("*.autre.local", host))

    def test_intranet_by_gpo(self):
        pol = {"zones": [{"pattern": "https://*.entreprise.local", "zone": "1", "scope": "ordinateur"}], "allowlist": {}}
        self.assertEqual(sso.zone_check(FakeAD(policies=pol), answers()).status, "ok")

    def test_missing_zone_gives_gpo_and_workstation_command(self):
        c = sso.zone_check(FakeAD(), answers())
        self.assertEqual(c.status, "warning")
        self.assertIn("Liste des attributions de sites aux zones", c.detail)
        self.assertIn(r"ZoneMap\Domains\entreprise.local\heptagoneslm", c.command)

    def test_trusted_sites_and_allowlist_warnings(self):
        trusted = {"zones": [{"pattern": "*.entreprise.local", "zone": "2", "scope": "ordinateur"}], "allowlist": {}}
        self.assertIn("Sites de confiance", sso.zone_check(FakeAD(policies=trusted), answers()).detail)
        edge = {"zones": [{"pattern": "*.entreprise.local", "zone": "1", "scope": "ordinateur"}],
                "allowlist": {"Edge": "*.autre.local"}}
        c = sso.zone_check(FakeAD(policies=edge), answers())
        self.assertEqual(c.status, "warning")
        self.assertIn("AuthServerAllowlist de Edge", c.detail)

    def test_short_name_is_intranet(self):
        self.assertEqual(sso.zone_check(FakeAD(), answers(public_host="heptagoneslm")).status, "ok")


class AccessResultsTest(unittest.TestCase):
    def test_record_parse_and_latest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = access.results_path(os.path.join(tmp, "logs", "heptagoneslm.log"))
            for user, at in (("a", "2026-09-28T09:00:00"), ("b", "2026-09-28T09:05:00"), ("a", "2026-09-28T09:10:00")):
                entry = access.make_entry(user, None, "Kerberos", "10.0.0.1", None, "Edge", False)
                entry["at"] = at
                access.record(path, entry)
            latest = access.latest_by_user(access.parse_results(path.read_text(encoding="utf-8") + "pas du json\n"))
            self.assertEqual([r["user"] for r in latest], ["a", "b"])

    def test_client_address_behind_proxy(self):
        headers = {"X-Forwarded-For": "1.2.3.4, 10.20.4.37:52311"}
        self.assertEqual(access.client_address(headers, "127.0.0.1", True), "10.20.4.37")
        self.assertEqual(access.client_address(headers, "127.0.0.1", False), "127.0.0.1")
        self.assertEqual(access.client_address({"X-Forwarded-For": "[fe80::1]:443"}, "127.0.0.1", True), "fe80::1")

    def test_browser(self):
        self.assertEqual(access.browser_of("Mozilla/5.0 ... Chrome/128.0 Safari/537.36 Edg/128.0"), "Edge")
        self.assertEqual(access.browser_of("Mozilla/5.0 ... Firefox/131.0"), "Firefox")

    def test_verification_reports_workstation_tests(self):
        s = SimulatedSystem(delay=0)
        a = answers()
        pending = [c for c in verify(s, a)["checks"] if c["category"] == "Test depuis un poste utilisateur"]
        self.assertEqual((pending[0]["status"], pending[0]["command"]),
                         ("warning", "https://heptagoneslm.entreprise.local/acces"))
        entry = access.make_entry("mmartin", "Marc Martin", "NTLM", "10.1.1.9", "PC-042", "Chrome", True)
        s.write_text(a.path("logs", access.FILE_NAME), json.dumps(entry) + "\n")
        rows = [c for c in verify(s, a)["checks"] if c["id"] == "access:mmartin"]
        self.assertEqual(rows[0]["status"], "warning")
        self.assertIn("zone Intranet", rows[0]["detail"])
        self.assertIn("NTLM", rows[0]["detail"])

    def test_simulated_install_shows_a_received_test(self):
        s = SimulatedSystem(delay=0)
        s.features = {k: True for k in s.features}
        s.modules = {k: True for k in s.modules}
        a = answers()
        inst = Installer(s, a)
        inst.start(); inst.wait(30)
        v = verify(s, a)
        self.assertEqual(v["access_url"], "https://heptagoneslm.entreprise.local/acces")
        self.assertIn("access:jdupont", {c["id"] for c in v["checks"]})


class ModuleTest(unittest.TestCase):
    def test_module_forwards_method_and_strips_client_values(self):
        cs = iis.USER_MODULE_CS
        self.assertIn('const string MethodHeader = "X-Remote-Auth-Method";', cs)
        self.assertIn("req.Headers.Remove(MethodHeader);", cs)
        self.assertIn("NTLMSSP", cs)
        self.assertNotIn("$\"", cs)          # C# 5 : pas de chaînes interpolées


class AccessPageTest(_ServerCase):
    web = {"auth": {"mode": "header"}}

    def setUp(self):
        self.logdir = tempfile.TemporaryDirectory()
        self.cfg.log_file = os.path.join(self.logdir.name, "heptagoneslm.log")

    def tearDown(self):
        self.logdir.cleanup()

    def test_page_requires_authentication(self):
        self.assertEqual(self.request("GET", "/acces")[0], 403)
        status, r, _ = self.request("GET", "/acces", headers={"X-Remote-User": "ENT\\jdupont"})
        self.assertEqual(status, 200)
        self.assertIn("text/html", r.getheader("Content-Type"))

    def test_info_and_method_remembered_per_user(self):
        user = {"X-Remote-User": "ENT\\kmethod", "X-Forwarded-For": "10.20.4.37"}
        self.request("GET", "/", headers={**user, "X-Remote-Auth-Method": "Kerberos"})
        status, _, body = self.request("GET", "/api/access-test", headers=user)
        info = json.loads(body)
        self.assertEqual((status, info["user"], info["method"], info["client"]), (200, "kmethod", "Kerberos", "10.20.4.37"))
        _, _, body = self.request("GET", "/api/access-test", headers={"X-Remote-User": "ENT\\autre"})
        self.assertEqual(json.loads(body)["method"], "Windows (méthode non déterminée)")

    def test_record_requires_custom_header(self):
        user = {"X-Remote-User": "ENT\\jdupont"}
        self.assertEqual(self.request("POST", "/api/access-test", {"prompted": False}, headers=user)[0], 403)
        self.assertEqual(self.request("POST", "/api/access-test", {"prompted": False},
                                      headers={"X-HeptagonesLM": "1"})[0], 401)      # non authentifié
        status, _, body = self.request("POST", "/api/access-test", {"prompted": False},
                                       headers={**user, "X-HeptagonesLM": "1"})
        self.assertEqual((status, json.loads(body)["entry"]["prompted"]), (200, False))
        saved = access.parse_results(access.results_path(self.cfg.log_file).read_text(encoding="utf-8"))
        self.assertEqual(saved[-1]["user"], "jdupont")
