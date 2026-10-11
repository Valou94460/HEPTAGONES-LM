-- Schéma HeptagonesLM (généré par `heptagoneslm ddl --dialect mssql`, ne pas modifier à la main)
-- Version du schéma : 10

IF OBJECT_ID(N'dbo.schema_info', N'U') IS NULL
CREATE TABLE dbo.schema_info (
    version INT NOT NULL,
    applied_at DATETIME2(0) NOT NULL DEFAULT SYSDATETIME()
)
GO

IF OBJECT_ID(N'dbo.license_server', N'U') IS NULL
CREATE TABLE dbo.license_server (
    id INT IDENTITY(1,1) PRIMARY KEY,
    name NVARCHAR(100) NOT NULL UNIQUE,
    license_type NVARCHAR(20) NOT NULL DEFAULT 'flexlm',
    port_at_host NVARCHAR(255) NOT NULL,
    enabled BIT NOT NULL DEFAULT 1,
    created_at DATETIME2(0) NOT NULL DEFAULT SYSDATETIME(),
    updated_at DATETIME2(0) NOT NULL DEFAULT SYSDATETIME()
)
GO

IF OBJECT_ID(N'dbo.feature', N'U') IS NULL
CREATE TABLE dbo.feature (
    id INT IDENTITY(1,1) PRIMARY KEY,
    server_id INT NOT NULL REFERENCES license_server(id),
    name NVARCHAR(255) NOT NULL,
    vendor NVARCHAR(100) NULL,
    version NVARCHAR(50) NULL,
    product NVARCHAR(255) NULL,
    publisher NVARCHAR(100) NULL,
    first_seen DATETIME2(0) NOT NULL,
    last_seen DATETIME2(0) NOT NULL,
    expires_on DATE NULL,
    CONSTRAINT uq_feature UNIQUE (server_id, name)
)
GO

IF OBJECT_ID(N'dbo.app_user', N'U') IS NULL
CREATE TABLE dbo.app_user (
    id INT IDENTITY(1,1) PRIMARY KEY,
    username NVARCHAR(255) NOT NULL UNIQUE,
    display_name NVARCHAR(255) NULL,
    department NVARCHAR(255) NULL,
    first_seen DATETIME2(0) NOT NULL DEFAULT SYSDATETIME()
)
GO

IF OBJECT_ID(N'dbo.workstation', N'U') IS NULL
CREATE TABLE dbo.workstation (
    id INT IDENTITY(1,1) PRIMARY KEY,
    hostname NVARCHAR(255) NOT NULL UNIQUE,
    site NVARCHAR(255) NULL,
    first_seen DATETIME2(0) NOT NULL DEFAULT SYSDATETIME()
)
GO

IF OBJECT_ID(N'dbo.license_session', N'U') IS NULL
CREATE TABLE dbo.license_session (
    id BIGINT IDENTITY(1,1) PRIMARY KEY,
    feature_id INT NOT NULL REFERENCES feature(id),
    user_id INT NOT NULL REFERENCES app_user(id),
    workstation_id INT NOT NULL REFERENCES workstation(id),
    display NVARCHAR(255) NULL,
    handle NVARCHAR(50) NULL,
    license_count INT NOT NULL DEFAULT 1,
    checkout_time DATETIME2(0) NOT NULL,
    checkin_time DATETIME2(0) NULL,
    last_seen DATETIME2(0) NOT NULL,
    source NVARCHAR(20) NOT NULL,
    close_reason NVARCHAR(30) NULL,
    borrowed BIT NOT NULL DEFAULT 0
)
GO

IF OBJECT_ID(N'dbo.license_event', N'U') IS NULL
CREATE TABLE dbo.license_event (
    id BIGINT IDENTITY(1,1) PRIMARY KEY,
    server_id INT NOT NULL REFERENCES license_server(id),
    feature_id INT NULL REFERENCES feature(id),
    user_id INT NULL REFERENCES app_user(id),
    workstation_id INT NULL REFERENCES workstation(id),
    event_time DATETIME2(0) NOT NULL,
    event_type NVARCHAR(20) NOT NULL,
    license_count INT NOT NULL DEFAULT 1,
    detail NVARCHAR(500) NULL
)
GO

IF OBJECT_ID(N'dbo.usage_snapshot', N'U') IS NULL
CREATE TABLE dbo.usage_snapshot (
    id BIGINT IDENTITY(1,1) PRIMARY KEY,
    feature_id INT NOT NULL REFERENCES feature(id),
    snapshot_time DATETIME2(0) NOT NULL,
    issued INT NULL,
    in_use INT NOT NULL,
    queued INT NOT NULL DEFAULT 0
)
GO

IF OBJECT_ID(N'dbo.usage_hourly', N'U') IS NULL
CREATE TABLE dbo.usage_hourly (
    feature_id INT NOT NULL REFERENCES feature(id),
    hour_start DATETIME2(0) NOT NULL,
    samples INT NOT NULL,
    sum_in_use BIGINT NOT NULL,
    max_in_use INT NOT NULL,
    min_in_use INT NOT NULL,
    issued INT NULL,
    max_queued INT NOT NULL DEFAULT 0,
    PRIMARY KEY (feature_id, hour_start)
)
GO

IF OBJECT_ID(N'dbo.collection_run', N'U') IS NULL
CREATE TABLE dbo.collection_run (
    id BIGINT IDENTITY(1,1) PRIMARY KEY,
    server_id INT NOT NULL REFERENCES license_server(id),
    collector NVARCHAR(30) NOT NULL,
    started_at DATETIME2(0) NOT NULL,
    finished_at DATETIME2(0) NULL,
    status NVARCHAR(10) NOT NULL,
    items INT NOT NULL DEFAULT 0,
    message NVARCHAR(2000) NULL
)
GO

IF OBJECT_ID(N'dbo.collector_state', N'U') IS NULL
CREATE TABLE dbo.collector_state (
    server_id INT NOT NULL REFERENCES license_server(id),
    collector NVARCHAR(30) NOT NULL,
    state_json NVARCHAR(MAX) NOT NULL,
    updated_at DATETIME2(0) NOT NULL,
    PRIMARY KEY (server_id, collector)
)
GO

IF OBJECT_ID(N'dbo.report_delivery', N'U') IS NULL
CREATE TABLE dbo.report_delivery (
    id BIGINT IDENTITY(1,1) PRIMARY KEY,
    subscription NVARCHAR(100) NOT NULL,
    period_start DATETIME2(0) NOT NULL,
    period_end DATETIME2(0) NOT NULL,
    sent_at DATETIME2(0) NOT NULL,
    recipients NVARCHAR(2000) NOT NULL,
    status NVARCHAR(10) NOT NULL,
    message NVARCHAR(2000) NULL
)
GO

IF OBJECT_ID(N'dbo.alert_state', N'U') IS NULL
CREATE TABLE dbo.alert_state (
    alert_key NVARCHAR(300) NOT NULL PRIMARY KEY,
    active BIT NOT NULL DEFAULT 1,
    first_seen DATETIME2(0) NOT NULL,
    last_sent DATETIME2(0) NULL,
    last_event_id BIGINT NULL,
    title NVARCHAR(500) NULL
)
GO

IF OBJECT_ID(N'dbo.feature_reservation', N'U') IS NULL
CREATE TABLE dbo.feature_reservation (
    feature_id INT NOT NULL REFERENCES feature(id),
    kind NVARCHAR(20) NOT NULL,
    name NVARCHAR(255) NOT NULL,
    reserved INT NOT NULL,
    updated_at DATETIME2(0) NOT NULL,
    PRIMARY KEY (feature_id, kind, name)
)
GO

IF OBJECT_ID(N'dbo.itsm_ticket', N'U') IS NULL
CREATE TABLE dbo.itsm_ticket (
    id BIGINT IDENTITY(1,1) PRIMARY KEY,
    connector NVARCHAR(100) NOT NULL,
    alert_key NVARCHAR(300) NOT NULL,
    ticket_id NVARCHAR(100) NULL,
    ticket_ref NVARCHAR(100) NULL,
    url NVARCHAR(500) NULL,
    title NVARCHAR(500) NULL,
    status NVARCHAR(10) NOT NULL,
    opened_at DATETIME2(0) NOT NULL,
    updated_at DATETIME2(0) NOT NULL,
    resolved_at DATETIME2(0) NULL
)
GO

IF OBJECT_ID(N'dbo.named_seat', N'U') IS NULL
CREATE TABLE dbo.named_seat (
    id BIGINT IDENTITY(1,1) PRIMARY KEY,
    vendor NVARCHAR(30) NOT NULL,
    product NVARCHAR(255) NOT NULL,
    email NVARCHAR(255) NOT NULL,
    display_name NVARCHAR(255) NULL,
    assigned_date DATE NULL,
    unassigned_date DATE NULL,
    days_used INT NULL,
    days_inactive INT NULL,
    last_used DATE NULL,
    version NVARCHAR(50) NULL,
    activity NVARCHAR(50) NULL,
    report_date DATE NOT NULL,
    imported_at DATETIME2(0) NOT NULL,
    source_file NVARCHAR(255) NULL
)
GO

IF OBJECT_ID(N'dbo.named_usage_day', N'U') IS NULL
CREATE TABLE dbo.named_usage_day (
    id BIGINT IDENTITY(1,1) PRIMARY KEY,
    vendor NVARCHAR(30) NOT NULL,
    product NVARCHAR(255) NOT NULL,
    email NVARCHAR(255) NOT NULL,
    usage_date DATE NOT NULL,
    imported_at DATETIME2(0) NOT NULL
)
GO

IF OBJECT_ID(N'dbo.user_project', N'U') IS NULL
CREATE TABLE dbo.user_project (
    username NVARCHAR(255) NOT NULL PRIMARY KEY,
    project NVARCHAR(255) NOT NULL,
    source_file NVARCHAR(255) NULL,
    updated_at DATETIME2(0) NOT NULL
)
GO

IF OBJECT_ID(N'dbo.user_pref', N'U') IS NULL
CREATE TABLE dbo.user_pref (
    username NVARCHAR(255) NOT NULL PRIMARY KEY,
    tour_version INT NOT NULL DEFAULT 0,
    onboarded_at DATETIME2(0) NULL,
    language NVARCHAR(5) NULL,
    updated_at DATETIME2(0) NOT NULL
)
GO

IF OBJECT_ID(N'dbo.named_import', N'U') IS NULL
CREATE TABLE dbo.named_import (
    id INT IDENTITY(1,1) PRIMARY KEY,
    vendor NVARCHAR(30) NOT NULL,
    file_name NVARCHAR(255) NOT NULL,
    file_hash NVARCHAR(64) NOT NULL UNIQUE,
    report_date DATE NOT NULL,
    rows_count INT NOT NULL,
    imported_at DATETIME2(0) NOT NULL
)
GO

IF COL_LENGTH(N'dbo.feature', N'expires_on') IS NULL
ALTER TABLE dbo.feature ADD expires_on DATE NULL
GO

IF COL_LENGTH(N'dbo.license_session', N'borrowed') IS NULL
ALTER TABLE dbo.license_session ADD borrowed BIT NOT NULL DEFAULT 0
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_session_feature_time')
CREATE INDEX ix_session_feature_time ON dbo.license_session (feature_id, checkout_time)
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_session_open')
CREATE INDEX ix_session_open ON dbo.license_session (source, checkin_time)
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_session_user')
CREATE INDEX ix_session_user ON dbo.license_session (user_id, checkout_time)
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_event_type_time')
CREATE INDEX ix_event_type_time ON dbo.license_event (event_type, event_time)
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_snapshot_feature_time')
CREATE INDEX ix_snapshot_feature_time ON dbo.usage_snapshot (feature_id, snapshot_time)
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_snapshot_time')
CREATE INDEX ix_snapshot_time ON dbo.usage_snapshot (snapshot_time)
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_hourly_time')
CREATE INDEX ix_hourly_time ON dbo.usage_hourly (hour_start)
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_run_server_time')
CREATE INDEX ix_run_server_time ON dbo.collection_run (server_id, started_at)
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_delivery_sub_period')
CREATE INDEX ix_delivery_sub_period ON dbo.report_delivery (subscription, period_start)
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_itsm_open')
CREATE INDEX ix_itsm_open ON dbo.itsm_ticket (status, connector, alert_key)
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_named_seat_report')
CREATE INDEX ix_named_seat_report ON dbo.named_seat (vendor, report_date)
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_named_usage_day')
CREATE INDEX ix_named_usage_day ON dbo.named_usage_day (usage_date, product)
GO

CREATE OR ALTER VIEW dbo.v_session_detail AS
SELECT s.id AS session_id, srv.name AS server_name, f.name AS feature,
       COALESCE(f.product, f.name) AS product, f.publisher, f.vendor,
       u.username, u.display_name, u.department, w.hostname, w.site,
       s.checkout_time, s.checkin_time, s.license_count, s.source,
       s.close_reason, DATEDIFF(MINUTE, s.checkout_time, COALESCE(s.checkin_time, s.last_seen)) AS duration_min
FROM license_session s
JOIN feature f ON f.id = s.feature_id
JOIN license_server srv ON srv.id = f.server_id
JOIN app_user u ON u.id = s.user_id
JOIN workstation w ON w.id = s.workstation_id
GO

CREATE OR ALTER VIEW dbo.v_usage_hourly AS
SELECT srv.name AS server_name, f.name AS feature,
       COALESCE(f.product, f.name) AS product, h.hour_start, h.issued,
       h.max_in_use, h.min_in_use,
       CAST(h.sum_in_use AS FLOAT) / h.samples AS avg_in_use,
       h.max_queued, h.samples
FROM usage_hourly h
JOIN feature f ON f.id = h.feature_id
JOIN license_server srv ON srv.id = f.server_id
GO

CREATE OR ALTER VIEW dbo.v_denial AS
SELECT e.event_time, srv.name AS server_name, f.name AS feature,
       COALESCE(f.product, f.name) AS product, u.username, w.hostname,
       e.detail
FROM license_event e
JOIN license_server srv ON srv.id = e.server_id
LEFT JOIN feature f ON f.id = e.feature_id
LEFT JOIN app_user u ON u.id = e.user_id
LEFT JOIN workstation w ON w.id = e.workstation_id
WHERE e.event_type = 'DENIED'
GO

