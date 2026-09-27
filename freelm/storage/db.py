"""Connexion à la base et différences de dialecte.

FreeLM n'utilise que des requêtes SQL simples avec des paramètres ``?``,
style supporté à la fois par ``pyodbc`` (SQL Server) et ``sqlite3``
(tests, démonstration). Les seules différences de dialecte (auto-incrément,
récupération de l'identifiant inséré, types longs) sont isolées ici.

Chaînes de connexion acceptées :

* SQL Server (ODBC) :
  ``DRIVER={ODBC Driver 18 for SQL Server};SERVER=sqlsrv01.entreprise.local;DATABASE=FreeLM;Trusted_Connection=yes;Encrypt=yes``
  (le certificat du serveur SQL est vérifié ; ``TrustServerCertificate=yes`` uniquement en test)
* SQLite : ``sqlite:///chemin/vers/freelm.db`` ou ``sqlite:///:memory:``
"""

from __future__ import annotations

import logging
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Iterator, Sequence

log = logging.getLogger(__name__)


def _register_sqlite_types() -> None:
    # Stockage ISO des dates, reconversion via le type déclaré DATETIME2 / DATE.
    sqlite3.register_adapter(datetime, lambda v: v.isoformat(" "))
    sqlite3.register_adapter(date, lambda v: v.isoformat())
    sqlite3.register_converter("DATETIME2", lambda b: datetime.fromisoformat(b.decode()))
    sqlite3.register_converter("DATE", lambda b: date.fromisoformat(b.decode()))


_register_sqlite_types()


class Database:
    """Enveloppe fine autour d'une connexion DB-API."""

    def __init__(self, connection_string: str):
        self.connection_string = connection_string
        if connection_string.startswith("sqlite:///"):
            self.dialect = "sqlite"
            path = connection_string[len("sqlite:///"):]
            self.conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES)
            self.conn.execute("PRAGMA foreign_keys = ON")
        else:
            self.dialect = "mssql"
            try:
                import pyodbc  # import tardif : inutile pour SQLite
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("pyodbc est requis pour SQL Server (pip install pyodbc)") from exc
            if re.search(r"trustservercertificate\s*=\s*(yes|true)", connection_string, re.IGNORECASE):
                log.warning("SÉCURITÉ : TrustServerCertificate=yes — le certificat de SQL Server n'est pas "
                            "vérifié (interception possible). À réserver aux tests.")
            self.conn = pyodbc.connect(connection_string, autocommit=False)
            self.conn.timeout = 300

    # -- exécution -------------------------------------------------------

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Any:
        cur = self.conn.cursor()
        cur.execute(sql, tuple(params))
        return cur

    def executemany(self, sql: str, rows: Sequence[Sequence[Any]]) -> None:
        if not rows:
            return
        cur = self.conn.cursor()
        if self.dialect == "mssql":
            cur.fast_executemany = True
        cur.executemany(sql, [tuple(r) for r in rows])

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[tuple]:
        return [tuple(r) for r in self.execute(sql, params).fetchall()]

    def query_dicts(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        cur = self.execute(sql, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        row = self.execute(sql, params).fetchone()
        return row[0] if row else None

    def insert(self, table: str, values: dict[str, Any]) -> int:
        """Insère une ligne et retourne son identifiant auto-incrémenté."""
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        if self.dialect == "mssql":
            sql = f"INSERT INTO {table} ({cols}) OUTPUT INSERTED.id VALUES ({marks})"
            return int(self.execute(sql, list(values.values())).fetchone()[0])
        cur = self.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(values.values()))
        return int(cur.lastrowid)

    # -- expressions dépendantes du dialecte ------------------------------------

    def hour_of(self, column: str) -> str:
        """Heure (0-23) d'une colonne date/heure, en SQL."""
        if self.dialect == "mssql":
            return f"DATEPART(HOUR, {column})"
        return f"CAST(strftime('%H', {column}) AS INTEGER)"

    def weekday_of(self, column: str) -> str:
        """Jour de semaine d'une colonne date/heure, lundi = 0 (indépendant de DATEFIRST)."""
        if self.dialect == "mssql":
            return f"(DATEDIFF(DAY, 0, {column}) % 7)"  # 01/01/1900 était un lundi
        return f"((CAST(strftime('%w', {column}) AS INTEGER) + 6) % 7)"

    def try_lock(self, name: str) -> bool:
        """Verrou applicatif exclusif, tenu jusqu'à la fermeture de la connexion.

        Empêche deux collectes simultanées (tâche planifiée + lancement manuel)
        de créer des sessions en double. SQL Server : ``sp_getapplock`` ;
        SQLite (tests, démonstration) : toujours accordé.
        """
        if self.dialect != "mssql":
            return True
        code = self.scalar(
            "SET NOCOUNT ON; DECLARE @r INT; "
            "EXEC @r = sp_getapplock @Resource = ?, @LockMode = 'Exclusive', "
            "@LockOwner = 'Session', @LockTimeout = 0; SELECT @r",
            [name],
        )
        self.conn.commit()
        return code is not None and int(code) >= 0

    # -- transactions ----------------------------------------------------

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    @contextmanager
    def transaction(self) -> Iterator["Database"]:
        try:
            yield self
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
