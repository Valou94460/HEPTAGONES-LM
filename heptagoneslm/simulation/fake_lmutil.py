"""Faux ``lmutil`` pour tester HeptagonesLM sans serveur de licences.

Imite ``lmutil lmstat -a -c <port@host>`` pour les serveurs du scénario de
démonstration, à l'heure courante. Si la variable ``HEPTAGONESLM_SIM_DIR`` est
définie, le log debug des serveurs simulés qui en ont un est complété à
chaque appel (``<HEPTAGONESLM_SIM_DIR>/logs/<SERVEUR>.log``), ce qui permet aussi de
tester le collecteur ``log`` en conditions réelles.

``heptagoneslm demo`` génère un script d'appel (``fake_lmutil.bat`` / ``fake_lmutil``)
référencé dans la configuration de démonstration.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

from heptagoneslm.simulation.model import find_server, render_lmstat, sync_log


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] != "lmstat" or "-c" not in args:
        print("usage: lmutil lmstat -a -c port@host  (simulateur HeptagonesLM)", file=sys.stderr)
        return 1
    target = args[args.index("-c") + 1] if args.index("-c") + 1 < len(args) else ""
    server = find_server(target)
    now = datetime.now().replace(microsecond=0)
    if server is None:
        print("lmutil - Copyright (c) 1989-2023 Flexera. All Rights Reserved.")
        print(f"License server status: {target}")
        print('lmgrd is not running: Cannot connect to license server system. (-15,570:10061 "WinSock: Connection refused")')
        return 1
    sim_dir = os.environ.get("HEPTAGONESLM_SIM_DIR")
    if sim_dir and server.with_log:
        sync_log(server, Path(sim_dir) / "logs" / f"{server.name}.log", now)
    sys.stdout.write(render_lmstat(server, now))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
