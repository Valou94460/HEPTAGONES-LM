# Journal des modifications

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) ;
numérotation [SemVer](https://semver.org/lang/fr/).

## [Non publié]

## [0.1.0] — 2026-09-27

Première version.

### Collecte
- Sondage `lmutil lmstat -a` : jetons émis / utilisés, checkouts en cours, files d'attente,
  utilisateurs avec espaces, changement d'année.
- Lecture incrémentale du log debug FlexLM : OUT / IN / DENIED / QUEUED / UNSUPPORTED,
  arrêts de démon, reconstitution des dates (TIMESTAMP, passage de minuit), détection de rotation.
- Reconstruction des sessions depuis lmstat (fin estimée) ou depuis le log (à la seconde).
- Registre de collecteurs extensible (futur RLM…).

### Stockage
- Schéma unique SQL Server / SQLite, script `sql/sqlserver/01_schema.sql`, vues de reporting.
- Agrégats horaires conservés, sondages bruts purgés selon la rétention.
- Journal des collectes, état persistant des logs.

### Restitution
- Interface web `freelm web` : 9 rapports, filtres, graphiques interactifs, mode sombre,
  liens partageables, exports Excel / CSV / JSON par rapport.
- Rapport Excel complet 8 onglets (`freelm report`).
- `FreeLM.exe` autonome pour Windows (double-clic → interface).

### Exploitation
- Verrou SQL Server empêchant deux collectes simultanées (pas de sessions en double).

### Outils
- `freelm demo` : simulateur de serveurs FlexLM (Autodesk, Ansys, MATLAB) et faux `lmutil`.
- `freelm import-users` (services AD), `import-log` (reprise d'historique), `apply-products`, `purge`.
- Scripts de tâches planifiées Windows.
- CI : tests Linux / Windows, SQL Server 2022, navigateur (Playwright), construction de l'exécutable ;
  publication automatique des releases.

[Non publié]: https://github.com/Valou94460/FREE-LM/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Valou94460/FREE-LM/releases/tag/v0.1.0
