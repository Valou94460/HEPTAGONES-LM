"""HeptagonesLM.exe lancé par double-clic : installation trouvée, interface déjà servie, ou choix explicite."""

import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from heptagoneslm import launcher


class LauncherTest(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(launcher.sys, "argv", ["HeptagonesLM.exe"])   # double-clic : aucun argument
        patch.start()
        self.addCleanup(patch.stop)
    def test_without_install_never_opens_demo_silently(self):
        with mock.patch.object(launcher, "find_config", return_value=None), \
             mock.patch("builtins.input", return_value="3"), \
             mock.patch.object(launcher, "cli_main") as cli:
            self.assertEqual(launcher.main(), 0)
        cli.assert_not_called()                                     # « Quitter » : rien n'est lancé

    def test_demo_only_on_explicit_choice(self):
        with mock.patch("builtins.input", return_value="2"):
            self.assertEqual(launcher.choose_without_install(), ["web", "--demo", "--open"])

    def test_install_choice_starts_setup(self):
        with mock.patch("builtins.input", return_value=""), mock.patch.object(launcher.os, "name", "posix"):
            self.assertEqual(launcher.choose_without_install(), ["setup"])      # choix par défaut : installer

    def test_running_interface_is_opened_not_restarted(self):
        with socket.socket() as srv, tempfile.TemporaryDirectory() as tmp:
            srv.bind(("127.0.0.1", 0)); srv.listen()
            port = srv.getsockname()[1]
            cfg = Path(tmp) / "heptagoneslm.yaml"
            cfg.write_text(f"database: {{connection_string: 'sqlite:///x.db'}}\nservers: []\n"
                           f"web: {{port: {port}, public_url: 'https://heptagoneslm.entreprise.local'}}\n", encoding="utf-8")
            self.assertEqual(launcher.running_interface(cfg), "https://heptagoneslm.entreprise.local")
            with mock.patch.object(launcher, "find_config", return_value=cfg), \
                 mock.patch.object(launcher.webbrowser, "open") as browser, \
                 mock.patch.object(launcher, "cli_main") as cli:
                self.assertEqual(launcher.main(), 0)
            browser.assert_called_once_with("https://heptagoneslm.entreprise.local")
            cli.assert_not_called()
        self.assertIsNone(launcher.running_interface(cfg))                  # fichier supprimé : rien

    def test_installed_config_is_a_candidate(self):
        with mock.patch.dict(launcher.os.environ, {"HEPTAGONESLM_CONFIG": ""}), \
             mock.patch.object(launcher.Path, "is_file", lambda p: str(p) == r"C:\HeptagonesLM\heptagoneslm.yaml"):
            self.assertEqual(str(launcher.find_config()), r"C:\HeptagonesLM\heptagoneslm.yaml")


if __name__ == "__main__":
    unittest.main()
