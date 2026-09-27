-- Création de la base HeptagonesLM et des droits du compte de service.
-- À exécuter une fois par un administrateur SQL Server, puis lancer
-- `heptagoneslm init-db` (ou 01_schema.sql) avec le compte de service.
-- Adapter le nom du compte (DOMAINE\svc_heptagoneslm) et les chemins si besoin.

IF DB_ID(N'HeptagonesLM') IS NULL
    CREATE DATABASE HeptagonesLM;
GO

-- Le modèle de récupération SIMPLE suffit pour des données de reporting.
ALTER DATABASE HeptagonesLM SET RECOVERY SIMPLE;
GO

IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = N'DOMAINE\svc_heptagoneslm')
    CREATE LOGIN [DOMAINE\svc_heptagoneslm] FROM WINDOWS;
GO

USE HeptagonesLM;
GO

IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'svc_heptagoneslm')
    CREATE USER [svc_heptagoneslm] FOR LOGIN [DOMAINE\svc_heptagoneslm];
GO

ALTER ROLE db_datareader ADD MEMBER [svc_heptagoneslm];
ALTER ROLE db_datawriter ADD MEMBER [svc_heptagoneslm];
-- Droit TEMPORAIRE, nécessaire uniquement pour `heptagoneslm init-db` (création / évolution
-- du schéma). Le retirer dès l'initialisation faite (moindre privilège) :
--     ALTER ROLE db_ddladmin DROP MEMBER [svc_heptagoneslm];
-- et ne le redonner que le temps d'une mise à jour de version.
ALTER ROLE db_ddladmin ADD MEMBER [svc_heptagoneslm];
GO

-- À exécuter APRÈS `heptagoneslm init-db` :
-- ALTER ROLE db_ddladmin DROP MEMBER [svc_heptagoneslm];
-- GO
