"""Accès aux données : toutes les requêtes d'écriture de HeptagonesLM passent ici."""

from __future__ import annotations

import json
import logging
from datetime import datetime

from heptagoneslm.storage.db import Database

log = logging.getLogger(__name__)


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def hour_floor(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


class Repository:
    """Opérations métier sur la base, avec cache des identifiants de dimensions.

    Le cache est valable pour la durée d'une exécution (une collecte).
    """

    def __init__(self, db: Database):
        self.db = db
        self._users: dict[str, int] = {}
        self._hosts: dict[str, int] = {}
        self._features: dict[tuple[int, str], int] = {}

    # -- dimensions ------------------------------------------------------

    def upsert_server(self, name: str, license_type: str, port_at_host: str, enabled: bool) -> int:
        row = self.db.query("SELECT id FROM license_server WHERE name = ?", [name])
        if row:
            self.db.execute(
                "UPDATE license_server SET license_type = ?, port_at_host = ?, enabled = ?, updated_at = ? "
                "WHERE id = ?",
                [license_type, port_at_host, int(enabled), datetime.now().replace(microsecond=0), row[0][0]],
            )
            return int(row[0][0])
        now = datetime.now().replace(microsecond=0)
        return self.db.insert(
            "license_server",
            {
                "name": name,
                "license_type": license_type,
                "port_at_host": port_at_host,
                "enabled": int(enabled),
                "created_at": now,
                "updated_at": now,
            },
        )

    def get_or_create_feature(
        self,
        server_id: int,
        name: str,
        seen_at: datetime,
        vendor: str | None = None,
        version: str | None = None,
        product: str | None = None,
        publisher: str | None = None,
    ) -> int:
        key = (server_id, name)
        fid = self._features.get(key)
        if fid is None:
            row = self.db.query("SELECT id FROM feature WHERE server_id = ? AND name = ?", [server_id, name])
            if row:
                fid = int(row[0][0])
            else:
                fid = self.db.insert(
                    "feature",
                    {
                        "server_id": server_id,
                        "name": name,
                        "vendor": vendor,
                        "version": version,
                        "product": product,
                        "publisher": publisher,
                        "first_seen": seen_at,
                        "last_seen": seen_at,
                    },
                )
                self._features[key] = fid
                return fid
            self._features[key] = fid
        # Mise à jour des attributs connus (le mapping produit peut évoluer).
        self.db.execute(
            "UPDATE feature SET last_seen = CASE WHEN last_seen < ? THEN ? ELSE last_seen END, "
            "first_seen = CASE WHEN first_seen > ? THEN ? ELSE first_seen END, "
            "vendor = COALESCE(?, vendor), version = COALESCE(?, version), "
            "product = COALESCE(?, product), publisher = COALESCE(?, publisher) WHERE id = ?",
            [seen_at, seen_at, seen_at, seen_at, vendor, version, product, publisher, fid],
        )
        return fid

    def has_column(self, table: str, column: str) -> bool:
        """Vrai si la colonne existe (base pas encore migrée par init-db sinon)."""
        key = (table, column)
        cache = self.__dict__.setdefault("_columns", {})
        if key not in cache:
            # Requête de métadonnées (n'échoue pas, ne perturbe pas la transaction en cours).
            if self.db.dialect == "mssql":
                cache[key] = self.db.scalar("SELECT COL_LENGTH(?, ?)", [f"dbo.{table}", column]) is not None
            else:
                cache[key] = column in {row[1] for row in self.db.query(f"PRAGMA table_info({table})")}
            if not cache[key]:
                log.warning("Colonne %s.%s absente : exécuter « heptagoneslm init-db » pour mettre à jour le schéma",
                            table, column)
        return cache[key]

    def set_feature_expiry(self, feature_id: int, expires_on) -> None:
        if expires_on is not None and self.has_column("feature", "expires_on"):
            self.db.execute("UPDATE feature SET expires_on = ? WHERE id = ?", [expires_on, feature_id])

    def get_or_create_user(self, username: str) -> int:
        key = _norm(username) or "(inconnu)"
        uid = self._users.get(key)
        if uid is None:
            row = self.db.query("SELECT id FROM app_user WHERE username = ?", [key])
            uid = int(row[0][0]) if row else self.db.insert(
                "app_user",
                {"username": key, "first_seen": datetime.now().replace(microsecond=0)},
            )
            self._users[key] = uid
        return uid

    def get_or_create_host(self, hostname: str) -> int:
        key = _norm(hostname) or "(inconnu)"
        hid = self._hosts.get(key)
        if hid is None:
            row = self.db.query("SELECT id FROM workstation WHERE hostname = ?", [key])
            hid = int(row[0][0]) if row else self.db.insert(
                "workstation",
                {"hostname": key, "first_seen": datetime.now().replace(microsecond=0)},
            )
            self._hosts[key] = hid
        return hid

    def update_user_info(self, username: str, display_name: str | None, department: str | None) -> bool:
        """Renseigne le nom complet et le service d'un utilisateur (créé s'il n'existe pas).

        Retourne True si l'utilisateur existait déjà.
        """
        key = _norm(username)
        existed = bool(self.db.query("SELECT id FROM app_user WHERE username = ?", [key]))
        uid = self.get_or_create_user(key)
        self.db.execute(
            "UPDATE app_user SET display_name = COALESCE(?, display_name), department = COALESCE(?, department) "
            "WHERE id = ?",
            [display_name or None, department or None, uid],
        )
        return existed

    def apply_product_mapping(self, resolver) -> int:
        """Réapplique les règles produit à toutes les features. Retourne le nb modifié."""
        changed = 0
        for fid, name, vendor, product, publisher in self.db.query(
            "SELECT id, name, vendor, product, publisher FROM feature"
        ):
            rule = resolver(name, vendor)
            new_product = rule.product if rule else None
            new_publisher = rule.publisher if rule else None
            if (new_product, new_publisher) != (product, publisher):
                self.db.execute(
                    "UPDATE feature SET product = ?, publisher = ? WHERE id = ?",
                    [new_product, new_publisher, fid],
                )
                changed += 1
        return changed

    # -- snapshots / agrégats ---------------------------------------------

    def add_snapshot(self, feature_id: int, when: datetime, issued: int | None, in_use: int, queued: int) -> None:
        self.db.execute(
            "INSERT INTO usage_snapshot (feature_id, snapshot_time, issued, in_use, queued) VALUES (?, ?, ?, ?, ?)",
            [feature_id, when, issued, in_use, queued],
        )
        self._update_hourly(feature_id, when, issued, in_use, queued)

    def _update_hourly(self, feature_id: int, when: datetime, issued: int | None, in_use: int, queued: int) -> None:
        hour = hour_floor(when)
        updated = self.db.execute(
            "UPDATE usage_hourly SET samples = samples + 1, sum_in_use = sum_in_use + ?, "
            "max_in_use = CASE WHEN max_in_use < ? THEN ? ELSE max_in_use END, "
            "min_in_use = CASE WHEN min_in_use > ? THEN ? ELSE min_in_use END, "
            "issued = CASE WHEN issued IS NULL OR issued < ? THEN ? ELSE issued END, "
            "max_queued = CASE WHEN max_queued < ? THEN ? ELSE max_queued END "
            "WHERE feature_id = ? AND hour_start = ?",
            [in_use, in_use, in_use, in_use, in_use, issued, issued, queued, queued, feature_id, hour],
        ).rowcount
        if not updated:
            self.db.execute(
                "INSERT INTO usage_hourly (feature_id, hour_start, samples, sum_in_use, max_in_use, "
                "min_in_use, issued, max_queued) VALUES (?, ?, 1, ?, ?, ?, ?, ?)",
                [feature_id, hour, in_use, in_use, in_use, issued, queued],
            )

    # -- sessions ----------------------------------------------------------

    def open_sessions(self, server_id: int, source: str) -> list[dict]:
        return self.db.query_dicts(
            "SELECT s.id, f.name AS feature, f.vendor, u.username, w.hostname, s.handle, "
            "s.checkout_time, s.last_seen, s.feature_id, s.user_id, s.workstation_id "
            "FROM license_session s JOIN feature f ON f.id = s.feature_id "
            "JOIN app_user u ON u.id = s.user_id JOIN workstation w ON w.id = s.workstation_id "
            "WHERE f.server_id = ? AND s.source = ? AND s.checkin_time IS NULL "
            "ORDER BY s.checkout_time, s.id",
            [server_id, source],
        )

    def open_session(
        self,
        feature_id: int,
        user_id: int,
        workstation_id: int,
        checkout_time: datetime,
        seen_at: datetime,
        source: str,
        count: int = 1,
        display: str | None = None,
        handle: str | None = None,
    ) -> int:
        return self.db.insert(
            "license_session",
            {
                "feature_id": feature_id,
                "user_id": user_id,
                "workstation_id": workstation_id,
                "display": display,
                "handle": handle,
                "license_count": count,
                "checkout_time": checkout_time,
                "checkin_time": None,
                "last_seen": seen_at,
                "source": source,
            },
        )

    def touch_sessions(self, session_ids: list[int], seen_at: datetime) -> None:
        self.db.executemany(
            "UPDATE license_session SET last_seen = ? WHERE id = ?",
            [(seen_at, sid) for sid in session_ids],
        )

    def close_session(self, session_id: int, checkin_time: datetime, reason: str) -> None:
        self.db.execute(
            "UPDATE license_session SET checkin_time = ?, last_seen = ?, close_reason = ? WHERE id = ?",
            [checkin_time, checkin_time, reason, session_id],
        )

    # -- évènements --------------------------------------------------------

    def add_event(
        self,
        server_id: int,
        event_time: datetime,
        event_type: str,
        feature_id: int | None,
        user_id: int | None,
        workstation_id: int | None,
        count: int,
        detail: str | None,
    ) -> None:
        self.db.execute(
            "INSERT INTO license_event (server_id, feature_id, user_id, workstation_id, event_time, "
            "event_type, license_count, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [server_id, feature_id, user_id, workstation_id, event_time, event_type, count, detail],
        )

    # -- journal / état ---------------------------------------------------

    def start_run(self, server_id: int, collector: str, started_at: datetime) -> int:
        return self.db.insert(
            "collection_run",
            {"server_id": server_id, "collector": collector, "started_at": started_at, "status": "RUNNING"},
        )

    def finish_run(self, run_id: int, status: str, items: int, message: str | None) -> None:
        self.db.execute(
            "UPDATE collection_run SET finished_at = ?, status = ?, items = ?, message = ? WHERE id = ?",
            [datetime.now().replace(microsecond=0), status, items, (message or "")[:2000] or None, run_id],
        )

    def get_state(self, server_id: int, collector: str) -> dict:
        raw = self.db.scalar(
            "SELECT state_json FROM collector_state WHERE server_id = ? AND collector = ?",
            [server_id, collector],
        )
        return json.loads(raw) if raw else {}

    def save_state(self, server_id: int, collector: str, state: dict) -> None:
        payload = json.dumps(state)
        now = datetime.now().replace(microsecond=0)
        n = self.db.execute(
            "UPDATE collector_state SET state_json = ?, updated_at = ? WHERE server_id = ? AND collector = ?",
            [payload, now, server_id, collector],
        ).rowcount
        if not n:
            self.db.execute(
                "INSERT INTO collector_state (server_id, collector, state_json, updated_at) VALUES (?, ?, ?, ?)",
                [server_id, collector, payload, now],
            )

    # -- rétention --------------------------------------------------------

    def purge(self, snapshots_before: datetime, events_before: datetime) -> dict[str, int]:
        n_snap = self.db.execute("DELETE FROM usage_snapshot WHERE snapshot_time < ?", [snapshots_before]).rowcount
        n_evt = self.db.execute("DELETE FROM license_event WHERE event_time < ?", [events_before]).rowcount
        n_run = self.db.execute("DELETE FROM collection_run WHERE started_at < ?", [events_before]).rowcount
        return {"usage_snapshot": n_snap, "license_event": n_evt, "collection_run": n_run}
