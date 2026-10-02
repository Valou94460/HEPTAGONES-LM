"""Non-régression sur des sorties réelles de serveurs de licences (tests/fixtures/reel).

Chaque sortie enregistrée par la recette sur le site d'un client est rejouée : l'analyseur de
HeptagonesLM doit retrouver les jetons émis et utilisés lus directement dans la sortie brute, les
sessions et le nombre d'utilisateurs validés lors de la recette (fichier ``.attendu.json``).
"""

import json
import unittest
from pathlib import Path

from heptagoneslm.config import ServerConfig
from heptagoneslm.setup.recette import parse_output, read_totals

REEL = Path(__file__).resolve().parent / "fixtures" / "reel"


def cases() -> list[tuple[Path, dict]]:
    out = []
    for txt in sorted(REEL.glob("*.txt")):
        expected = txt.with_name(txt.stem + ".attendu.json")
        if expected.is_file():
            out.append((txt, json.loads(expected.read_text(encoding="utf-8"))))
    return out


class SortiesReellesTest(unittest.TestCase):
    def test_fixtures_present(self):
        self.assertGreaterEqual(len(cases()), 2)
        orphans = [p.name for p in REEL.glob("*.txt") if not p.with_name(p.stem + ".attendu.json").is_file()]
        self.assertEqual(orphans, [], "sortie sans résumé attendu (.attendu.json)")

    def test_replay(self):
        for txt, exp in cases():
            with self.subTest(sortie=txt.name):
                text = txt.read_text(encoding="utf-8")
                srv = ServerConfig(name=exp["server"], port_at_host=exp["port_at_host"],
                                   license_type=exp.get("license_type", "flexlm"))
                status = parse_output(text, srv)
                self.assertTrue(status.server_up, status.errors)
                parsed = {f.name: f for f in status.features}
                # Lecture indépendante de la sortie brute : stable dans le temps
                totals = read_totals(text, srv.license_type)
                self.assertEqual({k: {"issued": v["issued"], "in_use": v["in_use"]} for k, v in totals.items()},
                                 {k: {"issued": v["issued"], "in_use": v["in_use"]} for k, v in exp["features"].items()})
                for name, want in exp["features"].items():
                    self.assertIn(name, parsed, f"feature {name} non trouvée par l'analyseur")
                    f = parsed[name]
                    self.assertEqual(f.issued, want["issued"], f"{name} : jetons émis")
                    self.assertEqual(f.in_use, want["in_use"], f"{name} : jetons utilisés")
                    self.assertEqual(sum(c.count or 1 for c in f.checkouts), want["sessions"], f"{name} : sessions")
                users = {(c.user.lower(), c.host.lower()) for f in status.features for c in f.checkouts}
                self.assertEqual(len(users), exp["users"], "utilisateurs connectés")


if __name__ == "__main__":
    unittest.main()
