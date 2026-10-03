"""26.3.2 : mise à jour en place et conservation des réglages personnalisés de heptagoneslm.yaml."""

import io
import json
import unittest

import yaml

from heptagoneslm import __version__
from heptagoneslm.config import parse_config
from heptagoneslm.setup.installer import ANSWERS_FILE, Installer, build_update_plan, merge_user_settings
from heptagoneslm.setup.server import SetupApp
from heptagoneslm.setup.unattended import run_update
from tests.test_setup import answers, ready_system

CUSTOM = """
products:
  - {pattern: "87545ACD_*", product: AutoCAD, publisher: Autodesk, annual_cost: 2300}
sites:
  - {pattern: "PC-LYO*", site: Lyon}
retention: {snapshots_days: 30, events_days: 365}
"""


def install(s, a):
    inst = Installer(s, a)
    inst.start()
    inst.wait(30)
    assert inst.status == "done", inst.error
    return inst


def customize(s, a, extra_web_roles=True):
    """L'exploitant complète la configuration à la main après l'installation."""
    cfg = yaml.safe_load(s.read_text(a.config_path))
    cfg.update(yaml.safe_load(CUSTOM))
    cfg["reporting"]["currency"] = "CHF"
    if extra_web_roles:
        cfg["web"]["roles"] = {"admins": ["ENTREPRISE\\GG_Admins"], "readers": []}
    s.write_text(a.config_path, yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
    parse_config(cfg)
    return cfg


class MergeTest(unittest.TestCase):
    def test_merge_keeps_manual_settings_but_wizard_answers_win(self):
        a = answers(email_enabled=True, smtp_host="smtp.entreprise.local", sender="h@entreprise.local",
                    recipients=["dsi@entreprise.local"], autodesk_enabled=True)
        new = a.config_dict()
        old = yaml.safe_load(CUSTOM)
        old["autodesk"] = {"annual_costs": {"AutoCAD": 2300}}
        old["email"] = {"smtp_host": "ancien.smtp", "subscriptions": [
            {"name": "Direction", "recipients": ["a@x.fr"], "period": "last-month"},
            {"name": "Achats", "recipients": ["b@x.fr"], "period": "last-month"}]}
        old["alerts"] = {"recipients": ["ancien@x.fr"], "types": ["denial"]}
        merged, kept = merge_user_settings(new, old)
        self.assertEqual(merged["products"][0]["product"], "AutoCAD")
        self.assertEqual(merged["retention"]["snapshots_days"], 30)
        self.assertEqual(merged["autodesk"]["annual_costs"], {"AutoCAD": 2300})
        self.assertEqual(len(merged["email"]["subscriptions"]), 2)           # abonnements ajoutés à la main
        self.assertEqual(merged["email"]["smtp_host"], "smtp.entreprise.local")  # réponse de l'assistant
        self.assertEqual(merged["alerts"]["recipients"], ["dsi@entreprise.local"])
        self.assertEqual(merged["alerts"]["types"], ["denial"])
        self.assertIn("products", kept)
        parse_config(merged)

    def test_reinstall_keeps_custom_settings(self):
        s, a = ready_system(), answers()
        install(s, a)
        customize(s, a)
        second = install(s, a)
        cfg = yaml.safe_load(s.read_text(a.config_path))
        self.assertEqual(cfg["products"][0]["product"], "AutoCAD")
        self.assertEqual(cfg["sites"][0]["site"], "Lyon")
        self.assertEqual(cfg["reporting"]["currency"], "CHF")
        self.assertEqual(cfg["web"]["roles"]["admins"], ["ENTREPRISE\\GG_Admins"])
        self.assertIn("Réglages personnalisés conservés", second.report_text())
        parse_config(cfg)


class UpdateTest(unittest.TestCase):
    def test_update_plan_needs_no_password(self):
        a = answers(service_password="", db_password="")
        self.assertEqual([st.id for st in build_update_plan(a)],
                         ["backup", "binaries", "schema", "watchdog", "start", "collect", "shortcut"])

    def test_update_keeps_configuration_and_records_version(self):
        s, a = ready_system(), answers()
        install(s, a)
        customize(s, a)
        before = s.read_text(a.config_path)
        saved = json.loads(s.read_text(a.path(ANSWERS_FILE)))
        saved["_version"] = "26.3.0"                                          # installation plus ancienne
        s.write_text(a.path(ANSWERS_FILE), json.dumps(saved))

        app = SetupApp(s, token="t")
        calls_before = len(s.calls)
        self.assertEqual(app.installed_version(a.config_path), "26.3.0")
        app.start_update(a.config_path)
        app.installer.wait(30)
        st = app.installer.state()
        self.assertEqual(st["status"], "done", st["error"])
        self.assertEqual([x["id"] for x in st["steps"]],
                         ["backup", "binaries", "schema", "watchdog", "start", "collect", "shortcut"])
        self.assertEqual(s.read_text(a.config_path), before)                 # configuration intacte
        self.assertEqual(json.loads(s.read_text(a.path(ANSWERS_FILE)))["_version"], __version__)
        backups = [p.name for p in s._real(a.path("backup")).rglob("*")]
        self.assertIn("heptagoneslm.yaml", backups)
        self.assertIn(ANSWERS_FILE, backups)
        self.assertIn("Mise à jour terminée", app.installer.report_text())
        update_calls = " ".join(s.calls[calls_before:])
        registered = [c for c in s.calls[calls_before:] if c.startswith("register_task:")]
        self.assertEqual(registered, ["register_task:HeptagonesLM - Surveillance de l'interface"])  # sous SYSTEM
        self.assertTrue(s.exists(r"C:\Users\Public\Desktop\HeptagonesLM.url"))
        self.assertNotIn("configure_iis_site", update_calls)
        self.assertEqual(app.installed_version(a.config_path), __version__)

    def test_silent_update(self):
        s, a = ready_system(), answers()
        out = io.StringIO()
        self.assertEqual(run_update(s, out=out), 1)
        self.assertIn("Aucune installation", out.getvalue())
        install(s, a)
        out = io.StringIO()
        code = run_update(s, out=out)
        self.assertIn(code, (0, 2), out.getvalue())
        self.assertIn("Mise à jour terminée", out.getvalue())

    def test_old_installation_without_answers_file(self):
        s, a = ready_system(), answers()
        install(s, a)
        s.write_text(a.path(ANSWERS_FILE), "")
        self.assertEqual(SetupApp(s, token="t").installed_version(a.config_path), "antérieure à 26.3.1")



class RestartTest(unittest.TestCase):
    """Après un redémarrage du serveur : interface relancée, tâches lancées même sur batterie."""

    def _scripts(self, spec):
        from heptagoneslm.setup.system import Result, WindowsSystem

        seen = {}
        w = WindowsSystem.__new__(WindowsSystem)
        w.ps = lambda script, **kw: seen.update(script=script, **kw) or Result(0, "", "")
        w.register_task(spec, "ENTREPRISE\\svc_heptagoneslm", "")
        return seen

    def test_watchdog_runs_as_system_without_password(self):
        from heptagoneslm.setup.installer import watchdog_spec

        seen = self._scripts(watchdog_spec(answers()))
        self.assertIn("-UserId 'SYSTEM' -LogonType ServiceAccount", seen["script"])
        self.assertNotIn("-Password", seen["script"])
        self.assertIn("-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries", seen["script"])
        self.assertIn("Start-ScheduledTask", seen["script"])
        self.assertIn("-ne ''Disabled''", seen["script"])            # tâche désactivée à la main : laissée telle quelle

    def test_shortcut_without_public_address(self):
        from heptagoneslm.setup.installer import shortcut_text

        self.assertIsNone(shortcut_text(answers(web_mode="iis", public_host="")))
        self.assertIn("URL=http://localhost:8765/", shortcut_text(answers(web_mode="local")))


if __name__ == "__main__":
    unittest.main()
