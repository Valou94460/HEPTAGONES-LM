import http.client
import importlib.util
import json
import os
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime, timedelta

from heptagoneslm.config import ConfigError, load_config, parse_config
from heptagoneslm.simulation.demo import run_demo
from heptagoneslm.web.auth import Auth, AuthError, LoginThrottle, SessionStore, UserInfo
from heptagoneslm.web.server import check_security, make_server

HAS_LDAP3 = importlib.util.find_spec("ldap3") is not None
MONDAY = datetime(2024, 9, 23)
LDAP_AUTH = {"mode": "ldap", "ldap_servers": ["ldaps://dc01.test.local"], "base_dn": "DC=test,DC=local",
             "upn_suffix": "test.local", "max_failures": 3, "lockout_minutes": 1}


class FakeDirectory:
    """Annuaire factice : jdupont / Secret-1."""

    def authenticate(self, username, password):
        if username == "jdupont" and password == "Secret-1":
            return UserInfo("jdupont", "Jean Dupont", "Bureau d'études", ["GG_HeptagonesLM"])
        raise AuthError("Identifiant ou mot de passe incorrect")


def config_with(tmp_config, web):
    raw = {"database": {"connection_string": tmp_config.database.connection_string},
           "servers": [{"name": "X", "port_at_host": "1@x"}], "web": web}
    return parse_config(raw)


class _ServerCase(unittest.TestCase):
    web: dict = {}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        demo = run_demo(cls.tmp.name, days=3, end=MONDAY + timedelta(days=3), make_report=False)
        cls.cfg = config_with(load_config(demo.config_path), cls.web)
        cls.auth = Auth(cls.cfg.web.auth, authenticator=FakeDirectory() if cls.cfg.web.auth.mode == "ldap" else None)
        cls.httpd = make_server(cls.cfg, port=0, auth=cls.auth)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        payload = json.dumps(body).encode() if body is not None else None
        hdrs = {"Content-Type": "application/json"} if payload else {}
        hdrs.update(headers or {})
        conn.request(method, path, body=payload, headers=hdrs)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, r, data


class LdapModeTest(_ServerCase):
    web = {"auth": LDAP_AUTH}

    def login(self, username="jdupont", password="Secret-1"):
        return self.request("POST", "/api/login", {"username": username, "password": password})

    def test_protected_without_session(self):
        status, _, body = self.request("GET", "/api/meta")
        self.assertEqual(status, 401)
        self.assertTrue(json.loads(body)["login"])
        status, r, _ = self.request("GET", "/")
        self.assertEqual((status, r.getheader("Location")), (302, "login.html"))

    def test_public_login_page(self):
        for path in ("/login.html", "/login.js", "/style.css"):
            self.assertEqual(self.request("GET", path)[0], 200, path)

    def test_login_logout_flow(self):
        status, r, body = self.login()
        self.assertEqual(status, 200, body)
        cookie = r.getheader("Set-Cookie")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        token = cookie.split(";", 1)[0]
        status, _, body = self.request("GET", "/api/me", headers={"Cookie": token})
        self.assertEqual((status, json.loads(body)["display_name"]), (200, "Jean Dupont"))
        self.assertEqual(self.request("GET", "/", headers={"Cookie": token})[0], 200)
        with self.assertLogs("heptagoneslm.audit", "INFO") as logs:
            status, _, _ = self.request("GET", "/api/export?type=summary&format=csv&start=2024-09-23&end=2024-09-25",
                                        headers={"Cookie": token})
        self.assertEqual(status, 200)
        self.assertTrue(any("Export summary (csv) par jdupont" in m for m in logs.output))
        self.request("POST", "/api/logout", headers={"Cookie": token})
        self.assertEqual(self.request("GET", "/api/meta", headers={"Cookie": token})[0], 401)

    def test_bad_password_and_lockout(self):
        self.auth.throttle = LoginThrottle(3, 1)
        for _ in range(3):
            status, _, body = self.login(password="faux")
            self.assertEqual(status, 401)
            self.assertIn("incorrect", json.loads(body)["error"])
        status, _, body = self.login()  # bon mot de passe, mais compte bloqué
        self.assertEqual(status, 401)
        self.assertIn("Trop de tentatives", json.loads(body)["error"])
        self.auth.throttle = LoginThrottle(3, 1)

    def test_forged_cookie(self):
        self.assertEqual(self.request("GET", "/api/meta", headers={"Cookie": "heptagoneslm_session=forge"})[0], 401)

    def test_security_headers(self):
        _, r, _ = self.request("GET", "/login.html")
        self.assertEqual(r.getheader("X-Frame-Options"), "DENY")
        self.assertIn("frame-ancestors 'none'", r.getheader("Content-Security-Policy"))


class HeaderModeTest(_ServerCase):
    web = {"auth": {"mode": "header", "allowed_users": ["jdupont", "asmith"]}}

    def test_trusted_header(self):
        status, _, body = self.request("GET", "/api/me", headers={"X-Remote-User": "TEST\\JDupont"})
        self.assertEqual((status, json.loads(body)["user"]), (200, "jdupont"))

    def test_missing_or_unlisted_user(self):
        self.assertEqual(self.request("GET", "/api/meta")[0], 401)
        self.assertEqual(self.request("GET", "/api/meta", headers={"X-Remote-User": "intrus"})[0], 401)
        self.assertEqual(self.request("GET", "/", headers={"X-Remote-User": "intrus"})[0], 403)

    def test_untrusted_proxy_ignored(self):
        self.auth.cfg.trusted_proxies = ["10.0.0.1"]
        try:
            self.assertEqual(self.request("GET", "/api/meta", headers={"X-Remote-User": "jdupont"})[0], 401)
        finally:
            self.auth.cfg.trusted_proxies = ["127.0.0.1", "::1"]


class SecurityConfigTest(unittest.TestCase):
    base = {"database": {"connection_string": "sqlite:///:memory:"}, "servers": []}

    def test_ldap_requires_https_on_network(self):
        cfg = parse_config({**self.base, "web": {"auth": LDAP_AUTH}})
        with self.assertRaises(ConfigError):
            check_security(cfg, "0.0.0.0")
        check_security(cfg, "127.0.0.1")  # poste local : autorisé
        cfg = parse_config({**self.base, "web": {"auth": LDAP_AUTH, "allow_insecure_http": True}})
        check_security(cfg, "0.0.0.0")

    def test_invalid_auth_config(self):
        for web in ({"auth": {"mode": "kerberos"}}, {"auth": {"mode": "ldap", "base_dn": "DC=x"}},
                    {"auth": {"mode": "ldap", "ldap_servers": ["ldaps://x"], "base_dn": "DC=x"}},
                    {"tls_cert": "a.pem"}):
            with self.assertRaises(ConfigError, msg=web):
                parse_config({**self.base, "web": web})

    def test_session_store_expiry(self):
        store = SessionStore(hours=-1)  # sessions déjà expirées
        s = store.create(UserInfo("u"))
        self.assertIsNone(store.get(s.token))


@unittest.skipUnless(shutil.which("openssl"), "openssl absent")
class HttpsTest(unittest.TestCase):
    def test_https_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            cert, key = os.path.join(tmp, "c.pem"), os.path.join(tmp, "k.pem")
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                            "-subj", "/CN=localhost", "-keyout", key, "-out", cert],
                           check=True, capture_output=True)
            cfg = parse_config({"database": {"connection_string": f"sqlite:///{os.path.join(tmp, 'x.db')}"},
                                "servers": [], "web": {"tls_cert": cert, "tls_key": key}})
            httpd = make_server(cfg, port=0)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            try:
                ctx = ssl.create_default_context(cafile=cert)
                ctx.check_hostname = False
                conn = http.client.HTTPSConnection("127.0.0.1", httpd.server_address[1], context=ctx, timeout=10)
                conn.request("GET", "/login.html")
                r = conn.getresponse()
                self.assertEqual(r.status, 200)
                self.assertIn("max-age", r.getheader("Strict-Transport-Security"))
            finally:
                httpd.shutdown()
                httpd.server_close()


@unittest.skipUnless(HAS_LDAP3, "ldap3 non installé")
class LdapAuthenticatorTest(unittest.TestCase):
    """Vrai code LDAP contre l'annuaire fictif de ldap3 (MOCK_SYNC)."""

    def setUp(self):
        import ldap3

        from heptagoneslm.config import AuthConfig
        from heptagoneslm.web.auth import LdapAuthenticator

        self.server = ldap3.Server("fake_dc", get_info=ldap3.OFFLINE_AD_2012_R2)
        setup = ldap3.Connection(self.server, user="CN=svc,DC=test,DC=local", password="svc-pw",
                                 client_strategy=ldap3.MOCK_SYNC)
        base = "DC=test,DC=local"
        setup.strategy.add_entry(f"CN=svc,{base}", {"userPassword": "svc-pw", "objectClass": "person", "sAMAccountName": "svc"})
        setup.strategy.add_entry(f"CN=GG_HeptagonesLM,OU=Groupes,{base}", {"objectClass": "group", "cn": "GG_HeptagonesLM"})
        setup.strategy.add_entry(f"CN=Bureau etudes,OU=Groupes,{base}",
                                 {"objectClass": "group", "cn": "Bureau etudes", "memberOf": [f"CN=GG_HeptagonesLM,OU=Groupes,{base}"]})
        setup.strategy.add_entry(f"CN=Jean Dupont,OU=Users,{base}", {
            "userPassword": "Secret-1", "objectClass": "person", "sAMAccountName": "jdupont",
            "displayName": "Jean Dupont", "department": "CAO", "memberOf": [f"CN=Bureau etudes,OU=Groupes,{base}"]})
        setup.strategy.add_entry(f"CN=Anna Smith,OU=Users,{base}", {
            "userPassword": "Secret-2", "objectClass": "person", "sAMAccountName": "asmith", "displayName": "Anna Smith"})
        self.make = lambda groups: LdapAuthenticator(
            AuthConfig(mode="ldap", ldap_servers=["ldap://fake"], base_dn=base, bind_dn=f"CN=svc,{base}",
                       bind_password="svc-pw", allowed_groups=groups),
            server=self.server, client_strategy=ldap3.MOCK_SYNC)

    def test_valid_user_with_nested_group(self):
        user = self.make(["GG_HeptagonesLM"]).authenticate("TEST\\jdupont", "Secret-1")
        self.assertEqual((user.username, user.display_name, user.department), ("jdupont", "Jean Dupont", "CAO"))
        self.assertIn("GG_HeptagonesLM", user.groups)   # via le groupe imbriqué « Bureau etudes »

    def test_wrong_password_unknown_user_empty_password(self):
        auth = self.make([])
        for u, p in (("jdupont", "faux"), ("inconnu", "x"), ("jdupont", "")):
            with self.assertRaises(AuthError):
                auth.authenticate(u, p)

    def test_group_restriction(self):
        with self.assertRaises(AuthError) as ctx:
            self.make(["GG_HeptagonesLM"]).authenticate("asmith", "Secret-2")
        # message générique pour l'utilisateur, cause précise pour l'audit
        self.assertIn("non autorisé", str(ctx.exception))
        self.assertIn("groupes autorisés", ctx.exception.reason)
        self.assertEqual(self.make([]).authenticate("asmith", "Secret-2").username, "asmith")


if __name__ == "__main__":
    unittest.main()
