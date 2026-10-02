"""Interface en ligne de commande de HeptagonesLM.

Exemples ::

    heptagoneslm -c heptagoneslm.yaml init-db
    heptagoneslm -c heptagoneslm.yaml collect                 # à planifier toutes les 5 min
    heptagoneslm -c heptagoneslm.yaml report --period last-month
    heptagoneslm -c heptagoneslm.yaml report --from 2024-09-01 --to 2024-10-01 --server AUTODESK
    heptagoneslm -c heptagoneslm.yaml import-log --server AUTODESK D:\\archives\\adsk_2024-08.log
    heptagoneslm parse-lmstat sortie_lmstat.txt         # diagnostic du parser, sans base
    heptagoneslm ddl --dialect mssql > 01_schema.sql
    heptagoneslm demo --days 30                         # environnement de démonstration simulé
    heptagoneslm -c heptagoneslm.yaml web --open              # interface web (rapports, exports)
    heptagoneslm web --demo --open                      # interface web sur données simulées

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

from heptagoneslm import __version__
from heptagoneslm.config import AppConfig, ConfigError, load_config

log = logging.getLogger("heptagoneslm")

DEFAULT_CONFIG = os.environ.get("HEPTAGONESLM_CONFIG", "heptagoneslm.yaml")


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
    from heptagoneslm.storage import Database

    return Database(cfg.database.connection_string)


def _period(args) -> tuple[datetime, datetime]:
    from heptagoneslm.reporting.dataset import period_bounds

    today = date.today()
    if args.date_from:
        start = datetime.combine(date.fromisoformat(args.date_from), datetime.min.time())
        end = (
            datetime.combine(date.fromisoformat(args.date_to), datetime.min.time())
            if args.date_to
            else datetime.combine(today + timedelta(days=1), datetime.min.time())
        )
        return start, end
    return period_bounds(args.period, today)


# ---------------------------------------------------------------------------
# Commandes
# ---------------------------------------------------------------------------


def cmd_init_db(cfg: AppConfig, args) -> int:
    from heptagoneslm.service import CollectionService
    from heptagoneslm.storage import create_schema

    with _open_db(cfg) as db:
        create_schema(db)
        CollectionService(cfg, db).sync_servers()
    log.info("Schéma créé / mis à jour, %d serveur(s) référencé(s)", len(cfg.servers))
    return 0


def cmd_collect(cfg: AppConfig, args) -> int:
    from heptagoneslm.service import CollectionService

    with _open_db(cfg) as db:
        if not db.try_lock("heptagoneslm_collect"):
            log.warning("Une collecte est déjà en cours : exécution ignorée")
            print("Une collecte est déjà en cours : exécution ignorée")
            return 0
        results = CollectionService(cfg, db).collect_all(only=args.server)
        if cfg.autodesk.import_dir and not args.server:
            _import_autodesk_folder(cfg, db)
        if cfg.alerts:
            _notify_alerts(cfg, db)
    failed = [r for r in results if r.status != "OK"]
    for r in results:
        print(f"{r.server:<20} {r.collector:<8} {r.status:<6} {r.items:>6}  {r.message or ''}")
    if not results:
        log.warning("Aucun serveur collecté (vérifier la configuration / --server)")
    return 2 if failed else 0


def _import_autodesk_folder(cfg: AppConfig, db) -> None:
    """Exports du portail Autodesk déposés dans le dossier surveillé : une erreur ne fait pas échouer la collecte."""
    from heptagoneslm.autodesk import import_folder

    try:
        results = import_folder(db, cfg.autodesk.import_dir)
    except Exception as exc:
        log.error("Import Autodesk impossible : %s (exécuter « init-db » après une mise à jour)", exc)
        return
    for r in results:
        if r["status"] != "déjà importé":
            print(f"Autodesk : {r['file']} {r['status']} {r.get('rows', 0)} licence(s) {r.get('error', '')}".rstrip())


def _notify_alerts(cfg: AppConfig, db) -> None:
    """Alertes e-mail après la collecte : une erreur d'envoi ne fait pas échouer la collecte."""
    from heptagoneslm.alerts import check_and_notify

    try:
        result = check_and_notify(cfg, db)
    except Exception as exc:
        log.error("Évaluation des alertes impossible : %s (exécuter « init-db » après une mise à jour)", exc)
        return
    if result.status in ("SENT", "ERROR"):
        print(f"Alertes : {result.status} {result.detail}")
        for t in result.tickets:
            print(f"  ticket : {t}")


def cmd_check_alerts(cfg: AppConfig, args) -> int:
    from heptagoneslm.alerts import check_and_notify

    if not cfg.alerts:
        print("Aucune alerte configurée (section alerts) : rien à évaluer.")
        return 0
    with _open_db(cfg) as db:
        result = check_and_notify(cfg, db, dry_run_dir=args.dry_run)
    print(f"Alertes : {result.status} {result.detail}")
    for a in result.alerts:
        print(f"  [{a.severity}] {a.title} — {a.detail}")
    for t in result.tickets:
        print(f"  ticket : {t}")
    return 2 if result.status == "ERROR" else 0


def cmd_test_itsm(cfg: AppConfig, args) -> int:
    from heptagoneslm.itsm import ItsmError, check_connector

    connectors = cfg.alerts.itsm if cfg.alerts else []
    if args.connector:
        connectors = [c for c in connectors if c.name.lower() == args.connector.lower()]
    if not connectors:
        print("Aucun connecteur ITSM correspondant (section alerts.itsm).")
        return 1
    failed = 0
    for c in connectors:
        try:
            steps = check_connector(c, cfg, resolve=not args.keep_open)
            print(f"{c.name:<16} OK     " + " ; ".join(steps))
        except ItsmError as exc:
            failed += 1
            print(f"{c.name:<16} ERREUR {exc}")
    return 2 if failed else 0


def cmd_report(cfg: AppConfig, args) -> int:
    from heptagoneslm.reporting import build_report_data
    from heptagoneslm.reporting.excel import write_excel_report

    start, end = _period(args)
    if end <= start:
        raise ConfigError("La fin de période doit être postérieure au début")
    output = args.output or os.path.join(
        cfg.reporting.output_dir, f"HeptagonesLM_{start:%Y%m%d}_{(end - timedelta(seconds=1)):%Y%m%d}.xlsx"
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
    from heptagoneslm.collectors.flexlm.logfile import FlexlmLogParser
    from heptagoneslm.service import CollectionService

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


def cmd_import_autodesk(cfg: AppConfig, args) -> int:
    from heptagoneslm.autodesk import ImportError_, import_report

    failed = 0
    with _open_db(cfg) as db:
        for file in args.files:
            try:
                r = import_report(db, file, force=args.force)
            except (ImportError_, OSError) as exc:
                failed += 1
                print(f"{file} : non importé : {exc}", file=sys.stderr)
                continue
            if r["status"] == "déjà importé":
                print(f"{r['file']} : déjà importé (--force pour réimporter)")
            else:
                print(f"{r['file']} : {r['rows']} licence(s) nommée(s), rapport du {r['report_date']}"
                      + (f", {r['skipped']} ligne(s) ignorée(s)" if r.get("skipped") else ""))
    return 2 if failed else 0


def cmd_parse_lmstat(args) -> int:
    from heptagoneslm.collectors.flexlm.lmstat import parse_lmstat

    text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    status = parse_lmstat(text)
    print(json.dumps(asdict(status), default=str, indent=2, ensure_ascii=False))
    return 0


def cmd_parse_log(args) -> int:
    from heptagoneslm.collectors.flexlm.logfile import FlexlmLogParser

    parser = FlexlmLogParser()
    with open(args.file, "rb") as fh:
        events = parser.parse_lines(raw.decode("utf-8", errors="replace") for raw in fh)
    for e in events:
        print(json.dumps(asdict(e), default=str, ensure_ascii=False))
    print(f"# {len(events)} évènements, {parser.skipped_undated} ignorés faute de date", file=sys.stderr)
    return 0


def cmd_test_server(cfg: AppConfig, args) -> int:
    from heptagoneslm.collectors import get_collector_class

    srv = cfg.server(args.server)
    status = get_collector_class(srv.license_type, "lmstat")(srv, cfg).poll()
    print(f"Serveur {srv.name} ({srv.port_at_host}) : UP={status.server_up} version={status.server_version}")
    for vendor, st in status.vendors.items():
        print(f"  démon {vendor}: {st}")
    for f in status.features:
        print(f"  {f.name:<35} {f.in_use:>4}/{f.issued if f.issued is not None else '-':<4} {len(f.checkouts)} checkout(s)")
    return 0


def cmd_import_users(cfg: AppConfig, args) -> int:
    from heptagoneslm.storage import Repository
    from heptagoneslm.users import import_users, read_users_csv

    users = read_users_csv(args.file)
    with _open_db(cfg) as db:
        with db.transaction():
            updated, created = import_users(Repository(db), users)
    print(f"{updated} utilisateur(s) mis à jour, {created} créé(s)")
    return 0


def cmd_send_report(cfg: AppConfig, args) -> int:
    from heptagoneslm.config import Subscription
    from heptagoneslm.mailer import deliver

    subs = cfg.email.subscriptions
    if args.subscription:
        wanted = {n.lower() for n in args.subscription}
        subs = [s for s in subs if s.name.lower() in wanted]
        if len(subs) != len(wanted):
            raise ConfigError(f"Abonnement inconnu (disponibles : {', '.join(s.name for s in cfg.email.subscriptions) or 'aucun'})")
    if not subs and args.to:  # envoi de test sans abonnement configuré
        subs = [Subscription(name="Test", recipients=args.to)]
    if not subs:
        print("Aucun abonnement e-mail configuré (section email.subscriptions) : rien à envoyer.")
        return 0
    if not args.dry_run and not cfg.email.smtp_host:
        raise ConfigError("email.smtp_host est obligatoire (ou utiliser --dry-run)")
    results = []
    with _open_db(cfg) as db:
        for sub in subs:
            results.append(deliver(cfg, db, sub, dry_run_dir=args.dry_run, override_to=args.to,
                                   skip_sent=args.skip_sent, period=args.period))
    for r in results:
        print(f"{r.subscription:<24} {r.status:<8} {r.detail}")
    return 2 if any(r.status == "ERROR" for r in results) else 0


def cmd_purge(cfg: AppConfig, args) -> int:
    from heptagoneslm.service import CollectionService

    with _open_db(cfg) as db:
        counts = CollectionService(cfg, db).purge()
    print(", ".join(f"{k}: {v}" for k, v in counts.items()))
    return 0


def cmd_apply_products(cfg: AppConfig, args) -> int:
    from heptagoneslm.service import CollectionService

    with _open_db(cfg) as db:
        n = CollectionService(cfg, db).apply_product_mapping()
    print(f"{n} feature(s) mise(s) à jour")
    return 0


def cmd_demo(args) -> int:
    from heptagoneslm.simulation.demo import format_summary, run_demo

    result = run_demo(
        output_dir=args.output_dir,
        days=args.days,
        connection_string=args.connection_string,
        poll_minutes=args.poll_minutes,
        make_report=not args.no_report,
    )
    print(format_summary(result))
    print(
        "\nPour continuer en direct avec le faux lmutil :\n"
        f"  heptagoneslm -c \"{result.config_path}\" collect\n"
        f"  heptagoneslm -c \"{result.config_path}\" report --period this-month"
    )
    return 0


def demo_home() -> Path:
    """Répertoire de la démo utilisée par ``heptagoneslm web --demo`` (et le .exe sans configuration)."""
    base = os.environ.get("LOCALAPPDATA") or os.path.join(Path.home(), ".local", "share")
    return Path(base) / "HeptagonesLM" / "demo"


def cmd_web(args) -> int:
    from heptagoneslm.web.server import serve

    if args.demo:
        from heptagoneslm.simulation.demo import run_demo

        from heptagoneslm import __version__
        from heptagoneslm.storage.schema import SCHEMA_VERSION

        home = demo_home()
        db = home / "heptagoneslm_demo.db"
        marker = home / "demo.version"
        stamp = f"{__version__} schéma {SCHEMA_VERSION}"
        # Régénérée chaque jour, et après une mise à jour (nouvelles tables, nouvelles données simulées)
        fresh = (db.exists() and date.fromtimestamp(db.stat().st_mtime) == date.today()
                 and marker.exists() and marker.read_text(encoding="utf-8").strip() == stamp)
        if not fresh:
            print(f"Préparation des données de démonstration ({args.demo_days} jours simulés)…")
            run_demo(home, days=args.demo_days, make_report=False)
            marker.write_text(stamp, encoding="utf-8")
        cfg = load_config(home / "heptagoneslm.demo.yaml")
        print("Mode démonstration : données simulées (Autodesk, Ansys, MATLAB).")
    else:
        cfg = load_config(args.config)
    _setup_logging("DEBUG" if args.verbose else cfg.log_level, None)
    serve(cfg, host=args.host, port=args.port, open_browser=args.open)
    return 0


def cmd_setup(args) -> int:
    from heptagoneslm.setup.system import SetupError, make_system

    _setup_logging("DEBUG" if args.verbose else "WARNING", None)
    try:
        system = make_system(args.simulate)
    except SetupError as exc:
        print(exc, file=sys.stderr)
        return 1
    if args.answers:
        from heptagoneslm.setup.unattended import run

        return run(system, args.answers)
    from heptagoneslm.setup.server import serve

    serve(system, port=args.port, open_browser=not args.no_open, token=args.token)
    return 0


def cmd_ddl(args) -> int:
    from heptagoneslm.storage.schema import render_script

    sys.stdout.write(render_script(args.dialect))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="heptagoneslm", description="Suivi et reporting de licences FlexLM/FlexNet")
    p.add_argument("-c", "--config", default=DEFAULT_CONFIG, help=f"fichier de configuration (défaut : {DEFAULT_CONFIG})")
    p.add_argument("-v", "--verbose", action="store_true", help="logs détaillés (DEBUG)")
    p.add_argument("--version", action="version", version=f"HeptagonesLM {__version__}")
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

    iu = sub.add_parser("import-users", help="importe nom complet et service des utilisateurs (CSV AD/RH)")
    iu.add_argument("file", help="CSV avec colonnes username;display_name;department (ou utilisateur;nom;service)")

    ia = sub.add_parser("import-autodesk",
                        help="importe un export « Usage report » du portail Autodesk Account (licences nommées)")
    ia.add_argument("files", nargs="+", help="export CSV ou Excel (manage.autodesk.com › Reporting › Usage report)")
    ia.add_argument("--force", action="store_true", help="réimporter un fichier déjà importé")

    sr = sub.add_parser("send-report", help="envoie le rapport par e-mail aux abonnés (à planifier)")
    sr.add_argument("--subscription", action="append", help="limiter à cet abonnement (répétable)")
    sr.add_argument("--period", choices=["last-month", "this-month", "last-week", "last-7d", "last-year"],
                    help="période (défaut : celle de l'abonnement)")
    sr.add_argument("--to", action="append", help="remplacer les destinataires (test) ; répétable")
    sr.add_argument("--skip-sent", action="store_true", help="ne pas renvoyer un rapport déjà envoyé pour la période")
    sr.add_argument("--dry-run", metavar="DOSSIER", help="écrire les messages en .eml au lieu de les envoyer")

    ca = sub.add_parser("check-alerts", help="évalue les alertes et les envoie par e-mail (aussi fait après chaque collecte)")
    ca.add_argument("--dry-run", metavar="DOSSIER", help="écrire le message en .eml sans l'envoyer ni mémoriser l'envoi")

    ti = sub.add_parser("test-itsm", help="ouvre, commente et résout un ticket de test dans chaque outil ITSM configuré")
    ti.add_argument("--connector", help="limiter à ce connecteur (nom)")
    ti.add_argument("--keep-open", action="store_true", help="laisser le ticket de test ouvert")

    sub.add_parser("purge", help="supprime les données au-delà des durées de rétention")
    sub.add_parser("apply-products", help="réapplique le mapping feature -> produit à tout l'historique")

    dm = sub.add_parser("demo", help="environnement de démonstration : simule des serveurs et génère un rapport")
    dm.add_argument("--days", type=int, default=30, help="nombre de jours d'historique simulé (défaut : 30)")
    dm.add_argument("--output-dir", default="demo", help="répertoire de sortie (défaut : ./demo)")
    dm.add_argument("--connection-string", help="base cible (défaut : SQLite dans le répertoire de sortie)")
    dm.add_argument("--poll-minutes", type=int, default=5, help="intervalle de sondage simulé")
    dm.add_argument("--no-report", action="store_true", help="ne pas générer le rapport Excel")

    st = sub.add_parser("setup", help="assistant d'installation sur un serveur Windows (IIS, base, tâches, vérification)")
    st.add_argument("--simulate", action="store_true", help="démonstration : aucune modification du système")
    st.add_argument("--answers", metavar="FICHIER.json", help="installation silencieuse à partir d'un fichier de réponses")
    st.add_argument("--port", type=int, default=0, help="port local de l'assistant (défaut : libre)")
    st.add_argument("--no-open", action="store_true", help="ne pas ouvrir le navigateur")
    st.add_argument("--token", help=argparse.SUPPRESS)

    w = sub.add_parser("web", help="interface web : rapports à la demande et exports")
    w.add_argument("--host", help="adresse d'écoute (défaut : web.host, sinon poste local uniquement)")
    w.add_argument("--port", type=int, help="port (défaut : web.port, sinon 8765)")
    w.add_argument("--open", action="store_true", help="ouvrir le navigateur")
    w.add_argument("--demo", action="store_true", help="utiliser des données de démonstration simulées")
    w.add_argument("--demo-days", type=int, default=90, help=argparse.SUPPRESS)

    d = sub.add_parser("ddl", help="affiche le script SQL de création du schéma")
    d.add_argument("--dialect", choices=["mssql", "sqlite"], default="mssql")
    return p


NO_CONFIG = {"parse-lmstat": cmd_parse_lmstat, "parse-log": cmd_parse_log, "ddl": cmd_ddl, "demo": cmd_demo}
WITH_CONFIG = {
    "init-db": cmd_init_db,
    "collect": cmd_collect,
    "report": cmd_report,
    "import-log": cmd_import_log,
    "test-server": cmd_test_server,
    "import-users": cmd_import_users,
    "import-autodesk": cmd_import_autodesk,
    "send-report": cmd_send_report,
    "check-alerts": cmd_check_alerts,
    "test-itsm": cmd_test_itsm,
    "purge": cmd_purge,
    "apply-products": cmd_apply_products,
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["fake-lmutil"]:  # faux lmutil de la démo (utilisé par le .exe)
        from heptagoneslm.simulation.fake_lmutil import main as fake_main

        return fake_main(argv[1:])
    args = build_parser().parse_args(argv)
    try:
        if args.command == "web":
            return cmd_web(args)
        if args.command == "setup":
            return cmd_setup(args)
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
