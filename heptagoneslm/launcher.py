"""Point d'entrée de l'exécutable Windows ``HeptagonesLM.exe``.

* Avec des arguments : se comporte exactement comme la commande ``heptagoneslm``
  (``HeptagonesLM.exe -c heptagoneslm.yaml collect``, ``HeptagonesLM.exe report``…), ce qui
  permet de l'utiliser aussi dans le Planificateur de tâches.
* ``HeptagonesLM-Setup.exe`` (même programme, construit avec élévation administrateur) :
  sans argument, ouvre l'assistant d'installation.
* Sans argument (double-clic) : ouvre l'interface de l'installation trouvée (variable
  HEPTAGONESLM_CONFIG, ``heptagoneslm.yaml`` dans le dossier courant, à côté de l'exécutable ou
  dans ``C:\\HeptagonesLM``) ; si elle tourne déjà (tâche « Interface web »), ouvre simplement le
  navigateur. Sans installation, n'ouvre jamais la démonstration sans le dire : propose
  d'installer (assistant, avec élévation) ou de voir la démonstration, clairement signalée.
"""

from __future__ import annotations

import os
import socket
import sys
import webbrowser
from pathlib import Path

from heptagoneslm.cli import main as cli_main


def find_config() -> Path | None:
    candidates = [os.environ.get("HEPTAGONESLM_CONFIG"), "heptagoneslm.yaml",
                  Path(sys.executable).resolve().parent / "heptagoneslm.yaml",
                  Path(r"C:\HeptagonesLM\heptagoneslm.yaml")]
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
    if config:
        url = running_interface(config)
        if url:                              # interface déjà servie (tâche planifiée, IIS)
            print(f"HeptagonesLM est installé : ouverture de {url}")
            webbrowser.open(url)
            return 0
        argv = ["-c", str(config), "web", "--open"]
    else:
        argv = choose_without_install()
        if argv is None:
            return 0
    code = cli_main(argv)
    if code:
        input("\nAppuyez sur Entrée pour fermer…")
    return code


def running_interface(config: Path) -> str | None:
    """Adresse de l'interface si elle répond déjà sur ce poste (sinon None)."""
    try:
        from heptagoneslm.config import load_config

        web = load_config(config).web
    except Exception:
        return None
    try:
        with socket.create_connection(("127.0.0.1", web.port), timeout=1):
            pass
    except OSError:
        return None
    return web.public_url or f"{'https' if web.tls_cert else 'http'}://localhost:{web.port}"


def choose_without_install() -> list[str] | None:
    print("HeptagonesLM n'est pas installé sur ce poste (aucun fichier heptagoneslm.yaml trouvé).\n")
    print("  1. Installer HeptagonesLM (assistant d'installation, droits d'administrateur)")
    print("  2. Voir une démonstration (données fictives, aucun serveur de licences interrogé)")
    print("  3. Quitter\n")
    choice = input("Votre choix [1] : ").strip() or "1"
    if choice == "1":
        return run_setup_elevated()
    if choice == "2":
        return ["web", "--demo", "--open"]
    return None


def run_setup_elevated() -> list[str] | None:
    """Relance cet exécutable en administrateur sur l'assistant ; hors Windows, l'assistant directement."""
    if os.name == "nt":
        import ctypes

        if not ctypes.windll.shell32.IsUserAnAdmin():
            rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, "setup", None, 1)
            if rc <= 32:
                print("Élévation refusée : lancer HeptagonesLM-Setup.exe, ou ce programme en administrateur.")
                input("\nAppuyez sur Entrée pour fermer…")
            return None
    return ["setup"]

if __name__ == "__main__":
    sys.exit(main())
