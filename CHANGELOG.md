# Journal des modifications

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) ;
numérotation [SemVer](https://semver.org/lang/fr/).

## [Non publié]

### Ajouté
- **Assistant d'installation** `FreeLM-Setup.exe` / `freelm setup` : interface pas à pas dans le
  navigateur (serveur local protégé par jeton) ; contrôle des prérequis avec installation
  automatique des rôles IIS, d'URL Rewrite et d'ARR ; tests de connexion (compte de service,
  SQL Server, lmstat, certificat, groupe AD, LDAP, SMTP) ; création de la base et des droits ;
  configuration d'IIS (site HTTPS, authentification Windows, groupe autorisé, proxy vers FreeLM) ;
  tâches planifiées sous compte de service ; pare-feu ; barre de progression, journal en direct,
  reprise sur échec ; vérification de bout en bout et rapport d'installation.
- **Installation silencieuse** `setup --answers reponses.json` et **démonstration** `setup --simulate`.
- Documentation `docs/INSTALLATION.md`.
- **Tableau de bord** (nouvelle page d'accueil) : alertes classées par gravité (collecte en échec
  ou arrêtée, refus, licences expirées ou proches de l'expiration, saturation, sous-utilisation),
  6 indicateurs clés comparés à la période précédente avec micro-courbes et accès direct au
  rapport détaillé, tendance d'occupation, état en direct, occupation par produit, utilisateurs
  les plus actifs, refus par jour, profil horaire, serveurs et échéances.
- **En direct** : jetons utilisés / libres par produit, utilisateurs connectés (poste, service,
  durée), file d'attente ; actualisation automatique chaque minute.
- **Efficacité des jetons** : part du temps ouvré où au moins N jetons sont utilisés, nombre de
  jetons recommandé (`reporting.target_coverage`, 95 % par défaut), excédent.
- **Optimisation & coûts** : coût annuel par produit (`products[].annual_cost`,
  `reporting.currency`), économies potentielles, coût par heure d'usage, recommandation ;
  colonne coût dans la répartition (refacturation par service).
- **Dates d'expiration** lues dans la sortie `lmstat` (colonne `feature.expires_on`, schéma v3).
- **Dossier d'exploitation** `docs/DEX_FreeLM.docx` (Word, une soixantaine de pages, captures d'écran et schémas).
- **Nouvelle interface** : barre latérale de navigation, filtres en pastilles, préréglages de
  période en un clic, cartes et jauges, graphiques en aires, thème Auto / Clair / Sombre
  mémorisé, menu en tiroir sur mobile.
- **Connexion avec les comptes Windows / Active Directory** (`web.auth.mode: ldap`) : page de
  connexion, vérification LDAPS, restriction par groupes AD (imbriqués), sessions sécurisées,
  blocage après échecs répétés, bouton de déconnexion.
- **Authentification unique** derrière IIS (`web.auth.mode: header`).
- **HTTPS** intégré (`web.tls_cert` / `web.tls_key`) ; refus de démarrer la connexion AD en HTTP
  sur le réseau ; en-têtes de sécurité (HSTS, anti-iframe).
- **Journal d'audit** : connexions, échecs, déconnexions, exports (utilisateur et filtres).
- **Rapport par e-mail** (`freelm send-report`) : abonnements avec destinataires, période et
  filtres ; résumé HTML (pénuries, sous-utilisation) + classeur Excel joint ; une fois par
  période (`--skip-sent`), aperçu `.eml` (`--dry-run`), envoi de test (`--to`) ; journal
  `report_delivery` (schéma v2).
- Tâches planifiées « Envoi des rapports » et, en option, « Interface web » permanente.
- Documentation `docs/AUTHENTIFICATION.md`.

### Sécurité (audit du 27/09/2026 — voir `docs/SECURITE.md`)
- Protection contre le DNS rebinding : contrôle de l'en-tête `Host` (`web.allowed_hosts`).
- STARTTLS LDAP vérifié avant tout envoi du mot de passe.
- Tâches planifiées : compte de service obligatoire, SYSTEM refusé pour l'interface web.
- Serveur web : validation de `Content-Length`, délai d'expiration des requêtes, plafond de
  connexions simultanées, erreurs internes non divulguées, en-tête `Server` sans version.
- Exports CSV / Excel protégés contre l'injection de formules.
- Journal d'audit protégé contre l'injection de lignes ; message de refus unique à la connexion.
- Configuration : SMTP authentifié sans chiffrement refusé, avertissements `TrustServerCertificate`
  et `ldap_validate_cert: false`, exemples en `Encrypt=yes`, retrait de `db_ddladmin` documenté.
- Publication : construction sans droit d'écriture, empreintes `SHA256SUMS` jointes à la release.

### Corrigé
- **Authentification unique derrière IIS** : l'utilisateur Windows n'était pas transmis à FreeLM
  (variable `{LOGON_USER}` évaluée par URL Rewrite *avant* l'authentification, donc vide). Il est
  désormais transmis par un module ASP.NET (`App_Code\FreeLMUserHeader.cs`) exécuté après
  l'authentification, qui ignore aussi tout en-tête `X-Remote-User` envoyé par le navigateur.
  L'assistant installe ASP.NET 4.x et configure le pool en .NET v4.0 ; procédure manuelle mise à
  jour dans `docs/AUTHENTIFICATION.md`.

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
