"""Schéma de la base HeptagonesLM (source unique pour SQL Server et SQLite).

Le script ``sql/sqlserver/01_schema.sql`` est généré à partir de ce module
(``heptagoneslm ddl --dialect mssql``) : modifier ici, puis régénérer.

Vue d'ensemble ::

    license_server 1─┬─< feature 1─┬─< usage_snapshot     (sondages lmstat bruts)
                     │             ├─< usage_hourly       (agrégats horaires conservés)
                     │             ├─< license_session >─ app_user, workstation
                     │             └─< license_event   >─ app_user, workstation
                     ├─< collection_run   (journal des collectes)
                     └─< collector_state  (offset des logs...)
    report_delivery   (envois de rapports par e-mail)
    alert_state       (alertes e-mail envoyées : anti-répétition, curseur des refus)
    itsm_ticket       (tickets ouverts dans ServiceNow, Jira, GLPI… pour chaque alerte)
"""

from __future__ import annotations

import textwrap

from heptagoneslm.storage.db import Database

SCHEMA_VERSION = 5

# Marqueurs remplacés selon le dialecte.
_TYPES = {
    "mssql": {
        "ID": "INT IDENTITY(1,1) PRIMARY KEY",
        "BIGID": "BIGINT IDENTITY(1,1) PRIMARY KEY",
        "TEXT": "NVARCHAR(MAX)",
        "DT": "DATETIME2(0)",
        "BOOL": "BIT",
        "NOW": "SYSDATETIME()",
        "DURATION_MIN": "DATEDIFF(MINUTE, s.checkout_time, COALESCE(s.checkin_time, s.last_seen))",
    },
    "sqlite": {
        "ID": "INTEGER PRIMARY KEY AUTOINCREMENT",
        "BIGID": "INTEGER PRIMARY KEY AUTOINCREMENT",
        "TEXT": "TEXT",
        "DT": "DATETIME2",
        "BOOL": "INTEGER",
        "NOW": "CURRENT_TIMESTAMP",
        "DURATION_MIN": (
            "CAST(ROUND((julianday(COALESCE(s.checkin_time, s.last_seen)) - "
            "julianday(s.checkout_time)) * 1440) AS INTEGER)"
        ),
    },
}

TABLES: dict[str, str] = {
    "schema_info": """
        version INT NOT NULL,
        applied_at {DT} NOT NULL DEFAULT {NOW}
    """,
    "license_server": """
        id {ID},
        name NVARCHAR(100) NOT NULL UNIQUE,
        license_type NVARCHAR(20) NOT NULL DEFAULT 'flexlm',
        port_at_host NVARCHAR(255) NOT NULL,
        enabled {BOOL} NOT NULL DEFAULT 1,
        created_at {DT} NOT NULL DEFAULT {NOW},
        updated_at {DT} NOT NULL DEFAULT {NOW}
    """,
    "feature": """
        id {ID},
        server_id INT NOT NULL REFERENCES license_server(id),
        name NVARCHAR(255) NOT NULL,
        vendor NVARCHAR(100) NULL,
        version NVARCHAR(50) NULL,
        product NVARCHAR(255) NULL,
        publisher NVARCHAR(100) NULL,
        first_seen {DT} NOT NULL,
        last_seen {DT} NOT NULL,
        expires_on DATE NULL,
        CONSTRAINT uq_feature UNIQUE (server_id, name)
    """,
    "app_user": """
        id {ID},
        username NVARCHAR(255) NOT NULL UNIQUE,
        display_name NVARCHAR(255) NULL,
        department NVARCHAR(255) NULL,
        first_seen {DT} NOT NULL DEFAULT {NOW}
    """,
    "workstation": """
        id {ID},
        hostname NVARCHAR(255) NOT NULL UNIQUE,
        site NVARCHAR(255) NULL,
        first_seen {DT} NOT NULL DEFAULT {NOW}
    """,
    "license_session": """
        id {BIGID},
        feature_id INT NOT NULL REFERENCES feature(id),
        user_id INT NOT NULL REFERENCES app_user(id),
        workstation_id INT NOT NULL REFERENCES workstation(id),
        display NVARCHAR(255) NULL,
        handle NVARCHAR(50) NULL,
        license_count INT NOT NULL DEFAULT 1,
        checkout_time {DT} NOT NULL,
        checkin_time {DT} NULL,
        last_seen {DT} NOT NULL,
        source NVARCHAR(20) NOT NULL,
        close_reason NVARCHAR(30) NULL
    """,
    "license_event": """
        id {BIGID},
        server_id INT NOT NULL REFERENCES license_server(id),
        feature_id INT NULL REFERENCES feature(id),
        user_id INT NULL REFERENCES app_user(id),
        workstation_id INT NULL REFERENCES workstation(id),
        event_time {DT} NOT NULL,
        event_type NVARCHAR(20) NOT NULL,
        license_count INT NOT NULL DEFAULT 1,
        detail NVARCHAR(500) NULL
    """,
    "usage_snapshot": """
        id {BIGID},
        feature_id INT NOT NULL REFERENCES feature(id),
        snapshot_time {DT} NOT NULL,
        issued INT NULL,
        in_use INT NOT NULL,
        queued INT NOT NULL DEFAULT 0
    """,
    "usage_hourly": """
        feature_id INT NOT NULL REFERENCES feature(id),
        hour_start {DT} NOT NULL,
        samples INT NOT NULL,
        sum_in_use BIGINT NOT NULL,
        max_in_use INT NOT NULL,
        min_in_use INT NOT NULL,
        issued INT NULL,
        max_queued INT NOT NULL DEFAULT 0,
        PRIMARY KEY (feature_id, hour_start)
    """,
    "collection_run": """
        id {BIGID},
        server_id INT NOT NULL REFERENCES license_server(id),
        collector NVARCHAR(30) NOT NULL,
        started_at {DT} NOT NULL,
        finished_at {DT} NULL,
        status NVARCHAR(10) NOT NULL,
        items INT NOT NULL DEFAULT 0,
        message NVARCHAR(2000) NULL
    """,
    "collector_state": """
        server_id INT NOT NULL REFERENCES license_server(id),
        collector NVARCHAR(30) NOT NULL,
        state_json {TEXT} NOT NULL,
        updated_at {DT} NOT NULL,
        PRIMARY KEY (server_id, collector)
    """,
    "report_delivery": """
        id {BIGID},
        subscription NVARCHAR(100) NOT NULL,
        period_start {DT} NOT NULL,
        period_end {DT} NOT NULL,
        sent_at {DT} NOT NULL,
        recipients NVARCHAR(2000) NOT NULL,
        status NVARCHAR(10) NOT NULL,
        message NVARCHAR(2000) NULL
    """,
    "alert_state": """
        alert_key NVARCHAR(300) NOT NULL PRIMARY KEY,
        active {BOOL} NOT NULL DEFAULT 1,
        first_seen {DT} NOT NULL,
        last_sent {DT} NULL,
        last_event_id BIGINT NULL,
        title NVARCHAR(500) NULL
    """,
    "itsm_ticket": """
        id {BIGID},
        connector NVARCHAR(100) NOT NULL,
        alert_key NVARCHAR(300) NOT NULL,
        ticket_id NVARCHAR(100) NULL,
        ticket_ref NVARCHAR(100) NULL,
        url NVARCHAR(500) NULL,
        title NVARCHAR(500) NULL,
        status NVARCHAR(10) NOT NULL,
        opened_at {DT} NOT NULL,
        updated_at {DT} NOT NULL,
        resolved_at {DT} NULL
    """,
}

INDEXES: dict[str, str] = {
    "ix_session_feature_time": "license_session (feature_id, checkout_time)",
    "ix_session_open": "license_session (source, checkin_time)",
    "ix_session_user": "license_session (user_id, checkout_time)",
    "ix_event_type_time": "license_event (event_type, event_time)",
    "ix_snapshot_feature_time": "usage_snapshot (feature_id, snapshot_time)",
    "ix_snapshot_time": "usage_snapshot (snapshot_time)",
    "ix_hourly_time": "usage_hourly (hour_start)",
    "ix_run_server_time": "collection_run (server_id, started_at)",
    "ix_delivery_sub_period": "report_delivery (subscription, period_start)",
    "ix_itsm_open": "itsm_ticket (status, connector, alert_key)",
}

VIEWS: dict[str, str] = {
    # Sessions dénormalisées, prêtes pour un tableau croisé Excel/Power BI.
    "v_session_detail": """
        SELECT s.id AS session_id, srv.name AS server_name, f.name AS feature,
               COALESCE(f.product, f.name) AS product, f.publisher, f.vendor,
               u.username, u.display_name, u.department, w.hostname, w.site,
               s.checkout_time, s.checkin_time, s.license_count, s.source,
               s.close_reason, {DURATION_MIN} AS duration_min
        FROM license_session s
        JOIN feature f ON f.id = s.feature_id
        JOIN license_server srv ON srv.id = f.server_id
        JOIN app_user u ON u.id = s.user_id
        JOIN workstation w ON w.id = s.workstation_id
    """,
    "v_usage_hourly": """
        SELECT srv.name AS server_name, f.name AS feature,
               COALESCE(f.product, f.name) AS product, h.hour_start, h.issued,
               h.max_in_use, h.min_in_use,
               CAST(h.sum_in_use AS FLOAT) / h.samples AS avg_in_use,
               h.max_queued, h.samples
        FROM usage_hourly h
        JOIN feature f ON f.id = h.feature_id
        JOIN license_server srv ON srv.id = f.server_id
    """,
    "v_denial": """
        SELECT e.event_time, srv.name AS server_name, f.name AS feature,
               COALESCE(f.product, f.name) AS product, u.username, w.hostname,
               e.detail
        FROM license_event e
        JOIN license_server srv ON srv.id = e.server_id
        LEFT JOIN feature f ON f.id = e.feature_id
        LEFT JOIN app_user u ON u.id = e.user_id
        LEFT JOIN workstation w ON w.id = e.workstation_id
        WHERE e.event_type = 'DENIED'
    """,
}


# Colonnes ajoutées après la création initiale d'une table : (table, colonne, type).
# Appliquées par init-db sur les bases existantes (migration idempotente).
ADDED_COLUMNS: list[tuple[str, str, str]] = [
    ("feature", "expires_on", "DATE NULL"),          # v3 : échéance des licences
]


def _fmt(template: str, dialect: str) -> str:
    out = textwrap.dedent(template).strip()
    for key, value in _TYPES[dialect].items():
        out = out.replace("{" + key + "}", value)
    return out


def ddl_statements(dialect: str) -> list[str]:
    """Retourne la liste ordonnée des ordres DDL idempotents du dialecte."""
    stmts: list[str] = []
    for name, body in TABLES.items():
        cols = textwrap.indent(_fmt(body, dialect), "    ").strip()
        if dialect == "mssql":
            stmts.append(f"IF OBJECT_ID(N'dbo.{name}', N'U') IS NULL\nCREATE TABLE dbo.{name} (\n    {cols}\n)")
        else:
            stmts.append(f"CREATE TABLE IF NOT EXISTS {name} (\n    {cols}\n)")
    for table, column, coltype in ADDED_COLUMNS:
        if dialect == "mssql":
            stmts.append(
                f"IF COL_LENGTH(N'dbo.{table}', N'{column}') IS NULL\n"
                f"ALTER TABLE dbo.{table} ADD {column} {coltype}"
            )
        # SQLite : pas d'ALTER conditionnel en SQL pur, voir create_schema().
    for name, target in INDEXES.items():
        if dialect == "mssql":
            stmts.append(
                f"IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'{name}')\n"
                f"CREATE INDEX {name} ON dbo.{target}"
            )
        else:
            stmts.append(f"CREATE INDEX IF NOT EXISTS {name} ON {target}")
    for name, body in VIEWS.items():
        sql = _fmt(body, dialect)
        if dialect == "mssql":
            stmts.append(f"CREATE OR ALTER VIEW dbo.{name} AS\n{sql}")
        else:
            stmts.append(f"DROP VIEW IF EXISTS {name}")
            stmts.append(f"CREATE VIEW {name} AS\n{sql}")
    return stmts


def render_script(dialect: str) -> str:
    """Script SQL complet (avec séparateurs GO pour SQL Server)."""
    sep = "\nGO\n\n" if dialect == "mssql" else ";\n\n"
    header = (
        "-- Schéma HeptagonesLM (généré par `heptagoneslm ddl --dialect {d}`, ne pas modifier à la main)\n"
        "-- Version du schéma : {v}\n\n"
    ).format(d=dialect, v=SCHEMA_VERSION)
    return header + sep.join(ddl_statements(dialect)) + sep


def create_schema(db: Database) -> None:
    """Crée (ou complète) le schéma dans la base connectée."""
    with db.transaction():
        for stmt in ddl_statements(db.dialect):
            db.execute(stmt)
        if db.dialect == "sqlite":
            for table, column, coltype in ADDED_COLUMNS:
                existing = {row[1] for row in db.query(f"PRAGMA table_info({table})")}
                if column not in existing:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
        if not db.scalar("SELECT COUNT(*) FROM schema_info"):
            db.execute("INSERT INTO schema_info (version) VALUES (?)", [SCHEMA_VERSION])
        else:  # évolution : les nouvelles tables sont créées ci-dessus (ordres idempotents)
            db.execute("UPDATE schema_info SET version = ? WHERE version < ?", [SCHEMA_VERSION, SCHEMA_VERSION])
