-- Création de la base FreeLM et des droits du compte de service.
-- À exécuter une fois par un administrateur SQL Server, puis lancer
-- `freelm init-db` (ou 01_schema.sql) avec le compte de service.
-- Adapter le nom du compte (DOMAINE\svc_freelm) et les chemins si besoin.

IF DB_ID(N'FreeLM') IS NULL
    CREATE DATABASE FreeLM;
GO

-- Le modèle de récupération SIMPLE suffit pour des données de reporting.
ALTER DATABASE FreeLM SET RECOVERY SIMPLE;
GO

IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = N'DOMAINE\svc_freelm')
    CREATE LOGIN [DOMAINE\svc_freelm] FROM WINDOWS;
GO

USE FreeLM;
GO

IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'svc_freelm')
    CREATE USER [svc_freelm] FOR LOGIN [DOMAINE\svc_freelm];
GO

ALTER ROLE db_datareader ADD MEMBER [svc_freelm];
ALTER ROLE db_datawriter ADD MEMBER [svc_freelm];
-- Droit TEMPORAIRE, nécessaire uniquement pour `freelm init-db` (création / évolution
-- du schéma). Le retirer dès l'initialisation faite (moindre privilège) :
--     ALTER ROLE db_ddladmin DROP MEMBER [svc_freelm];
-- et ne le redonner que le temps d'une mise à jour de version.
ALTER ROLE db_ddladmin ADD MEMBER [svc_freelm];
GO

-- À exécuter APRÈS `freelm init-db` :
-- ALTER ROLE db_ddladmin DROP MEMBER [svc_freelm];
-- GO
