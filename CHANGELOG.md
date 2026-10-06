# Journal des modifications

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) ;
numérotation `AA.N.C` : année, numéro de version dans l'année, correctif (ex. 26.1.0, 26.1.1, 26.2.0).

## [Non publié]

## [26.5.0] — 2026-10-06

### Nouveautés — « Prise en main »
- **Navigation en 6 entrées** (Tableau de bord, En direct, Analyse, Optimisation, Achats, Données) plus
  Administration pour les administrateurs ; les vues d'une rubrique sont des onglets. Les noms des vues sont
  inchangés et les anciens liens (`#type=…`) fonctionnent toujours. Toutes les options sont conservées.
- **Accueil « À traiter »** : pénuries, économies possibles, échéances proches, avec un bouton vers la vue utile.
- **Filtres contextuels** (ceux qui ne s'appliquent pas à la vue sont masqués, avec une note) et **tableaux
  lisibles** (colonnes essentielles, bouton pour afficher toutes les colonnes).
- **Aide intégrée** : glossaire en bulles « ? » sur les en-têtes, messages explicites sur les pages vides.
- **Visite guidée** à la première connexion (5 étapes selon le rôle), mémorisée côté serveur par utilisateur
  (schéma v10, table `user_pref`), relançable par « Revoir la visite guidée ». Non proposée sur la démonstration
  ni quand la page est ouverte sur un rapport précis.
- **Checklist de démarrage** de l'administrateur (serveurs, coûts, échéances, projets, e-mail) dans
  Administration et en carte « Mise en route » sur le tableau de bord (`/api/admin/checklist`).

## [26.4.0] — 2026-10-05

### Nouveautés
- **Prévision du besoin** (rubrique *Achats › Prévision*) : pic de jetons prévu à 3, 6 et 12 mois par produit,
  jetons nécessaires (pic prévu arrondi), avec marge (haut de la fourchette à 90 %), écart aux jetons émis et
  impact budgétaire annuel. Méthode explicable : pic hebdomadaire, tendance linéaire dès 8 semaines
  d'historique (prolongée au plus sur la durée de l'historique), saisonnalité mensuelle dès 12 mois. Produits
  Autodesk Cloud compris (utilisateurs distincts par jour). Graphique historique / prévision.
- **Rapport de renouvellement** (*Achats › Renouvellements*) : pour chaque échéance des 12 mois à venir (date
  d'expiration du fichier de licence, `renewal` d'une règle `products`, `autodesk.renewals` ou date saisie dans
  l'Administration pour les abonnements Autodesk) : usage réel des 90 derniers jours, jetons recommandés, besoin
  prévu, quantité à renouveler, coût actuel et proposé, économie ou budget. Jamais de baisse proposée pour un
  produit avec refus ou saturations fréquentes.
- **Refacturation par projet** : nouvel axe « Projet » dans la Répartition et le Croisement, coûts compris
  (licences Autodesk nommées imputées à leur titulaire). Projet déduit d'un import CSV utilisateur ; projet
  (export des membres d'un groupe AD, outil de gestion de projets) — `heptagoneslm import-projects` ou
  *Administration › Projets* — puis des règles `projects` (nom du poste, utilisateur, service). Schéma v9
  (table `user_project`).
- **Rapports planifiés sur mesure** : chaque abonnement e-mail peut choisir ses vues (Prévision,
  Renouvellements, Répartition par projet, Optimisation…), ses filtres (serveurs, produits, utilisateurs,
  services, sites), l'axe d'analyse et une période trimestrielle (`last-quarter`) ; le message reprend chaque
  vue et le classeur joint contient une feuille par vue. Gestion depuis *Administration › Rapports planifiés*
  (création, modification, suppression, envoi d'essai).
- *Administration › Coûts et échéances des licences Autodesk* : date de fin d'abonnement par produit.

### Corrections
- Un envoi d'essai (`send-report --to`, ou bouton « Envoyer un essai ») n'est plus enregistré comme l'envoi de la
  période : l'envoi planifié n'est plus sauté.

## [26.3.3] — 2026-10-04

Publie aussi le contenu de 26.3.1 et 26.3.2 (non publiées séparément).

### Nouveautés 26.3.3
- **Licences Autodesk nommées dans toutes les rubriques** : usage quotidien lu par l'API Autodesk (table
  `named_usage_day`, schéma v8, synchronisation incrémentale) ; produits Autodesk (Vault, collections…) en
  serveur « Autodesk Cloud » dans la Synthèse, l'Usage dans le temps, la Répartition, le Croisement,
  l'Efficacité, l'Optimisation & coûts et les filtres, en licences affectées et jours d'utilisation (aucune
  heure inventée). Coûts annuels par produit saisis dans *Administration › Coûts des licences Autodesk*.
- **Rubrique « Administration » de l'interface web**, réservée au groupe AD des administrateurs (nouveau champ
  de l'assistant, ou accueil du Setup › « Administrateurs de l'interface web » pour une installation
  existante) : ajout, modification, suppression et test en direct des serveurs de licences, et import d'un
  export Autodesk depuis le navigateur. Configuration validée avant écriture, sauvegarde et journal d'audit.
- **Garde-fou contre le simulateur en production** : si `heptagoneslm.yaml` pointe vers le faux `lmutil` des
  tests (`fake-lmutil`), la vérification et la recette passent en erreur, et le PV ne peut pas être conforme. Sur un
  environnement de test, `HEPTAGONESLM_ALLOW_SIMULATOR=1` ramène ce contrôle à un avertissement, qui reste une
  réserve dans le PV.
- **Licences Autodesk sur une installation existante** : bouton « Configurer les licences Autodesk » sur
  l'accueil du Setup (exports du portail et API Autodesk, connexion comprise). Seule la section `autodesk` de
  `heptagoneslm.yaml` est écrite, sauvegarde `.bak` faite avant ; le Client Secret est chiffré (DPAPI),
  l'interface est redémarrée et une collecte lancée, sans réinstaller ni ressaisir de mot de passe.
- **Bouton « Quitter l'assistant »** en fin d'installation, de mise à jour, de vérification et de recette
  (refusé tant qu'une opération est en cours) ; l'assistant s'arrête aussi seul après 30 minutes sans onglet
  ouvert.
- **Plus aucune fenêtre console** au double-clic sur `HeptagonesLM-Setup.exe` : le Setup devient une application
  fenêtrée et seul le navigateur s'affiche. En cas d'échec au démarrage, une boîte de message s'affiche. En ligne
  de commande, la sortie est reprise dans l'invite ou dans le fichier de redirection ; pour récupérer le code
  retour du mode silencieux, lancer avec `start /wait` ou `Start-Process -Wait`, comme pour un `.msi`.
- **PV de recette, rapport Excel et rapport d'installation relus** : pluriels corrects, serveur sans
  licence (rapport vide attendu, pas de tableau vide), nom réel du serveur et statuts en français dans le
  rapport d'installation, « page X sur Y », cases à cocher affichées.

### Ajouté
- **Icône HeptagonesLM sur le bureau** (et dans le menu Démarrer) en fin d'installation ou de mise à jour :
  ouvre le tableau de bord.
- **Relance automatique de l'interface web** : tâche « Surveillance de l'interface » (toutes les 5 minutes,
  sous SYSTEM) qui relance l'interface si elle ne tourne pas, par exemple après un redémarrage du serveur où
  le domaine ou la base n'étaient pas encore prêts. Les tâches tournent aussi sur batterie.
- **Mise à jour en place** : l'assistant reconnaît l'installation existante et sa version ; « Mettre à jour »
  sauvegarde (configuration, réponses, web.config, base SQLite), remplace l'exécutable, met à jour le schéma,
  redémarre l'interface, lance une collecte de contrôle puis la vérification — sans rien ressaisir
  (configuration, mots de passe, tâches et site IIS conservés). Mode silencieux : `setup --update`.
- **Réglages personnalisés conservés** quand l'assistant réécrit `heptagoneslm.yaml` (products, sites,
  rétention, coûts Autodesk, rôles, abonnements e-mail, types d'alertes).
- **Signature de code** de `HeptagonesLM-Setup.exe` et `HeptagonesLM.exe` (Authenticode horodaté) avec le
  certificat de l'éditeur (secrets GitHub `SIGN_PFX_BASE64` / `SIGN_PFX_PASSWORD`) ; procédure validée en CI
  avec un certificat de test.
- **Connecteur API Autodesk (Business Success Plan Reporting)** : l'usage des licences Autodesk nommées est
  lu directement chez Autodesk (API Insights : équipes, requêtes d'usage asynchrones paginées, limite de débit
  respectée), sans export manuel. Connexion OAuth 3 étapes d'un administrateur (PKCE, ou Client Secret chiffré
  par DPAPI), jeton de renouvellement chiffré et renouvelé à chaque synchronisation. Synchronisation par la
  collecte au plus toutes les 24 h, ou `autodesk-sync [--dry-run]` ; reconnexion par `autodesk-login`. Les
  licences affectées sans usage sur la période sont reprises du dernier export ou de la dernière
  synchronisation (inactives, jamais utilisées). Identifiants hachés pris en charge. Guide : `docs/AUTODESK_API.md`.
- **Assistant d'installation : étape « Licences Autodesk nommées »** (dossier surveillé des exports du portail,
  seuil d'inactivité, API Autodesk avec bouton « Se connecter à Autodesk » et choix de l'équipe).

### Ajouté
- **Licences Autodesk « utilisateur nommé »** : import des exports « Usage report » du portail Autodesk
  Account (CSV ou Excel, colonnes en anglais ou en français, présentation 2026 comprise) par
  `heptagoneslm import-autodesk export.csv` ou par dépôt dans un dossier surveillé (`autodesk.import_dir`,
  importé à la collecte suivante, une seule fois par fichier). Nouveau rapport « Autodesk · utilisateurs
  nommés » : licences actives, inactives (au-delà de `autodesk.inactive_days`, 30 jours par défaut) et jamais
  utilisées, coût annuel récupérable (`autodesk.annual_costs`), exportable comme les autres vues.
  Schéma v7 (tables `named_seat`, `named_import`) : exécuter `init-db` après la mise à jour.
- **Recette sur le site client** (étape 12 de l'assistant, ou depuis l'accueil quand HeptagonesLM est déjà
  installé) : collecte planifiée déclenchée, `lmstat` / `rlmstat` en direct comparé feature par feature à la
  base (lecture indépendante de la sortie brute), collecte planifiée sur N heures, vérification de bout en
  bout, connexion depuis les postes, refus d'un compte hors du groupe, e-mail, ticket ITSM et rapport Excel de
  test confirmés par l'opérateur. **PV de recette** Word à signer et archive des preuves.
- Sorties réelles des serveurs de licences (anonymisables) enregistrées par la recette et rejouées par les
  tests de non-régression (`tests/fixtures/reel`).
- **Vérification du serveur accélérée** : lectures en parallèle, IIS absent détecté sans `Get-WindowsFeature`,
  progression affichée.
- **Assistant d'installation : étape « Tickets ITSM »** (désactivée par défaut) : ServiceNow, Jira,
  GLPI ou Teams / Slack, gravité minimale, autorité de certification interne facultative ; bouton
  « Ouvrir un ticket de test » qui ouvre, commente puis résout un vrai ticket avec les identifiants
  saisis (refus 401 / 403, adresse 404 et certificat non reconnu expliqués). Aussi en mode silencieux
  (`itsm_*` dans le fichier de réponses).
- **Secrets chiffrés par Windows (DPAPI)** : mot de passe ou jetons de l'outil ITSM dans
  `<installation>\secrets\*.dpapi`, dossier réservé à SYSTEM, aux administrateurs et au compte de
  service ; la configuration n'en contient qu'une référence `dpapi:`, déchiffrée en mémoire au moment
  de l'appel. Vérification finale : secret présent et déchiffrable sur le serveur.

- Dossier d'exploitation 1.10 : recette et PV (§ 5.1, 5.3), section `autodesk` (§ 6.2), licences Autodesk
  nommées (§ 8.15).

### Corrigé
- Installation sur Windows Server 2025 : test de refus hors du groupe sans `ConvertTo-SecureString` (module
  parfois impossible à charger depuis PowerShell 7) ; état des rôles IIS lu par une requête CIM ciblée
  (vérification du serveur : 98,7 s → 10 s) ; création du site IIS fiabilisée (identifiant explicite, seconde
  tentative) ; version réelle du schéma dans le résumé de l'installation.
- Assistant : le message d'erreur d'un champ disparaît dès que le champ est corrigé.

## [26.3.0] — 2026-10-01

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
- Assistant d'installation : bouton **« Rechercher l'outil »** (étape Serveurs de licences). Il trouve
  lmutil.exe et rlmutil.exe sur ce serveur et sur les serveurs de licences déclarés (démons en cours
  d'exécution, services Windows, dossiers d'installation connus, Program Files) ; un outil trouvé sur
  un autre serveur est copié dans `<installation>\bin`.

### Corrigé
- **Démonstration signalée** : bandeau permanent « Démonstration — données fictives » et titre
  d'onglet « [Démonstration] » dès que l'interface tourne sur des données simulées (`web --demo`,
  option `web.demo`). Auparavant, rien ne distinguait la démonstration de vraies données.
- **HeptagonesLM.exe par double-clic** : n'ouvre plus la démonstration sans le dire. Il ouvre
  l'installation trouvée, y compris dans `C:\HeptagonesLM`, ou simplement le navigateur si l'interface
  est déjà servie ; sans installation, il propose d'installer (assistant, élévation) ou de voir la
  démonstration.
- Assistant d'installation : le chemin de lmutil / rlmutil accepte le dossier de l'outil et un chemin
  entre guillemets (« Copier en tant que chemin d'accès ») ; un dossier donné au test `lmstat`
  provoquait une erreur interne.
- Relance de la démonstration sur une base conservée (SQL Server) : réservations supprimées avant les features.
- Le filtre « Services » s'applique désormais aussi aux refus et aux utilisateurs de la vue En direct.
- La démonstration (`web --demo`) est régénérée après une mise à jour de l'application.
- Schéma v6 (colonne `license_session.borrowed`, table `feature_reservation`) ; les colonnes ajoutées
  prennent le type propre à SQL Server ou SQLite.

### Modifié
- **Un seul exécutable livré : `HeptagonesLM-Setup.exe`** (release, zip, artefact de la CI
  `HeptagonesLM-Setup`). `HeptagonesLM.exe` n'est plus proposé à part : l'assistant le contient et
  l'installe sur le serveur.
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

[Non publié]: https://github.com/Valou94460/HEPTAGONES-LM/compare/v26.4.0...HEAD
[26.4.0]: https://github.com/Valou94460/HEPTAGONES-LM/compare/v26.3.3...v26.4.0
[26.3.3]: https://github.com/Valou94460/HEPTAGONES-LM/compare/v26.3.0...v26.3.3
[26.3.0]: https://github.com/Valou94460/HEPTAGONES-LM/compare/v26.1.1...v26.3.0
[26.2.0]: https://github.com/Valou94460/HEPTAGONES-LM/compare/v26.1.1...v26.3.0
[26.1.1]: https://github.com/Valou94460/HEPTAGONES-LM/compare/v26.1.0...v26.1.1
[26.1.0]: https://github.com/Valou94460/HEPTAGONES-LM/releases/tag/v26.1.0
