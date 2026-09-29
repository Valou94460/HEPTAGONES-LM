"""Rôles de l'interface : résolution, périmètre, et filtrage réel par le serveur HTTP."""

import copy
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from heptagoneslm.config import ConfigError, load_config, parse_config
from heptagoneslm.simulation.demo import run_demo
from heptagoneslm.web import api, roles
from heptagoneslm.web.auth import Auth, UserInfo
from heptagoneslm.web.server import make_server

MONDAY = datetime(2024, 9, 23)
SIMU = "Simulation & calcul"
ROLES = {
    "admins": ["ENTREPRISE\\GG_HLM_Admins"],
    "readers": ["GG_HLM_Lecteurs", "direction.achats"],
    "departments": [
        {"group": "CN=GG_Resp_Simulation,OU=Groupes,DC=entreprise,DC=local", "departments": [SIMU]},
        {"user": "chef.methodes", "departments": ["Méthodes industrialisation"], "servers": ["AUTODESK"]},
    ],
}


def roles_cfg(extra=None):
    raw = {"database": {"connection_string": "sqlite:///:memory:"}, "servers": [],
           "web": {"roles": {**copy.deepcopy(ROLES), **(extra or {})}}}
    return parse_config(raw).web.roles


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.cfg = roles_cfg()

    def test_roles_by_group_and_user(self):
        cases = [
            (UserInfo("alice", groups=["ENTREPRISE\\GG_HLM_Admins", "ENTREPRISE\\GG_HLM_Lecteurs"]), "admin"),
            (UserInfo("bruno", groups=["GG_HLM_Lecteurs"]), "reader"),              # CN (connexion LDAP)
            (UserInfo("direction.achats"), "reader"),                                # compte individuel
            (UserInfo("chloe", groups=["ENTREPRISE\\gg_resp_simulation"]), "service"),   # casse et domaine ignorés
        ]
        for user, role in cases:
            self.assertEqual(roles.resolve(user, self.cfg, "header").role, role, user)
        scoped = roles.resolve(UserInfo("chloe", groups=["GG_Resp_Simulation"]), self.cfg, "ldap")
        self.assertEqual((scoped.departments, scoped.servers, scoped.global_view), ([SIMU], [], False))
        self.assertIn(SIMU, scoped.describe())

    def test_no_role(self):
        with self.assertRaises(roles.Forbidden):
            roles.resolve(UserInfo("inconnu", groups=["ENTREPRISE\\Utilisateurs du domaine"]), self.cfg, "header")
        self.assertEqual(roles.resolve(UserInfo("inconnu"), roles_cfg({"default": "reader"}), "header").role, "reader")
        self.assertEqual(roles.resolve(UserInfo("x"), None, "header").role, "reader")      # pas de section roles
        self.assertEqual(roles.resolve(UserInfo("local"), self.cfg, "none").role, "local")

    def test_union_of_scopes(self):
        cfg = roles_cfg({"departments": [
            {"group": "GG_A", "departments": ["A"], "servers": ["S1"]},
            {"group": "GG_B", "departments": ["B", "A"], "servers": ["S2"]},
            {"group": "GG_C", "departments": ["C"]},                     # tous serveurs
        ]})
        a = roles.resolve(UserInfo("u", groups=["GG_A", "GG_B"]), cfg, "ldap")
        self.assertEqual((a.departments, a.servers), (["A", "B"], ["S1", "S2"]))
        a = roles.resolve(UserInfo("u", groups=["GG_A", "GG_C"]), cfg, "ldap")
        self.assertEqual((a.departments, a.servers), (["A", "C"], []))

    def test_restrict(self):
        access = roles.Access("service", [SIMU, "Autre"], ["ANSYS"])
        p = roles.restrict(api.parse_params({}), access)
        self.assertEqual((p.departments, p.servers, p.scoped), ([SIMU, "Autre"], ["ANSYS"], True))
        p = roles.restrict(api.parse_params({"department": [SIMU]}), access)
        self.assertEqual(p.departments, [SIMU])
        with self.assertRaises(roles.Forbidden):
            roles.restrict(api.parse_params({"department": ["Direction"]}), access)
        with self.assertRaises(roles.Forbidden):
            roles.restrict(api.parse_params({"server": ["MATLAB"]}), access)
        p = roles.restrict(api.parse_params({"department": ["Direction"]}), roles.Access("reader"))
        self.assertEqual((p.departments, p.scoped), (["Direction"], False))

    def test_config_validation(self):
        for bad in ({"departments": [{"departments": ["A"]}]},                       # ni group ni user
                    {"departments": [{"group": "G", "user": "u", "departments": ["A"]}]},
                    {"departments": [{"group": "G"}]},                              # sans service
                    {"default": "admin"}):
            with self.assertRaises(ConfigError, msg=bad):
                parse_config({"database": {"connection_string": "sqlite:///:memory:"}, "servers": [],
                              "web": {"roles": {**ROLES, **bad}}})
        with self.assertRaises(ConfigError):
            parse_config({"database": {"connection_string": "sqlite:///:memory:"}, "servers": [], "web": {"roles": {"default": "reader"}}})


class HeaderGroupsTest(unittest.TestCase):
    def test_groups_from_trusted_proxy_only(self):
        cfg = parse_config({"database": {"connection_string": "sqlite:///:memory:"}, "servers": [],
                            "web": {"auth": {"mode": "header"}}}).web.auth
        auth = Auth(cfg)
        headers = {"X-Remote-User": "ENTREPRISE\\Chloe", "X-Remote-Groups": "ENTREPRISE%5CGG_Resp_Simulation;ENTREPRISE%5CBureau%20d%27%C3%A9tudes"}
        user = auth.user_from_header(headers, "127.0.0.1")
        self.assertEqual(user.username, "chloe")
        self.assertEqual(user.groups, ["ENTREPRISE\\GG_Resp_Simulation", "ENTREPRISE\\Bureau d'études"])
        self.assertIsNone(auth.user_from_header(headers, "10.0.0.8"))            # pas le reverse proxy


class RolesHttpTest(unittest.TestCase):
    """Serveur réel (mode authentification unique) sur la base de démonstration."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        result = run_demo(cls.tmp.name, days=7, end=MONDAY + timedelta(days=7), make_report=False)
        cfg = load_config(result.config_path)
        raw_roles = parse_config({"database": {"connection_string": "sqlite:///:memory:"}, "servers": [],
                                  "web": {"auth": {"mode": "header"}, "roles": ROLES}}).web
        cfg.web.auth, cfg.web.roles = raw_roles.auth, raw_roles.roles
        cls.httpd = make_server(cfg, port=0)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.period = {"start": "2024-09-16", "end": "2024-09-29"}

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def get(self, path, user, groups=(), **query):
        q = urllib.parse.urlencode({**self.period, **query}, doseq=True)
        req = urllib.request.Request(f"{self.base}{path}?{q}", headers={
            "X-Remote-User": f"ENTREPRISE\\{user}",
            "X-Remote-Groups": ";".join(urllib.parse.quote(g) for g in groups)})
        try:
            with urllib.request.urlopen(req) as r:
                body = r.read()
                return r.status, (json.loads(body) if r.headers["Content-Type"].startswith("application/json") else body)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    RESP = ("chloe", ["ENTREPRISE\\GG_Resp_Simulation"])

    def test_reader_sees_everything(self):
        status, meta = self.get("/api/meta", "bruno", ["ENTREPRISE\\GG_HLM_Lecteurs"])
        self.assertEqual(status, 200)
        self.assertGreater(len(meta["departments"]), 1)
        self.assertEqual(meta["access"]["role"], "reader")

    def test_service_manager_sees_only_his_department(self):
        status, meta = self.get("/api/meta", *self.RESP)
        self.assertEqual((status, meta["departments"], meta["access"]["role"]), (200, [SIMU], "service"))
        self.assertTrue(meta["users"] and all(u["department"] == SIMU for u in meta["users"]))
        allowed = {u["username"] for u in meta["users"]}

        status, sessions = self.get("/api/report", *self.RESP, type="sessions")
        self.assertEqual(status, 200)
        self.assertTrue(sessions["rows"])
        self.assertEqual({r["department"] for r in sessions["rows"]}, {SIMU})

        status, breakdown = self.get("/api/report", *self.RESP, type="breakdown", dimension="department")
        self.assertEqual({r["key"] if "key" in r else r.get("label") for r in breakdown["rows"]} - {None}, {SIMU})

        status, denials = self.get("/api/report", *self.RESP, type="denials")
        self.assertTrue(all(r["username"] in allowed for r in denials["rows"]))

        status, dash = self.get("/api/report", *self.RESP, type="dashboard")
        self.assertEqual(status, 200)
        self.assertTrue(all(u["department"] == SIMU for u in dash.get("top_users", [])))

        status, live = self.get("/api/report", *self.RESP, type="live")
        users = [u for p in live["live"]["products"] for u in p["users"]]
        self.assertTrue(all(u["department"] == SIMU for u in users))

        # CSV des sessions : même filtrage
        status, csv = self.get("/api/export", *self.RESP, type="sessions", format="csv")
        self.assertEqual(status, 200)
        other = [d for d in ("Bureau d'études mécanique", "Méthodes industrialisation") if d.encode("utf-8") in csv]
        self.assertEqual(other, [])

    def test_out_of_scope_requests_are_refused(self):
        status, body = self.get("/api/report", *self.RESP, type="sessions", department="Méthodes industrialisation")
        self.assertEqual(status, 403)
        self.assertIn("hors de votre périmètre", body["error"])
        status, body = self.get("/api/export", *self.RESP, type="full", format="xlsx")
        self.assertEqual(status, 403)
        self.assertIn("vue globale", body["error"])

    def test_user_scope_with_server(self):
        status, meta = self.get("/api/meta", "chef.methodes")
        self.assertEqual((meta["servers"], meta["departments"]), (["AUTODESK"], ["Méthodes industrialisation"]))
        status, _ = self.get("/api/report", "chef.methodes", type="summary", server="ANSYS")
        self.assertEqual(status, 403)

    def test_no_role(self):
        status, body = self.get("/api/meta", "inconnu", ["ENTREPRISE\\Utilisateurs du domaine"])
        self.assertEqual(status, 403)
        self.assertIn("pas de rôle", body["error"])
        status, me = self.get("/api/me", "inconnu")
        self.assertEqual((status, me["access"]), (200, None))           # le test d'accès reste possible
        req = urllib.request.Request(self.base + "/acces", headers={"X-Remote-User": "ENTREPRISE\\inconnu"})
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.status, 200)


if __name__ == "__main__":
    unittest.main()
