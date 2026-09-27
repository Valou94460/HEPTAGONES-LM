"""Interface en ligne de commande de FreeLM.

Exemples ::

    freelm -c freelm.yaml init-db
    freelm -c freelm.yaml collect                 # à planifier toutes les 5 min
    freelm -c freelm.yaml report --period last-month
    freelm -c freelm.yaml report --from 2024-09-01 --to 2024-10-01 --server AUTODESK
    freelm -c freelm.yaml import-log --server AUTODESK D:\\archives\\adsk_2024-08.log
    freelm parse-lmstat sortie_lmstat.txt         # diagnostic du parser, sans base
    freelm ddl --dialect mssql > 01_schema.sql

Codes retour : 0 = succès, 1 = erreur, 2 = collecte partiellement en échec.
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import sys
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path

from freelm import __version__
from freelm.config import AppConfig, ConfigError, load_config

log = logging.getLogger("freelm")

DEFAULT_CONFIG = os.environ.get("FREELM_CONFIG", "freelm.yaml")


def _setup_logging(level: str, log_file: str | None) -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s - %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)


def _open_db(cfg: AppConfig):
    from freelm.storage import Database

    return Database(cfg.database.connection_string)


def _period(args) -> tuple[datetime, datetime]:
    from freelm.reporting import previous_month

    today = date.today()
    if args.date_from:
        start = datetime.combine(date.fromisoformat(args.date_from), datetime.min.time())
        end = (
            datetime.combine(date.fromisoformat(args.date_to), datetime.min.time())
            if args.date_to
            else datetime.combine(today + timedelta(days=1), datetime.min.time())
        )
        return start, end
    midnight = datetime.combine(today, datetime.min.time())
    if args.period == "this-month":
        return midnight.replace(day=1), midnight + timedelta(days=1)
    if args.period == "last-week":
        monday = midnight - timedelta(days=today.weekday())
        return monday - timedelta(days=7), monday
    if args.period == "last-7d":
        return midnight - timedelta(days=7), midnight
    if args.period == "last-year":
        return midnight.replace(year=today.year - 1, month=1, day=1), midnight.replace(month=1, day=1)
    return previous_month(today)


# ---------------------------------------------------------------------------
# Commandes
# ---------------------------------------------------------------------------


def cmd_init_db(cfg: AppConfig, args) -> int:
    from freelm.service import CollectionService
    from freelm.storage import create_schema

    with _open_db(cfg) as db:
        create_schema(db)
        CollectionService(cfg, db).sync_servers()
    log.info("Schéma créé / mis à jour, %d serveur(s) référencé(s)", len(cfg.servers))
    return 0


def cmd_collect(cfg: AppConfig, args) -> int:
    from freelm.service import CollectionService

    with _open_db(cfg) as db:
        results = CollectionService(cfg, db).collect_all(only=args.server)
    failed = [r for r in results if r.status != "OK"]
    for r in results:
        print(f"{r.server:<20} {r.collector:<8} {r.status:<6} {r.items:>6}  {r.message or ''}")
    if not results:
        log.warning("Aucun serveur collecté (vérifier la configuration / --server)")
    return 2 if failed else 0


def cmd_report(cfg: AppConfig, args) -> int:
    from freelm.reporting import build_report_data
    from freelm.reporting.excel import write_excel_report

    start, end = _period(args)
    if end <= start:
        raise ConfigError("La fin de période doit être postérieure au début")
    output = args.output or os.path.join(
        cfg.reporting.output_dir, f"FreeLM_{start:%Y%m%d}_{(end - timedelta(seconds=1)):%Y%m%d}.xlsx"
    )
    if args.skip_existing and os.path.exists(output):
        log.info("Rapport déjà présent, rien à faire : %s", output)
        return 0
    with _open_db(cfg) as db:
        data = build_report_data(db, start, end, servers=args.server, business_hours=cfg.reporting.business_hours)
    path = write_excel_report(data, output, top_n=cfg.reporting.top_n_chart)
    log.info("Rapport généré : %s (%d features, %d sessions)", path, len(data.summaries), len(data.sessions))
    print(path)
    return 0


def cmd_import_log(cfg: AppConfig, args) -> int:
    from freelm.collectors.flexlm.logfile import FlexlmLogParser
    from freelm.service import CollectionService

    srv = cfg.server(args.server)
    parser = FlexlmLogParser()
    with _open_db(cfg) as db:
        service = CollectionService(cfg, db)
        server_id = service.server_id(srv)
        total = 0
        for file in args.files:
            with open(file, "rb") as fh:
                lines = (raw.decode("utf-8", errors="replace").rstrip("\r\n") for raw in fh)
                events = parser.parse_lines(lines)
            with db.transaction():
                n = service.apply_events(server_id, events, "log", track_sessions=srv.sessions_from == "log")
            total += n
            log.info("%s : %d évènements lus, %d enregistrements", file, len(events), n)
        if parser.skipped_undated:
            log.warning("%d évènement(s) sans date ignoré(s)", parser.skipped_undated)
    print(f"{total} enregistrements importés")
    return 0


def cmd_parse_lmstat(args) -> int:
    from freelm.collectors.flexlm.lmstat import parse_lmstat

    text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    status = parse_lmstat(text)
    print(json.dumps(asdict(status), default=str, indent=2, ensure_ascii=False))
    return 0


def cmd_parse_log(args) -> int:
    from freelm.collectors.flexlm.logfile import FlexlmLogParser

    parser = FlexlmLogParser()
    with open(args.file, "rb") as fh:
        events = parser.parse_lines(raw.decode("utf-8", errors="replace") for raw in fh)
    for e in events:
        print(json.dumps(asdict(e), default=str, ensure_ascii=False))
    print(f"# {len(events)} évènements, {parser.skipped_undated} ignorés faute de date", file=sys.stderr)
    return 0


def cmd_test_server(cfg: AppConfig, args) -> int:
    from freelm.collectors import get_collector_class

    srv = cfg.server(args.server)
    status = get_collector_class(srv.license_type, "lmstat")(srv, cfg).poll()
    print(f"Serveur {srv.name} ({srv.port_at_host}) : UP={status.server_up} version={status.server_version}")
    for vendor, st in status.vendors.items():
        print(f"  démon {vendor}: {st}")
    for f in status.features:
        print(f"  {f.name:<35} {f.in_use:>4}/{f.issued if f.issued is not None else '-':<4} {len(f.checkouts)} checkout(s)")
    return 0


def cmd_purge(cfg: AppConfig, args) -> int:
    from freelm.service import CollectionService

    with _open_db(cfg) as db:
        counts = CollectionService(cfg, db).purge()
    print(", ".join(f"{k}: {v}" for k, v in counts.items()))
    return 0


def cmd_apply_products(cfg: AppConfig, args) -> int:
    from freelm.service import CollectionService

    with _open_db(cfg) as db:
        n = CollectionService(cfg, db).apply_product_mapping()
    print(f"{n} feature(s) mise(s) à jour")
    return 0


def cmd_ddl(args) -> int:
    from freelm.storage.schema import render_script

    sys.stdout.write(render_script(args.dialect))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="freelm", description="Suivi et reporting de licences FlexLM/FlexNet")
    p.add_argument("-c", "--config", default=DEFAULT_CONFIG, help=f"fichier de configuration (défaut : {DEFAULT_CONFIG})")
    p.add_argument("-v", "--verbose", action="store_true", help="logs détaillés (DEBUG)")
    p.add_argument("--version", action="version", version=f"FreeLM {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="crée ou met à jour le schéma de la base")

    c = sub.add_parser("collect", help="exécute une collecte (à planifier)")
    c.add_argument("--server", action="append", help="limiter à ce serveur (répétable)")

    r = sub.add_parser("report", help="génère le rapport Excel")
    r.add_argument("--period", default="last-month",
                   choices=["last-month", "this-month", "last-week", "last-7d", "last-year"])
    r.add_argument("--from", dest="date_from", help="début AAAA-MM-JJ (prioritaire sur --period)")
    r.add_argument("--to", dest="date_to", help="fin exclue AAAA-MM-JJ (défaut : demain)")
    r.add_argument("--server", action="append", help="limiter à ce serveur (répétable)")
    r.add_argument("-o", "--output", help="chemin du fichier .xlsx")
    r.add_argument("--skip-existing", action="store_true",
                   help="ne rien faire si le fichier existe déjà (tâche quotidienne -> rapport mensuel)")

    i = sub.add_parser("import-log", help="importe des logs FlexLM archivés (reprise d'historique)")
    i.add_argument("--server", required=True)
    i.add_argument("files", nargs="+", help="fichiers de log, dans l'ordre chronologique")

    pl = sub.add_parser("parse-lmstat", help="affiche le résultat du parsing d'une sortie lmstat (diagnostic)")
    pl.add_argument("file")
    pg = sub.add_parser("parse-log", help="affiche les évènements extraits d'un log FlexLM (diagnostic)")
    pg.add_argument("file")

    t = sub.add_parser("test-server", help="interroge un serveur via lmstat sans rien écrire")
    t.add_argument("--server", required=True)

    sub.add_parser("purge", help="supprime les données au-delà des durées de rétention")
    sub.add_parser("apply-products", help="réapplique le mapping feature -> produit à tout l'historique")

    d = sub.add_parser("ddl", help="affiche le script SQL de création du schéma")
    d.add_argument("--dialect", choices=["mssql", "sqlite"], default="mssql")
    return p


NO_CONFIG = {"parse-lmstat": cmd_parse_lmstat, "parse-log": cmd_parse_log, "ddl": cmd_ddl}
WITH_CONFIG = {
    "init-db": cmd_init_db,
    "collect": cmd_collect,
    "report": cmd_report,
    "import-log": cmd_import_log,
    "test-server": cmd_test_server,
    "purge": cmd_purge,
    "apply-products": cmd_apply_products,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command in NO_CONFIG:
            _setup_logging("DEBUG" if args.verbose else "INFO", None)
            return NO_CONFIG[args.command](args)
        cfg = load_config(args.config)
        _setup_logging("DEBUG" if args.verbose else cfg.log_level, cfg.log_file)
        return WITH_CONFIG[args.command](cfg, args)
    except ConfigError as exc:
        print(f"Erreur de configuration : {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # dernier filet pour la tâche planifiée
        log.exception("Échec de la commande %s : %s", args.command, exc)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
