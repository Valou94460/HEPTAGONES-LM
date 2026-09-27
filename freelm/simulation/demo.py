"""Environnement de démonstration : rejoue N jours d'activité simulée dans une
base FreeLM, puis génère le rapport Excel.

Tout passe par le vrai code de FreeLM : les sorties lmstat et les logs sont
produits au format texte FlexNet, analysés par les parsers, puis appliqués par
``CollectionService`` exactement comme en production. Seule la source (le
simulateur à la place des serveurs de licences) et l'horloge (accélérée)
diffèrent.

Produits dans le répertoire de sortie :

* ``freelm_demo.db``      base SQLite (sauf si une chaîne SQL Server est fournie)
* ``logs/ANSYS.log``      log debug simulé du serveur Ansys
* ``freelm.demo.yaml``    configuration prête pour continuer « en direct »
* ``fake_lmutil[.bat]``   faux lmutil appelé par ``freelm collect``
* ``reports/*.xlsx``      rapport Excel de la période simulée
"""

from __future__ import annotations

import importlib.util
import logging
import os
import shutil
import stat
import sys
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path

import yaml

import freelm
from freelm.collectors.flexlm.lmstat import parse_lmstat
from freelm.collectors.flexlm.logfile import read_log_incremental
from freelm.config import AppConfig, parse_config
from freelm.reporting.dataset import ReportData, build_report_data
from freelm.service import CollectionService
from freelm.simulation.model import (
    DEFAULT_SCENARIO, USERS, SimServer, department_of, display_name_of, render_lmstat, sync_log,
)
from freelm.storage import Database, create_schema
from freelm.users import import_users, read_users_csv

log = logging.getLogger(__name__)


@dataclass
class DemoResult:
    start: datetime
    end: datetime
    config_path: Path
    report_path: Path | None
    data: ReportData
    counts: dict[str, int] = field(default_factory=dict)


def _write_fake_lmutil(output_dir: Path) -> Path:
    """Script d'appel du faux lmutil (chemin absolu de Python et du package)."""
    package_root = Path(freelm.__file__).resolve().parent.parent
    python = Path(sys.executable).resolve()
    # Exécutable autonome (PyInstaller) : le .exe sert lui-même de faux lmutil.
    if getattr(sys, "frozen", False):
        command = f'"{python}" fake-lmutil'
    else:
        command = f'"{python}" -m freelm.simulation.fake_lmutil'
    if os.name == "nt":
        path = output_dir / "fake_lmutil.bat"
        path.write_text(
            "@echo off\r\n"
            f'set "FREELM_SIM_DIR={output_dir}"\r\n'
            f'set "PYTHONPATH={package_root};%PYTHONPATH%"\r\n'
            f"{command} %*\r\n"
            "exit /b %ERRORLEVEL%\r\n",
            encoding="utf-8",
        )
    else:
        path = output_dir / "fake_lmutil"
        path.write_text(
            "#!/bin/sh\n"
            f'export FREELM_SIM_DIR="{output_dir}"\n'
            f'export PYTHONPATH="{package_root}${{PYTHONPATH:+:$PYTHONPATH}}"\n'
            f'exec {command} "$@"\n',
            encoding="utf-8",
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def demo_config_dict(output_dir: Path, connection_string: str, lmutil: Path, scenario=DEFAULT_SCENARIO) -> dict:
    servers, products = [], []
    for srv in scenario:
        entry = {
            "name": srv.name,
            "port_at_host": srv.port_at_host,
            "collectors": ["lmstat", "log"] if srv.with_log else ["lmstat"],
            "sessions_from": "log" if srv.with_log else "lmstat",
        }
        if srv.with_log:
            entry["log_path"] = str(output_dir / "logs" / f"{srv.name}.log")
        servers.append(entry)
        for f in srv.features:
            products.append({"pattern": f.name, "product": f.product, "publisher": f.publisher})
    return {
        "database": {"connection_string": connection_string},
        "lmutil_path": str(lmutil),
        "logging": {"file": str(output_dir / "logs" / "freelm.log"), "level": "INFO"},
        "servers": servers,
        "products": products,
        "reporting": {"output_dir": str(output_dir / "reports"), "top_n_chart": 10, "business_hours": [8, 19]},
        "retention": {"snapshots_days": 90, "events_days": 730},
    }


def _reset_demo_data(db: Database, server_ids: list[int]) -> None:
    """Supprime les données déjà présentes pour les serveurs de démonstration."""
    marks = ", ".join("?" for _ in server_ids)
    features = f"SELECT id FROM feature WHERE server_id IN ({marks})"
    with db.transaction():
        for table in ("license_session", "usage_snapshot", "usage_hourly"):
            db.execute(f"DELETE FROM {table} WHERE feature_id IN ({features})", server_ids)
        for table in ("license_event", "collection_run", "collector_state", "feature"):
            db.execute(f"DELETE FROM {table} WHERE server_id IN ({marks})", server_ids)


def _simulate_day(
    svc: CollectionService, cfg: AppConfig, server_ids: dict[str, int], sim: SimServer,
    day: datetime, day_end: datetime, poll: timedelta, log_dir: Path, first: datetime,
) -> int:
    srv_cfg = cfg.server(sim.name)
    sid = server_ids[sim.name]
    items = 0
    t = day
    while t < day_end:
        status = parse_lmstat(render_lmstat(sim, t), sim.port_at_host, queried_at=t)
        items += svc.apply_status(sid, status, "lmstat", srv_cfg.sessions_from == "lmstat")
        t += poll
    if sim.with_log:
        path = log_dir / f"{sim.name}.log"
        sync_log(sim, path, day_end, start=first)
        events, state = read_log_incremental(str(path), svc.repo.get_state(sid, "log"))
        items += svc.apply_events(sid, events, "log", srv_cfg.sessions_from == "log")
        svc.repo.save_state(sid, "log", state)
    run_id = svc.repo.start_run(sid, "simulation", day)
    svc.repo.finish_run(run_id, "OK", items, None)
    return items


def run_demo(
    output_dir: str | Path = "demo",
    days: int = 30,
    connection_string: str | None = None,
    poll_minutes: int = 5,
    end: datetime | None = None,
    make_report: bool = True,
) -> DemoResult:
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    log_dir = output_dir / "logs"
    if log_dir.exists():
        shutil.rmtree(log_dir)
    log_dir.mkdir()

    if connection_string is None:
        db_file = output_dir / "freelm_demo.db"
        if db_file.exists():
            db_file.unlink()
        connection_string = f"sqlite:///{db_file}"

    poll = timedelta(minutes=poll_minutes)
    if end is None:
        now = datetime.now().replace(second=0, microsecond=0)
        end = now - timedelta(minutes=now.minute % poll_minutes)
    start = datetime.combine((end - timedelta(days=days)).date(), time())

    lmutil = _write_fake_lmutil(output_dir)
    cfg_dict = demo_config_dict(output_dir, connection_string, lmutil)
    config_path = output_dir / "freelm.demo.yaml"
    config_path.write_text(
        "# Configuration de démonstration générée par `freelm demo`\n"
        + yaml.safe_dump(cfg_dict, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    cfg = parse_config(cfg_dict)

    with Database(connection_string) as db:
        create_schema(db)
        svc = CollectionService(cfg, db)
        server_ids = svc.sync_servers()
        _reset_demo_data(db, list(server_ids.values()))

        # Référentiel utilisateurs (comme un export AD) : noms complets et services.
        users_csv = output_dir / "users.csv"
        users_csv.write_text(
            "username;display_name;department\n"
            + "".join(f"{u};{display_name_of(u)};{department_of(u)}\n" for u in USERS),
            encoding="utf-8",
        )
        with db.transaction():
            import_users(svc.repo, read_users_csv(users_csv))

        log.info("Simulation du %s au %s (sondage toutes les %d min)", f"{start:%d/%m/%Y}", f"{end:%d/%m/%Y %H:%M}", poll_minutes)
        day = start
        while day < end:
            day_end = min(day + timedelta(days=1), end)
            with db.transaction():
                for sim in DEFAULT_SCENARIO:
                    _simulate_day(svc, cfg, server_ids, sim, day, day_end, poll, log_dir, start)
            if day.weekday() == 6 or day_end == end:
                log.info("  simulé jusqu'au %s", f"{day_end:%d/%m/%Y %H:%M}")
            day = day_end

        counts = {
            table: int(db.scalar(f"SELECT COUNT(*) FROM {table}"))
            for table in ("feature", "app_user", "usage_snapshot", "usage_hourly", "license_session", "license_event")
        }
        data = build_report_data(db, start, end, business_hours=cfg.reporting.business_hours, now=end)

    report_path = None
    if make_report:
        if importlib.util.find_spec("openpyxl") is None:
            log.warning("openpyxl non installé : rapport Excel non généré (pip install openpyxl)")
        else:
            from freelm.reporting.excel import write_excel_report

            report_path = write_excel_report(
                data, output_dir / "reports" / f"FreeLM_demo_{start:%Y%m%d}_{end:%Y%m%d}.xlsx",
                top_n=cfg.reporting.top_n_chart,
            )
    return DemoResult(start, end, config_path, report_path, data, counts)


def format_summary(result: DemoResult) -> str:
    """Résumé texte affiché en fin de démonstration."""
    lines = [
        f"Période simulée : {result.start:%d/%m/%Y} -> {result.end:%d/%m/%Y %H:%M}",
        "Volumétrie : " + ", ".join(f"{k}={v}" for k, v in result.counts.items()),
        "",
        f"{'Serveur':<9} {'Produit':<32} {'Émis':>4} {'Pic':>4} {'Moy.':>5} {'Taux':>6} {'Ouvré':>6} {'Satur.':>6} {'Refus':>5} {'Util.':>5} {'Heures':>7}",
    ]

    def pct(v):
        return f"{v:6.1%}" if v is not None else "     -"

    for s in sorted(result.data.summaries, key=lambda s: (s.server, s.label)):
        lines.append(
            f"{s.server:<9} {s.label[:32]:<32} {s.issued or 0:>4} {s.peak_in_use:>4} {s.avg_in_use:>5.2f} "
            f"{pct(s.avg_utilization)} {pct(s.business_utilization)} {s.saturated_hours:>6} {s.denials:>5} "
            f"{s.distinct_users:>5} {s.usage_hours:>7.1f}"
        )
    lines += [
        "",
        f"Configuration « en direct » : {result.config_path}",
        f"Rapport Excel               : {result.report_path or 'non généré'}",
    ]
    return "\n".join(lines)
