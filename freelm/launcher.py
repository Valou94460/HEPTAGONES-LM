"""Point d'entrée de l'exécutable Windows ``FreeLM.exe``.

* Avec des arguments : se comporte exactement comme la commande ``freelm``
  (``FreeLM.exe -c freelm.yaml collect``, ``FreeLM.exe report``…), ce qui
  permet de l'utiliser aussi dans le Planificateur de tâches.
* ``FreeLM-Setup.exe`` (même programme, construit avec élévation administrateur) :
  sans argument, ouvre l'assistant d'installation.
* Sans argument (double-clic) : ouvre l'interface web dans le navigateur, sur
  la configuration trouvée (variable FREELM_CONFIG, ``freelm.yaml`` dans le
  dossier courant ou à côté de l'exécutable), sinon sur des données de
  démonstration.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from freelm.cli import main as cli_main


def find_config() -> Path | None:
    candidates = [os.environ.get("FREELM_CONFIG"), "freelm.yaml",
                  Path(sys.executable).resolve().parent / "freelm.yaml"]
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    return None


def main() -> int:
    if len(sys.argv) > 1:
        return cli_main()
    if "setup" in Path(sys.executable).stem.lower():
        code = cli_main(["setup"])
        if code:
            input("\nAppuyez sur Entrée pour fermer…")
        return code
    config = find_config()
    argv = ["-c", str(config), "web", "--open"] if config else ["web", "--demo", "--open"]
    code = cli_main(argv)
    if code:
        input("\nAppuyez sur Entrée pour fermer…")
    return code


if __name__ == "__main__":
    sys.exit(main())
