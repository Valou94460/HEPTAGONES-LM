"""Interface HeptagonesLM en mode « ldap » avec un annuaire factice (jdupont / Secret-1),
pour tester la page de connexion dans un navigateur :

    python tests/ui/fake_auth_server.py 8766
"""

import sys
import tempfile
from datetime import datetime, timedelta

from heptagoneslm.config import load_config, parse_config
from heptagoneslm.simulation.demo import run_demo
from heptagoneslm.web.auth import Auth, AuthError, UserInfo
from heptagoneslm.web.server import make_server


class FakeDirectory:
    def authenticate(self, username, password):
        username = username.split("\\")[-1].lower()
        if username == "jdupont" and password == "Secret-1":
            return UserInfo("jdupont", "Jean Dupont", "Bureau d'études")
        raise AuthError("Identifiant ou mot de passe incorrect")


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
    tmp = tempfile.mkdtemp()
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    demo = run_demo(tmp, days=14, end=now, make_report=False)
    raw = load_config(demo.config_path)
    cfg = parse_config({
        "database": {"connection_string": raw.database.connection_string},
        "servers": [{"name": s.name, "port_at_host": s.port_at_host} for s in raw.servers],
        "web": {"auth": {"mode": "ldap", "ldap_servers": ["ldaps://fake"], "base_dn": "DC=fake",
                         "upn_suffix": "fake.local"}},
        "logging": {"file": f"{tmp}/logs/heptagoneslm.log"},     # tests d'accès (/acces) hors du dépôt
    })
    httpd = make_server(cfg, port=port, auth=Auth(cfg.web.auth, authenticator=FakeDirectory()))
    print(f"prêt sur le port {httpd.server_address[1]}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
