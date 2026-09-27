"""Installation silencieuse : ``freelm setup --answers reponses.json``.

Mêmes contrôles, étapes et vérification que l'assistant graphique, avec une
progression en mode texte. Les valeurs ``${VARIABLE}`` du fichier sont remplacées
par les variables d'environnement (mots de passe hors du fichier).
Codes retour : 0 succès, 1 échec de l'installation, 2 installé avec des erreurs de vérification.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

from freelm.setup.answers import Answers
from freelm.setup.checks import apply_fix, blocking, run_checks
from freelm.setup.installer import Installer
from freelm.setup.system import SetupError, System
from freelm.setup.verify import verify

SYMBOL = {"ok": "OK ", "warning": "AVT", "error": "ERR", "info": " - "}


def _expand(value):
    if isinstance(value, str):
        return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, list):
        return [_expand(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    return value


def load_answers(path: str) -> Answers:
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return Answers.from_dict(_expand(raw))


def _bar(pct: float, width: int = 30) -> str:
    full = int(width * pct / 100)
    return "[" + "#" * full + "-" * (width - full) + f"] {pct:5.1f} %"


def run(system: System, answers_path: str, out=sys.stdout) -> int:
    p = lambda *a: print(*a, file=out, flush=True)
    try:
        a = load_answers(answers_path)
    except (OSError, ValueError) as exc:
        p(f"Fichier de réponses illisible : {exc}")
        return 1
    errors = a.validate()
    if errors:
        p("Réponses invalides :")
        for e in errors:
            p(f"  - {e['field']} : {e['message']}")
        return 1

    p(f"== Prérequis ({system.name})")
    checks = run_checks(system, a)
    for c in checks:
        p(f"  [{SYMBOL[c.status]}] {c.category} · {c.label} : {c.detail}")
    fixes = list(dict.fromkeys(c.fix for c in blocking(checks) if c.fix))
    if fixes and a.auto_fix:
        for fix in fixes:
            p(f"  Correction automatique : {fix} …")
            try:
                p("   ", apply_fix(system, fix))
            except SetupError as exc:
                p(f"    échec : {exc}")
        checks = run_checks(system, a)
    blockers = blocking(checks)
    if blockers:
        p("Prérequis bloquants : " + ", ".join(c.label for c in blockers)
          + ("" if a.auto_fix else " (\"auto_fix\": true pour les installer automatiquement)"))
        return 1

    p("== Installation")
    inst = Installer(system, a)
    inst.start()
    seen, last_bar = 0, ""
    while True:
        st = inst.state(seen)
        for line in st["logs"]:
            if line["level"] != "debug":
                p(f"  {line['t']} {line['msg']}")
        seen = st["log_count"]
        bar = _bar(st["percent"])
        if bar != last_bar and st["status"] == "running":
            p(f"  {bar}  {st['current'] or ''}")
            last_bar = bar
        if st["status"] in ("done", "failed"):
            break
        time.sleep(0.5)
    if inst.status == "failed":
        p(f"ÉCHEC : {inst.error}")
        return 1
    p(f"  {_bar(100)}  Installation terminée")

    p("== Vérification")
    v = verify(system, a)
    for c in v["checks"]:
        p(f"  [{SYMBOL[c['status']]}] {c['category']} · {c['label']} : {c['detail']}")
    p(f"Résultat : {v['counts']['ok']} conforme(s), {v['counts']['warning']} avertissement(s), "
      f"{v['counts']['error']} erreur(s)" + (f" — {v['url']}" if v.get("url") else ""))
    return 0 if v["success"] else 2
