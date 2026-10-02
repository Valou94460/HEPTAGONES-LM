"""Assistant simulé avec une installation 26.3.0 existante (réglages personnalisés) : test du bouton « Mettre à jour ».

    python tests/ui/update_harness.py 8793     (puis node tests/ui/setup_update.cjs captures/)
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from heptagoneslm.setup.installer import ANSWERS_FILE  # noqa: E402
from heptagoneslm.setup.server import make_server  # noqa: E402
from tests.test_setup import answers, ready_system  # noqa: E402
from tests.test_update import customize, install  # noqa: E402

s, a = ready_system(), answers()
s.delay = 0.15                                   # progression visible dans le navigateur
install(s, a)
customize(s, a)
saved = json.loads(s.read_text(a.path(ANSWERS_FILE)))
saved["_version"] = "26.3.0"                     # installation plus ancienne que l'assistant
s.write_text(a.path(ANSWERS_FILE), json.dumps(saved))
httpd, _app = make_server(s, int(sys.argv[1]) if len(sys.argv) > 1 else 8793, token="upd")
print("prêt", flush=True)
httpd.serve_forever()
