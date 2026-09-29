# Journal des modifications

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) ;
numérotation `AA.N.C` : année, numéro de version dans l'année, correctif (ex. 26.1.0, 26.1.1, 26.2.0).

## [Non publié]

## [26.3.0] — 2026-09-29

Publie aussi le contenu de 26.2.0 (non publiée séparément).

### Ajouté
- **Tickets ITSM automatiques** (`alerts.itsm`) : ServiceNow (incident, API Table), Jira (Cloud et
  Data Center, API REST v2), GLPI (API REST) et webhook générique (JSON, Microsoft Teams, Slack).
  Chaque alerte ouvre un ticket, le complète à chaque rappel et le résout quand la condition
  disparaît ; sans doublon (table `itsm_ticket`), avec rattrapage si l'outil était injoignable ;
  filtres par type et gravité minimale (`min_severity`, « serious » par défaut).
- Commande `test-itsm` : ouvre, commente et résout un ticket de test dans chaque outil configuré.
- **Rôles** (`web.roles`) : administrateur, lecteur, responsable de service (ne voit que les
  utilisateurs de ses services — sessions, répartition, refus, En direct, exports — et en option
  certains serveurs ou produits), par groupe AD ou par compte ; contrôle par le serveur, refus 403
  tracés. Le module IIS transmet les groupes Windows (`X-Remote-Groups`).

- **Licences empruntées** (FlexLM, « linger ») : signalées dans les sessions et la vue En direct
  (indicateur « Jetons empruntés »).
- **Réservations / utilisateurs nommés** : lignes `RESERVATION` de lmstat mémorisées (table
  `feature_reservation`) ; vue « Réservations » avec le taux d'usage réel du bénéficiaire et une
  recommandation (réservation peu utilisée).
- **Multi-sites** : section `sites` (site des postes d'après leur nom), filtre et répartition par site,
  réappliqués à l'historique par `apply-products`.
- **Licences inutilisées** : vue dédiée (jamais utilisées sur la période, dernière utilisation,
  échéance, coût annuel).
- Démonstration enrichie : licence inutilisée, réservations, emprunts, trois sites.

### Corrigé
- Le filtre « Services » s'applique désormais aussi aux refus et aux utilisateurs de la vue En direct.
- La démonstration (`web --demo`) est régénérée après une mise à jour de l'application.
- Schéma v6 (colonne `license_session.borrowed`, table `feature_reservation`) ; les colonnes ajoutées
  prennent le type propre à SQL Server ou SQLite.

### Modifié
- Les alertes ont leur propre section `alerts` (destinataires e-mail, connecteurs ITSM, règles) ;
  la forme `email.alerts` reste acceptée. Schéma de base v6 (tables `itsm_ticket`, `feature_reservation`).

## [26.2.0] — 2026-09-29

### Ajouté
- **Alertes immédiates par e-mail** (`email.alerts`), évaluées après chaque collecte ou par
  `check-alerts` : collecte en échec (puis « rétablie ») ou arrêtée, nouveaux refus de licence,
  plus aucun jeton disponible, licence qui expire (paliers J-30, J-7, expirée). Anti-répétition
  (`repeat_hours`), filtres par type, serveur et produit ; aucune alerte perdue si le SMTP est
  indisponible. Option « Alertes immédiates » dans l'assistant d'installation (étape E-mail).
- **Collecteur RLM** (Reprise License Manager : Foundry, Maxon, Chaos…) : `rlmutil rlmstat -a`,
  pools additionnés par licence, utilisateurs, postes et échéances. `license_type: rlm` dans la
  configuration ; type de serveur et chemin de `rlmutil.exe` dans l'assistant, avec test en direct.

### Modifié
- Schéma de base v4 (table `alert_state`) : exécuter `init-db` après la mise à jour
  (fait automatiquement par l'assistant).
- Le type de gestionnaire (`license_type`) et les collecteurs d'un serveur sont vérifiés au
  chargement de la configuration.

## [26.1.1] — 2026-09-29

### Ajouté
- **Contrôles d'authentification unique** dans l'assistant d'installation (avant l'installation
  et à la vérification finale), avec la commande à exécuter :
  - SPN Kerberos `HTTP/<nom>` sur le compte ordinateur (absent : bouton « Déclarer le SPN » ;
    porté par un autre compte : commandes `setspn` de correction) ;
  - zone Intranet local (GPO d'attribution des sites aux zones) et stratégies
    `AuthServerAllowlist` d'Edge / Chrome.
- **Test d'accès depuis un poste utilisateur** : page `/acces` (nom reconnu, méthode Kerberos /
  NTLM, saisie ou non d'un mot de passe) ; les résultats remontent dans la vérification finale.
  Le module IIS transmet la méthode d'authentification (`X-Remote-Auth-Method`).
- Publication d'une version depuis l'onglet Actions (étiquette créée sur `main`), avec contrôle
  de la correspondance entre l'étiquette et la version du code.

## [26.1.0] — 2026-09-28

Première version publiée (développée sous le nom de travail FreeLM).

### Ajouté
- **Assistant d'installation** `HeptagonesLM-Setup.exe` / `heptagoneslm setup` : interface pas à pas dans le
  navigateur (serveur local protégé par jeton) ; contrôle des prérequis avec installation
  automatique des rôles IIS, d'URL Rewrite et d'ARR ; tests de connexion (compte de service,
  SQL Server, lmstat, certificat, groupe AD, LDAP, SMTP) ; création de la base et des droits ;
  configuration d'IIS (site HTTPS, authentification Windows, groupe autorisé, proxy vers HeptagonesLM) ;
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
- **Dossier d'exploitation** `docs/DEX_HeptagonesLM.docx` (Word, une soixantaine de pages, captures d'écran et schémas).
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
- **Rapport par e-mail** (`heptagoneslm send-report`) : abonnements avec destinataires, période et
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

### Modifié
- **Nouveau nom : HeptagonesLM** (anciennement FreeLM), avec le logo heptagone dans l'interface,
  l'assistant, l'icône des exécutables et la documentation. Commande `heptagoneslm`, exécutables
  `HeptagonesLM.exe` / `HeptagonesLM-Setup.exe`, configuration `heptagoneslm.yaml`, variables
  d'environnement `HEPTAGONESLM_*`, dossier par défaut `C:\HeptagonesLM`, tâches planifiées
  sous `\HeptagonesLM\`, dossier d'exploitation `docs/DEX_HeptagonesLM.docx`.

### Corrigé
- **Authentification unique derrière IIS** : l'utilisateur Windows n'était pas transmis à HeptagonesLM
  (variable `{LOGON_USER}` évaluée par URL Rewrite *avant* l'authentification, donc vide). Il est
  désormais transmis par un module ASP.NET (`App_Code\HeptagonesLMUserHeader.cs`) exécuté après
  l'authentification, qui ignore aussi tout en-tête `X-Remote-User` envoyé par le navigateur.
  L'assistant installe ASP.NET 4.x et configure le pool en .NET v4.0 ; procédure manuelle mise à
  jour dans `docs/AUTHENTIFICATION.md`.
- **Réinstallation / mise à jour** avec l'assistant : les tâches et processus HeptagonesLM en cours sont
  arrêtés avant le remplacement de `HeptagonesLM.exe` (fichier verrouillé), puis relancés.

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
- Interface web `heptagoneslm web` : 9 rapports, filtres, graphiques interactifs, mode sombre,
  liens partageables, exports Excel / CSV / JSON par rapport.
- Rapport Excel complet 8 onglets (`heptagoneslm report`).
- `HeptagonesLM.exe` autonome pour Windows (double-clic → interface).

### Exploitation
- Verrou SQL Server empêchant deux collectes simultanées (pas de sessions en double).

### Outils
- `heptagoneslm demo` : simulateur de serveurs FlexLM (Autodesk, Ansys, MATLAB) et faux `lmutil`.
- `heptagoneslm import-users` (services AD), `import-log` (reprise d'historique), `apply-products`, `purge`.
- Scripts de tâches planifiées Windows.
- CI : tests Linux / Windows, SQL Server 2022, navigateur (Playwright), construction de l'exécutable ;
  publication automatique des releases.

[Non publié]: https://github.com/Valou94460/HEPTAGONES-LM/compare/v26.3.0...HEAD
[26.3.0]: https://github.com/Valou94460/HEPTAGONES-LM/compare/v26.1.1...v26.3.0
[26.2.0]: https://github.com/Valou94460/HEPTAGONES-LM/compare/v26.1.1...v26.3.0
[26.1.1]: https://github.com/Valou94460/HEPTAGONES-LM/compare/v26.1.0...v26.1.1
[26.1.0]: https://github.com/Valou94460/HEPTAGONES-LM/releases/tag/v26.1.0
