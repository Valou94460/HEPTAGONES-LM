"""Orchestration d'une collecte : exécute les collecteurs configurés et
applique leurs résultats en base.

Reconstruction des sessions
---------------------------
* Source ``lmstat`` (polling) : à chaque sondage, la liste des checkouts en
  cours est comparée aux sessions ouvertes en base. Un nouveau checkout ouvre
  une session (heure de début donnée par lmstat, précision minute) ; une
  session qui n'apparaît plus est fermée. L'heure de fin exacte n'est pas
  connue : elle est estimée au milieu de l'intervalle entre les deux sondages
  (plafonné à 30 min si la collecte a été interrompue).
* Source ``log`` : chaque ``OUT`` ouvre une session, chaque ``IN`` ferme la
  plus ancienne session ouverte pour le même (feature, utilisateur, poste).
  Un arrêt du démon ferme toutes ses sessions ouvertes. Précision : seconde.

Une seule source de sessions par serveur (paramètre ``sessions_from``) afin
d'éviter les doublons ; l'autre collecteur reste utile (lmstat fournit les
jetons émis/utilisés, le log fournit les refus DENIED).
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from heptagoneslm.collectors import CollectorError, get_collector_class
from heptagoneslm.config import AppConfig, ServerConfig
from heptagoneslm.models import EventType, LicenseEvent, ServerStatus
from heptagoneslm.storage.db import Database
from heptagoneslm.storage.repository import Repository

log = logging.getLogger(__name__)

#: Types d'évènements conservés dans license_event. OUT/IN ne sont pas
#: stockés individuellement : ils sont matérialisés sous forme de sessions.
PERSISTED_EVENT_TYPES = {
    EventType.DENIED,
    EventType.QUEUED,
    EventType.DEQUEUED,
    EventType.UNSUPPORTED,
    EventType.SERVER_DOWN,
}

_MAX_CHECKIN_ESTIMATE = timedelta(minutes=30)


@dataclass
class RunResult:
    server: str
    collector: str
    status: str  # "OK" | "ERROR"
    items: int = 0
    message: str | None = None


class CollectionService:
    def __init__(self, config: AppConfig, db: Database):
        self.config = config
        self.db = db
        self.repo = Repository(db)
        self._server_ids: dict[str, int] = {}

    # -- référentiel ------------------------------------------------------

    def sync_servers(self) -> dict[str, int]:
        """Reporte la liste des serveurs du fichier de config dans la table license_server."""
        with self.db.transaction():
            for srv in self.config.servers:
                self._server_ids[srv.name] = self.repo.upsert_server(
                    srv.name, srv.license_type, srv.port_at_host, srv.enabled
                )
        return dict(self._server_ids)

    def server_id(self, srv: ServerConfig) -> int:
        if srv.name not in self._server_ids:
            self.sync_servers()
        return self._server_ids[srv.name]

    def _feature_id(self, server_id: int, name: str, seen_at: datetime, vendor=None, version=None) -> int:
        rule = self.config.resolve_product(name, vendor)
        return self.repo.get_or_create_feature(
            server_id,
            name,
            seen_at,
            vendor=vendor,
            version=version,
            product=rule.product if rule else None,
            publisher=rule.publisher if rule else None,
        )

    # -- collecte ---------------------------------------------------------

    def collect_all(self, only: list[str] | None = None) -> list[RunResult]:
        self.sync_servers()
        results: list[RunResult] = []
        for srv in self.config.servers:
            if not srv.enabled:
                continue
            if only and srv.name.lower() not in {o.lower() for o in only}:
                continue
            results.extend(self.collect_server(srv))
        return results

    def collect_server(self, srv: ServerConfig) -> list[RunResult]:
        results = []
        for name in srv.collectors:
            results.append(self._run_collector(srv, name))
        return results

    def _run_collector(self, srv: ServerConfig, collector_name: str) -> RunResult:
        server_id = self.server_id(srv)
        started = datetime.now().replace(microsecond=0)
        track_sessions = srv.sessions_from == collector_name
        try:
            cls = get_collector_class(srv.license_type, collector_name)
            collector = cls(srv, self.config)
            with self.db.transaction():
                if cls.kind == "status":
                    status = collector.poll()
                    items = self.apply_status(server_id, status, collector_name, track_sessions)
                else:
                    state = self.repo.get_state(server_id, collector_name)
                    events, new_state = collector.read_new_events(state)
                    items = self.apply_events(server_id, events, collector_name, track_sessions)
                    self.repo.save_state(server_id, collector_name, new_state)
                run_id = self.repo.start_run(server_id, collector_name, started)
                self.repo.finish_run(run_id, "OK", items, None)
            log.info("%s/%s : OK (%d éléments)", srv.name, collector_name, items)
            return RunResult(srv.name, collector_name, "OK", items)
        except Exception as exc:  # une erreur sur un serveur ne bloque pas les autres
            # Erreur attendue (serveur injoignable...) : message court ; sinon trace complète.
            log.error(
                "%s/%s : échec - %s", srv.name, collector_name, exc,
                exc_info=not isinstance(exc, CollectorError),
            )
            try:
                with self.db.transaction():
                    run_id = self.repo.start_run(server_id, collector_name, started)
                    self.repo.finish_run(run_id, "ERROR", 0, str(exc))
            except Exception:  # pragma: no cover - base indisponible
                log.exception("Impossible de journaliser l'échec en base")
            return RunResult(srv.name, collector_name, "ERROR", 0, str(exc))

    # -- application d'un sondage d'état -------------------------------------

    def apply_status(self, server_id: int, status: ServerStatus, source: str, track_sessions: bool) -> int:
        now = status.queried_at
        current: dict[tuple, tuple] = {}
        for feat in status.features:
            fid = self._feature_id(server_id, feat.name, now, feat.vendor, feat.version)
            self.repo.set_feature_expiry(fid, feat.expires)
            self.repo.add_snapshot(fid, now, feat.issued, feat.in_use, feat.queued)
            for co in feat.checkouts:
                current[co.session_key()] = (co, fid)

        if not track_sessions:
            return len(status.features)

        down_vendors = {v.lower() for v, st in status.vendors.items() if st != "UP"}
        still_open: list[int] = []
        for sess in self.repo.open_sessions(server_id, source):
            discriminator = sess["handle"] or sess["checkout_time"].isoformat()
            key = (sess["feature"], sess["username"], sess["hostname"], discriminator)
            if key in current:
                still_open.append(sess["id"])
                current.pop(key)
            elif (sess.get("vendor") or "").lower() in down_vendors:
                # Démon vendeur arrêté : on ne sait pas si le jeton est rendu.
                continue
            else:
                last_seen = sess["last_seen"]
                checkin = (last_seen + min((now - last_seen) / 2, _MAX_CHECKIN_ESTIMATE)).replace(microsecond=0)
                self.repo.close_session(sess["id"], max(checkin, sess["checkout_time"]), "not_seen")
        self.repo.touch_sessions(still_open, now)

        for co, fid in current.values():
            start = co.start_time if co.start_time and co.start_time <= now else now
            self.repo.open_session(
                fid,
                self.repo.get_or_create_user(co.user),
                self.repo.get_or_create_host(co.host),
                checkout_time=start,
                seen_at=now,
                source=source,
                count=co.count,
                display=co.display,
                handle=co.handle,
            )
        return len(status.features)

    # -- application d'évènements de log ---------------------------------------

    def apply_events(self, server_id: int, events: list[LicenseEvent], source: str, track_sessions: bool) -> int:
        open_by_key: dict[tuple, deque] = defaultdict(deque)
        vendor_of_session: dict[int, str | None] = {}
        if track_sessions:
            for sess in self.repo.open_sessions(server_id, source):
                open_by_key[(sess["feature_id"], sess["user_id"], sess["workstation_id"])].append(sess["id"])
                vendor_of_session[sess["id"]] = sess.get("vendor")

        stored = 0
        for e in events:
            if e.event_type == EventType.SERVER_DOWN:
                if track_sessions:
                    for key, ids in open_by_key.items():
                        keep = deque()
                        for sid in ids:
                            v = vendor_of_session.get(sid)
                            if e.vendor is None or (v or "").lower() == e.vendor.lower():
                                self.repo.close_session(sid, e.event_time, "server_down")
                            else:
                                keep.append(sid)
                        open_by_key[key] = keep
                self.repo.add_event(server_id, e.event_time, e.event_type.value, None, None, None, 0, e.detail)
                stored += 1
                continue

            fid = self._feature_id(server_id, e.feature, e.event_time, e.vendor)
            uid = self.repo.get_or_create_user(e.user or "")
            hid = self.repo.get_or_create_host(e.host or "")

            if e.event_type in PERSISTED_EVENT_TYPES:
                self.repo.add_event(server_id, e.event_time, e.event_type.value, fid, uid, hid, e.count, e.detail)
                stored += 1

            if not track_sessions:
                continue
            key = (fid, uid, hid)
            if e.event_type == EventType.OUT:
                sid = self.repo.open_session(
                    fid, uid, hid, checkout_time=e.event_time, seen_at=e.event_time, source=source, count=e.count
                )
                open_by_key[key].append(sid)
                vendor_of_session[sid] = e.vendor
                stored += 1
            elif e.event_type == EventType.IN:
                if open_by_key[key]:
                    sid = open_by_key[key].popleft()
                    reason = "checkin"
                    if e.detail and "INACTIVE" in e.detail.upper():
                        reason = "inactive"
                    self.repo.close_session(sid, e.event_time, reason)
                    stored += 1
                # IN sans OUT connu (lecture démarrée en cours de session) : ignoré.
        return stored

    # -- maintenance -------------------------------------------------------

    def purge(self, now: datetime | None = None) -> dict[str, int]:
        now = now or datetime.now()
        with self.db.transaction():
            return self.repo.purge(
                now - timedelta(days=self.config.retention.snapshots_days),
                now - timedelta(days=self.config.retention.events_days),
            )

    def apply_product_mapping(self) -> int:
        with self.db.transaction():
            return self.repo.apply_product_mapping(self.config.resolve_product)
