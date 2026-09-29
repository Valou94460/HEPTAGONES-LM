# Plan de test HeptagonesLM

| | |
|---|---|
| **Version testée** | 26.1.1 |
| **Objet** | Valider HeptagonesLM avant la mise en production : collecte FlexLM, historisation SQL Server, interface web, exports, exécutable Windows, exploitation |
| **Documents liés** | [README](../README.md) · [Architecture](ARCHITECTURE.md) · [Configuration d'exemple](../config/heptagoneslm.example.yaml) |

## Sommaire

1. [Stratégie](#1-stratégie)
2. [Environnements](#2-environnements)
3. [Critères d'entrée, de sortie et gravité](#3-critères-dentrée-de-sortie-et-gravité)
4. [Tests automatisés](#4-tests-automatisés)
5. [Cas de test fonctionnels](#5-cas-de-test-fonctionnels) (installation, configuration, collecte, données, rapports, interface, exécutable, exploitation, connexion AD, e-mails)
6. [Validation sur les serveurs réels](#6-validation-sur-les-serveurs-réels)
7. [Performance](#7-performance)
8. [Sécurité](#8-sécurité)
9. [Robustesse](#9-robustesse)
10. [Recette métier](#10-recette-métier)
11. [Planning et procès-verbal](#11-planning-et-procès-verbal)

**Utilisation** : chaque cas porte un identifiant (`COL-03`…) et une case de statut.
Renseigner ✅ (conforme), ❌ (anomalie, avec le n° d'issue GitHub) ou ➖ (non applicable).
Copier ce fichier par campagne de test (`recette_2026-10.md`) ou reporter les statuts
dans un tableur.

---

## 1. Stratégie

| Niveau | Quoi | Comment | Qui | Où |
|---|---|---|---|---|
| **N1 — Unitaire** | Parsers lmstat / log, sessions, indicateurs, API, authentification, e-mails | 89 tests automatisés (`python -m pytest`) | CI | E1 |
| **N2 — Intégration** | Chaîne complète simulée : simulateur → parsers → base → rapports ; SQL Server réel ; exécutable Windows | Workflow CI (5 jobs) | CI | E1 |
| **N3 — Bout en bout** | Interface web et page de connexion dans un vrai navigateur | Playwright (`tests/ui/*.cjs`) | CI | E1 |
| **N4 — Recette fonctionnelle** | Cas de test manuels du § 5 sur les données de démonstration | Manuel | Admin applicatif | E2 |
| **N5 — Validation réelle (VSR)** | Exactitude des données face aux serveurs de licences réels | Procédure du § 6 | Admin applicatif + admin licences | E3 |
| **N6 — Recette métier** | Les rapports répondent aux questions des utilisateurs | Scénarios du § 10 | Responsables métier | E3 |
| **N7 — Exploitation** | Tâches planifiées, reprise sur incident, purge, performance | § 5.8, 7, 9 | Admin système | E3 |

Principe : les niveaux N1 à N3 tournent automatiquement à chaque modification ; la
campagne manuelle (N4 à N7) se concentre sur ce qu'un automate ne peut pas juger —
l'exactitude face à la réalité, l'installation sur **votre** infrastructure et
l'utilité des rapports.

## 2. Environnements

| Id | Environnement | Composants | Données |
|---|---|---|---|
| **E1** | CI GitHub Actions | Ubuntu + Windows, Python 3.10 / 3.12, SQL Server 2022 (conteneur), Chromium | Simulateur, fixtures |
| **E2** | Poste de test | `HeptagonesLM.exe` ou Python, SQLite | `heptagoneslm demo` / `heptagoneslm web --demo` |
| **E3** | Préproduction | Serveur Windows HeptagonesLM, base SQL Server **dédiée au test** (`HeptagonesLM_TEST`), accès en lecture aux serveurs de licences réels et à leurs logs | Réelles |
| **E4** | Production | Idem E3, base `HeptagonesLM` | Réelles |

Prérequis E3 : compte de service (ex. `DOMAINE\svc_heptagoneslm`) ; ouverture pare-feu vers
les ports `lmgrd` et des démons vendeurs ; partage en lecture des logs ; ODBC Driver 18 ;
droits `db_datareader`, `db_datawriter`, `db_ddladmin` sur la base de test.

Données de test à préparer pour E3 :
- une sortie `lmutil lmstat -a -c <port@serveur> > lmstat_<serveur>.txt` par serveur ;
- un extrait de log debug par serveur dont le log est accessible ;
- 2 ou 3 utilisateurs volontaires pour les tests contrôlés (§ 6.3) ;
- un export AD `users.csv` (§ 5.8).

## 3. Critères d'entrée, de sortie et gravité

**Entrée** : CI verte sur la version testée ; E3 disponible ; données de test prêtes.

**Sortie (mise en production autorisée)** :
- 100 % des cas **bloquants** (marqués 🔴) conformes ;
- aucune anomalie bloquante ou majeure ouverte ;
- validation réelle (§ 6) conforme sur chaque serveur de licences ;
- procès-verbal (§ 11) signé.

| Gravité | Définition | Exemple |
|---|---|---|
| **Bloquante** | Données fausses, perte de données, collecte ou interface inutilisable | Sessions non fermées, taux d'occupation faux |
| **Majeure** | Fonction importante dégradée, contournement possible | Export Excel en erreur, un onglet vide |
| **Mineure** | Gêne sans impact sur les données | Libellé tronqué, tri inversé |

## 4. Tests automatisés

Exécution locale : `pip install -e ".[dev]" && python -m pytest -v` (aucun serveur requis).
En CI : à chaque push et pull request ([ci.yml](../.github/workflows/ci.yml)).

### 4.1 Traçabilité

| Fichier | Tests | Couvre |
|---|---:|---|
| `tests/test_lmstat_parser.py` | 9 | Dates d'expiration (formats, permanente, échéance la plus proche), totaux émis/utilisés, checkouts (user, poste, handle, début, nb jetons), utilisateur avec espaces, files d'attente, features non comptées / en erreur, changement d'année, serveur injoignable |
| `tests/test_log_parser.py` | 7 | Types d'évènements, champs, passage de minuit, lignes TIMESTAMP, lignes sans date, préfixe date, lecture incrémentale (ligne partielle, pas de relecture), rotation |
| `tests/test_collection.py` | 8 | Cycle de vie des sessions lmstat (ouverture, fermeture estimée), nouveau handle, démon vendeur arrêté, sondages et agrégats horaires, journal en cas d'échec, sessions et évènements depuis le log, reprise d'historique antérieur, rendu du script SQL Server, vues |
| `tests/test_report.py` | 7 | Indicateurs (émis, pic, moyenne, taux, heures saturées, refus, utilisateurs, heures), profil horaire, contention, filtre serveur, mois précédent, export Excel (y compris période vide) |
| `tests/test_simulation.py` | 9 | Déterminisme du simulateur, jamais plus de jetons utilisés qu'émis, log conforme au modèle, faux lmutil, sessions en base identiques au modèle (lmstat et log), durées exactes (log), configuration « en direct » |
| `tests/test_web.py` | 14 | Paramètres (défauts, listes, validation), fichiers statiques et types MIME, métadonnées, les 13 vues (tableau de bord, en direct, efficacité, optimisation : cohérence des recommandations et des coûts), alertes (sévérités, regroupement, collecte arrêtée), filtres (serveur, utilisateur, produit), granularités, exports CSV / JSON / Excel (9 rapports + classeur complet), erreurs 400 / 404, import utilisateurs (format AD) |
| `tests/test_auth.py` | 16 | Mode ldap (routes protégées, redirection, connexion / déconnexion, cookie `HttpOnly` `SameSite`, cookie forgé, blocage après échecs, audit des exports, en-têtes de sécurité), mode header (proxy de confiance, utilisateur non autorisé, proxy non fiable), refus de l'AD sans HTTPS, configurations invalides, expiration des sessions, serveur HTTPS, **vrai code LDAP** contre l'annuaire fictif ldap3 (groupes imbriqués, mauvais mot de passe, mot de passe vide, restriction par groupe) |
| `tests/test_mailer.py` | 9 | Libellés de période, validation de la configuration, envoi réel à un serveur SMTP de test (destinataires, sujet, HTML, texte, lien, pièce jointe), abonnement filtré, une fois par période, aperçu `.eml`, erreur SMTP journalisée, destinataires de test, abonnement ponctuel |
| `tests/test_security.py` | 14 | Non-régression de l'audit ([SECURITE.md](SECURITE.md)) : DNS rebinding (Host forgé → 421), `Content-Length` négatif, connexion inactive coupée, plafond de connexions, en-tête `Server` sans version, erreur interne non divulguée, date hors limites, injection dans le journal d'audit, formules neutralisées (CSV, Excel), échec STARTTLS avant bind, SMTP authentifié sans chiffrement refusé |
| `tests/ui/login_smoke.cjs` | — | Navigateur réel : redirection vers la connexion, mauvais mot de passe, connexion `DOMAINE\identifiant`, retour au rapport demandé, nom affiché, export, déconnexion |
| `tests/test_setup.py` | 21 | Assistant d'installation : réponses et `heptagoneslm.yaml` généré (3 modes, validé par le chargeur réel), secrets jamais écrits, `web.config` IIS, échappement PowerShell, scripts sans mot de passe, prérequis et corrections, installation complète, échec puis reprise, plan selon le mode, vérification, serveur local (jeton, Host, anti-CSRF), mode silencieux |
| `tests/ui/setup_smoke.cjs` | — | Navigateur réel : parcours complet de l'assistant (simulation) : corrections automatiques, validation des champs, tous les tests de connexion, installation jusqu'à 100 %, vérification verte, rapport sans mot de passe, mobile |
| `tests/ui/ui_smoke.cjs` | — | Navigateur réel : tableau de bord (6 KPI, micro-courbes, blocs, clic KPI → rapport) et vue en direct en clair et en sombre, 11 rapports en clair, 3 en sombre, filtre multi-produits, infobulle, exports CSV / JSON / Excel / complet, bouton Précédent, absence de débordement mobile, **aucune erreur JavaScript** |

### 4.2 Jobs de la CI

| Job | Vérifie |
|---|---|
| Tests + démo (Linux, Windows × Python 3.10, 3.12) | Tests unitaires ; script SQL à jour ; démo 30 jours + rapport Excel ; collecte « en direct » via le faux lmutil ; relecture des classeurs ; rendu PDF (LibreOffice) |
| Interface web (navigateur) | `tests/ui/ui_smoke.cjs`, `tests/ui/login_smoke.cjs`, `tests/ui/setup_smoke.cjs` + captures d'écran publiées |
| Installation réelle sur Windows Server | `HeptagonesLM-Setup.exe setup --answers` sur un runner Windows : installation d'IIS, d'URL Rewrite et d'ARR, site HTTPS (certificat), tâches sous un compte de service local, authentification unique vérifiée à travers IIS, collecte planifiée, réinstallation (idempotence) |
| Exécutable Windows | Construction de `HeptagonesLM.exe` ; `--version` ; démo ; collecte ; interface ; export du classeur complet |
| SQL Server 2022 | Création de base ; démo 14 jours ; `init-db` ×2 (idempotence) ; collecte ; purge ; rapport ; verrou anti-collectes simultanées ; aperçu d'e-mail ; vues SQL non vides ; relance de la démo |

| Id | Cas | Résultat attendu | Statut |
|---|---|---|:---:|
| AUT-01 🔴 | Consulter la dernière exécution CI de la version testée | Tous les jobs verts | ☐ |
| AUT-02 | Télécharger l'artefact `captures-interface-web` | Captures lisibles, cohérentes avec § 5.6 | ☐ |
| AUT-03 | `python -m pytest` sur le poste de test (E2) | 89 tests passés (« skipped » uniquement si openpyxl, ldap3 ou openssl sont absents) | ☐ |

---

## 5. Cas de test fonctionnels

Sauf mention contraire : E2 avec `heptagoneslm web --demo` / `heptagoneslm demo`, puis rejoués
sur E3 pour les cas marqués **[E3]**.

### 5.1 Installation (INS)

| Id | Objectif | Étapes | Résultat attendu | Statut |
|---|---|---|---|:---:|
| INS-01 🔴 | Exécutable autonome | Copier `HeptagonesLM.exe` sur un poste **sans Python** ; `HeptagonesLM.exe --version` | Affiche `HeptagonesLM 26.1.1` | ☐ |
| INS-02 | Installation Python | `py -3 -m venv venv` ; `venv\Scripts\pip install .` ; `heptagoneslm --version` | Installation sans erreur, version affichée | ☐ |
| INS-03 🔴 [E3] | Création de la base | DBA : `sqlcmd -S … -i sql\sqlserver\00_create_database.sql` (base de test) | Base et compte de service créés, rôles attribués | ☐ |
| INS-04 🔴 [E3] | Création du schéma | `HeptagonesLM.exe -c heptagoneslm.yaml init-db` | Message « Schéma créé / mis à jour, N serveur(s) » ; tables et 3 vues visibles dans SSMS | ☐ |
| INS-05 [E3] | Idempotence | Relancer `init-db` 2 fois | Aucune erreur, aucune donnée perdue, `schema_info` contient 1 ligne | ☐ |
| INS-06 [E3] | Script SQL manuel | Exécuter `sql\sqlserver\01_schema.sql` sur une base vide, puis `init-db` | Schéma identique, `init-db` sans erreur | ☐ |
| INS-07 [E3] | Pilote ODBC absent | Sur un poste sans ODBC Driver 18 : `init-db` | Message d'erreur explicite du pilote, code retour 1 | ☐ |

### 5.2 Configuration (CFG)

| Id | Étapes | Résultat attendu | Statut |
|---|---|---|:---:|
| CFG-01 | `-c` vers un fichier inexistant | « Fichier de configuration introuvable », code 1 | ☐ |
| CFG-02 | Supprimer `database.connection_string` | « database.connection_string est obligatoire » | ☐ |
| CFG-03 | Serveur sans `port_at_host` | « servers[i] : champ obligatoire manquant 'port_at_host' » | ☐ |
| CFG-04 | Deux serveurs de même nom | « Nom de serveur en double » | ☐ |
| CFG-05 | `sessions_from: log` sans le collecteur `log` | Erreur « ce collecteur n'est pas activé » | ☐ |
| CFG-06 | Collecteur `log` sans `log_path` | « log_path est obligatoire » | ☐ |
| CFG-07 | Mot de passe SQL via `${HEPTAGONESLM_DB_PASSWORD}` (variable d'environnement du compte de service) | Connexion réussie ; le mot de passe n'apparaît pas dans le YAML | ☐ |
| CFG-08 | Règle produit `*_Toolbox` avec `vendor: MLM` | Seules les features MLM correspondantes prennent le produit | ☐ |
| CFG-09 | Modifier une règle produit puis `apply-products` | Le nouveau libellé apparaît sur tout l'historique (interface, rapports) | ☐ |

### 5.3 Collecte lmstat (COL)

| Id | Objectif | Étapes | Résultat attendu | Statut |
|---|---|---|---|:---:|
| COL-01 🔴 [E3] | Connexion à chaque serveur | `test-server --server X` pour chaque serveur | UP, démons vendeurs UP, liste des features avec `utilisés/émis` | ☐ |
| COL-02 🔴 [E3] | Collecte nominale | `collect` | Une ligne `OK` par serveur/collecteur, code retour 0 ; lignes dans `usage_snapshot`, `usage_hourly`, `collection_run` | ☐ |
| COL-03 🔴 [E3] | Serveur injoignable | Ajouter un serveur fictif `27000@inexistant` ; `collect` | Ce serveur `ERROR` (message lmgrd), **les autres `OK`**, code retour 2, échec visible dans l'onglet Collectes et le badge d'état | ☐ |
| COL-04 | lmutil absent | `lmutil_path` erroné ; `collect` | `ERROR` « lmutil introuvable », pas de trace Python | ☐ |
| COL-05 | Délai dépassé | `timeout_s: 1` sur un serveur lent / filtré | `ERROR` « délai dépassé (1s) » | ☐ |
| COL-06 [E3] | Triade redondante | `port_at_host: "p@a:p@b:p@c"` | Collecte `OK` | ☐ |
| COL-07 [E3] | Démon vendeur arrêté | Arrêter un démon (fenêtre de maintenance) pendant une session ouverte ; `collect` | Sessions de ce démon **restent ouvertes** (pas de fermeture à tort) | ☐ |
| COL-08 | Features non comptées / en erreur | `parse-lmstat` sur une sortie contenant `Uncounted` et `Error:` | Émis vide pour la feature non comptée, erreur signalée pour l'autre, pas de plantage | ☐ |
| COL-09 🔴 [E3] | Diagnostic de parsing | `parse-lmstat lmstat_<serveur>.txt` pour chaque sortie réelle | Toutes les features et tous les utilisateurs de la sortie texte sont présents, heures de début correctes | ☐ |

### 5.4 Collecte des logs (LOG)

| Id | Objectif | Étapes | Résultat attendu | Statut |
|---|---|---|---|:---:|
| LOG-01 🔴 [E3] | Diagnostic de parsing | `parse-log <log réel>` | Évènements OUT / IN / DENIED avec date et heure correctes ; nb « ignorés faute de date » = 0 (ou uniquement en début de fichier) | ☐ |
| LOG-02 🔴 [E3] | Lecture incrémentale | `collect` deux fois de suite | 2ᵉ exécution : 0 élément (ou seulement les nouveaux) — pas de doublon de refus ni de session | ☐ |
| LOG-03 [E3] | Rotation / redémarrage de lmgrd | Redémarrer le service de licences (fenêtre de maintenance) puis `collect` | Rotation détectée (journal), lecture reprise au début du nouveau fichier, sessions ouvertes fermées (`server_down`) | ☐ |
| LOG-04 | Partage inaccessible | Renommer temporairement `log_path` ; `collect` | `ERROR` « Log introuvable » pour le collecteur log, lmstat `OK` | ☐ |
| LOG-05 [E3] | Reprise d'historique | `import-log --server X` sur 1 à 3 anciens logs (ordre chronologique) | Sessions et refus historiques visibles dans l'interface sur les périodes passées | ☐ |
| LOG-06 | Passage de minuit | Session ouverte avant minuit, fermée après | Session sur deux jours, durée correcte | ☐ |

### 5.5 Exactitude des données (DAT)

À dérouler en démonstration (E2) : le simulateur connaissant la vérité, on peut
comparer. La validation sur les serveurs réels fait l'objet du § 6.

| Id | Étapes | Résultat attendu | Statut |
|---|---|---|:---:|
| DAT-01 🔴 | Onglet Synthèse, 30 derniers jours | Pour chaque produit : pic ≤ jetons émis ; taux au pic = pic / émis ; heures saturées > 0 ⇔ taux au pic = 100 % | ☐ |
| DAT-02 | Profil horaire | Heures ouvrées (8 h–18 h) nettement plus chargées ; nuits et week-ends quasi nuls | ☐ |
| DAT-03 | Refus | Refus uniquement sur ANSYS (seul serveur avec log dans la démo) | ☐ |
| DAT-04 | Répartition par utilisateur → filtrer un utilisateur → Sessions | Total d'heures de Répartition = somme de la colonne « Heures (période) » des sessions | ☐ |
| DAT-05 | Export CSV de Sessions → somme dans Excel | Égal au total affiché dans l'interface | ☐ |
| DAT-06 | Même période : KPI « Heures d'utilisation » (Synthèse) vs total Répartition par produit | Identiques | ☐ |

### 5.6 Interface web (WEB)

Lancer `HeptagonesLM.exe` (double-clic) ou `heptagoneslm web --demo --open`.

| Id | Objectif | Étapes | Résultat attendu | Statut |
|---|---|---|---|:---:|
| WEB-01 🔴 | Ouverture | Lancer l'interface | Tableau de bord affiché, badge « Collectes OK », plage des données affichée (barre latérale) | ☐ |
| WEB-02 🔴 | 13 vues | Cliquer chaque entrée de la barre latérale | Chaque onglet affiche son tableau (et son graphique le cas échéant), aucun message d'erreur | ☐ |
| WEB-03 | Préréglages de période | Tester 7 j, 30 j, mois en cours, mois précédent, année, toutes les données | Dates « Du / Au » cohérentes, données rechargées | ☐ |
| WEB-04 | Dates personnalisées | Saisir des dates | Préréglage passe à « Personnalisée », données de la période | ☐ |
| WEB-05 | Début après la fin | Du > Au | Message « La date de début doit précéder la date de fin » | ☐ |
| WEB-06 🔴 | Filtres multiples | Serveurs = ANSYS ; Produits = 2 produits ; Utilisateurs = 1 ; Services = 1 | Tableaux et graphiques limités à la sélection ; « Tout effacer » rétablit | ☐ |
| WEB-07 | Recherche dans un filtre | Taper « ans » dans Produits | Liste filtrée ; « Tout cocher » ne coche que les éléments visibles | ☐ |
| WEB-08 | Note sur les filtres utilisateurs | Filtre utilisateur actif sur Synthèse | Note : les taux restent des compteurs globaux | ☐ |
| WEB-09 | Usage dans le temps | Pas heure / jour / semaine / mois × pic / moyenne / taux | Axe et courbes adaptés ; > 8 produits → note « graphique limité », tableau complet | ☐ |
| WEB-10 | Couleur stable | Filtrer 3 produits puis en retirer 1 | Les 2 restants gardent leur couleur | ☐ |
| WEB-11 | Infobulles | Survoler courbes, barres, cellules de la carte de chaleur | Valeurs exactes et libellés affichés | ☐ |
| WEB-12 | Répartition et Croisement | Changer « Par » (utilisateur, poste, service, produit, serveur, éditeur) | Regroupement correct ; « (non renseigné) » si service absent | ☐ |
| WEB-13 | Tableau | Tri par clic sur en-tête (asc/desc) ; recherche ; « Afficher plus » (Sessions) | Tri correct (numérique pour les nombres) ; filtrage immédiat ; compteur « N / M lignes » | ☐ |
| WEB-14 🔴 | Lien partageable | Régler des filtres, copier l'URL, l'ouvrir dans un autre navigateur | Même onglet, mêmes filtres, mêmes données | ☐ |
| WEB-15 | Précédent / Suivant | Changer d'onglet puis bouton Précédent du navigateur | Retour à l'onglet et aux filtres précédents | ☐ |
| WEB-16 | Période sans données | Année précédente sur la démo | « Aucune donnée pour ces filtres », pas d'erreur | ☐ |
| WEB-17 | Mode sombre | Système en thème sombre | Interface et graphiques lisibles | ☐ |
| WEB-18 | Largeur mobile / fenêtre étroite | Réduire la fenêtre à ~400 px | Pas de défilement horizontal de la page | ☐ |
| WEB-19 | Navigateurs | Edge et Chrome (et Firefox si utilisé) | Comportement identique | ☐ |
| WEB-20 | Port occupé | Lancer deux instances | La 2ᵉ utilise le port suivant (8766) et l'indique | ☐ |
| WEB-21 | Badge d'état en échec | Provoquer COL-03 puis recharger | Badge « ⚠ 1 collecteur(s) en échec » ; clic → onglet Collectes | ☐ |
| WEB-22 🔴 | Indicateurs du tableau de bord | Comparer les 6 KPI aux vues détaillées (Synthèse, Répartition, Contention, Refus, Optimisation) sur la même période | Valeurs identiques ; variation « vs » cohérente avec la période précédente de même durée ; couleur verte = amélioration | ☐ |
| WEB-23 | KPI cliquables | Cliquer chaque carte KPI | Ouvre le rapport détaillé correspondant avec les mêmes filtres | ☐ |
| WEB-24 🔴 | Alertes | Arrêter la tâche de collecte 1 h ; provoquer un refus ; licence expirant sous 30 j | Alerte « Collecte arrêtée » (critique), « refus » (important), « expirent dans les 30 jours » (attention) ; « Voir » ouvre la vue concernée | ☐ |
| WEB-25 🔴 | En direct | Ouvrir un produit sur un poste, attendre le sondage suivant | Carte du produit : jeton compté, utilisateur, poste et durée affichés ; actualisation automatique en moins de 1 min après le sondage | ☐ |
| WEB-26 | Échéances | Comparer « Serveurs et échéances » à `lmutil lmstat -i` | Mêmes dates d'expiration ; licences permanentes absentes | ☐ |
| WEB-27 | Efficacité | Filtrer un seul produit | Courbe en nombre de jetons, décroissante ; jetons recommandés ≤ pic observé | ☐ |
| WEB-28 | Optimisation et coûts | Renseigner `annual_cost` pour 2 produits | Coût annuel = coût × jetons ; économie = excédent × coût ; aucun produit avec refus proposé à la baisse ; sans `annual_cost` : note explicative | ☐ |
| WEB-29 | Thème | Barre latérale : Auto / Clair / Sombre, puis recharger | Thème appliqué immédiatement et conservé au rechargement | ☐ |
| WEB-30 | Menu mobile | Fenêtre ~400 px : bouton menu | Barre latérale en tiroir ; se ferme après choix d'une vue ou Échap | ☐ |

### 5.7 Exports (EXP)

| Id | Étapes | Résultat attendu | Statut |
|---|---|---|:---:|
| EXP-01 🔴 | Excel sur chaque onglet | Fichier `HeptagonesLM_<rapport>_<début>_<fin>.xlsx` ; s'ouvre dans Excel sans réparation ; titre, filtres appliqués, tableau Excel, graphique natif (onglet « Graphique ») | ☐ |
| EXP-02 🔴 | CSV | S'ouvre par double-clic dans Excel français : colonnes séparées, accents corrects, décimales à virgule, pourcentages en % | ☐ |
| EXP-03 | JSON | Fichier valide, mêmes lignes que l'écran | ☐ |
| EXP-04 🔴 | Rapport complet | Classeur 8 onglets (Synthèse, Usage quotidien, Utilisateurs, Profil horaire, Contention, Refus, Sessions, À propos), filtres serveurs/produits appliqués | ☐ |
| EXP-05 | Cohérence export / écran | Comparer 5 lignes au hasard | Valeurs identiques (hors arrondi d'affichage) | ☐ |
| EXP-06 | Tableau croisé dynamique | Dans l'export complet, onglet Sessions : Insertion › Tableau croisé dynamique | TCD créé sur le Tableau Excel sans retraitement | ☐ |
| EXP-07 | Rapport en ligne de commande | `report --period last-month` puis `report --from … --to … --server …` | Fichiers dans `reporting.output_dir`, contenu conforme à la période | ☐ |
| EXP-08 | `--skip-existing` | Relancer `report --period last-month --skip-existing` | « Rapport déjà présent, rien à faire », fichier inchangé | ☐ |
| EXP-09 | Erreur d'export visible | (E2 Python sans openpyxl) cliquer Excel | Message « Export impossible : Module manquant : openpyxl » | ☐ |

### 5.8 Exécutable, planification, référentiel, maintenance (EXE / PLN / USR / MNT)

| Id | Objectif | Étapes | Résultat attendu | Statut |
|---|---|---|---|:---:|
| EXE-01 🔴 | Double-clic sans configuration | Double-clic sur `HeptagonesLM.exe` dans un dossier vide | Console « Mode démonstration… », navigateur ouvert sur l'interface | ☐ |
| EXE-02 🔴 [E3] | Double-clic avec configuration | Placer `heptagoneslm.yaml` à côté de l'exe, double-clic | Interface sur les **données réelles** | ☐ |
| EXE-03 | Fermeture | Fermer la console | Interface arrêtée (page injoignable) | ☐ |
| EXE-04 | SmartScreen / antivirus | Premier lancement sur un poste standard | Avertissement éventuel contournable ; pas de blocage antivirus (sinon : liste blanche) | ☐ |
| PLN-01 🔴 [E3] | Enregistrement des tâches | `scripts\register_tasks.ps1 -HeptagonesLMHome C:\HeptagonesLM -ServiceAccount DOMAINE\svc_heptagoneslm` | 3 tâches sous `\HeptagonesLM\` dans le Planificateur | ☐ |
| PLN-02 🔴 [E3] | Collecte toutes les 5 min | Attendre 30 min | 6 exécutions `OK` par collecteur dans l'onglet Collectes ; pas de chevauchement | ☐ |
| PLN-03 [E3] | Droits du compte de service | Tâche exécutée sans session ouverte | Collecte OK (accès réseau lmgrd, partage des logs, SQL Server en authentification Windows) | ☐ |
| PLN-04 [E3] | Rapport mensuel | Exécuter manuellement la tâche « Rapport mensuel » deux fois | Rapport du mois précédent créé une seule fois | ☐ |
| PLN-05 [E3] | Redémarrage du serveur HeptagonesLM | Redémarrer la machine | Tâches reprennent sans intervention | ☐ |
| USR-01 [E3] | Import AD | Export `Get-ADUser … | Export-Csv users.csv` ; `import-users users.csv` | « N mis à jour, M créés » ; services visibles dans le filtre Services et la Répartition par service | ☐ |
| USR-02 | Colonnes françaises / séparateur `;` / encodage ANSI | CSV `utilisateur;nom;service` enregistré par Excel | Import correct, accents conservés | ☐ |
| USR-03 | Colonne identifiant absente | CSV sans colonne reconnue | Message listant les noms de colonnes attendus | ☐ |
| MNT-01 [E3] | Purge | `retention.snapshots_days: 1` (base de test) ; `purge` | Sondages bruts anciens supprimés ; **agrégats horaires et rapports inchangés** | ☐ |
| MNT-02 [E3] | Sauvegarde / restauration | Sauvegarde SQL puis restauration sur une autre base | Interface fonctionnelle sur la base restaurée | ☐ |
| MNT-03 | Mise à jour de version | Remplacer l'exe / réinstaller le paquet, `init-db` | Données conservées, interface fonctionnelle | ☐ |

### 5.9 Connexion Windows / Active Directory (AUTH)

Environnement E3, configuration selon [AUTHENTIFICATION.md](AUTHENTIFICATION.md) ;
prévoir un compte de test membre du groupe autorisé et un compte non membre.

| Id | Objectif | Étapes | Résultat attendu | Statut |
|---|---|---|---|:---:|
| AUTH-01 🔴 [E3] | Sécurité au démarrage | `auth.mode: ldap`, `host: 0.0.0.0`, **sans** certificat ; lancer `web` | Refus : « Connexion AD sur le réseau sans HTTPS… », code 1 | ☐ |
| AUTH-02 🔴 [E3] | HTTPS | Certificat de l'AC d'entreprise ; ouvrir `https://heptagoneslm…:8765` depuis un poste du domaine | Cadenas sans avertissement ; `http://` refusé | ☐ |
| AUTH-03 🔴 [E3] | Redirection | Ouvrir un lien de rapport partagé sans être connecté | Page de connexion ; après connexion, retour **sur le rapport du lien** | ☐ |
| AUTH-04 🔴 [E3] | Connexion nominale | Compte membre du groupe, formats `jdupont`, `DOMAINE\jdupont`, `jdupont@domaine` | Connexion ; nom complet affiché en haut à droite | ☐ |
| AUTH-05 🔴 [E3] | Mauvais mot de passe | Saisir un mot de passe erroné | « Identifiant ou mot de passe incorrect » ; échec dans le journal d'audit | ☐ |
| AUTH-06 🔴 [E3] | Restriction par groupe | Compte valide **hors** du groupe autorisé | « Accès refusé : vous n'appartenez à aucun groupe autorisé » | ☐ |
| AUTH-07 [E3] | Groupe imbriqué | Compte membre d'un groupe lui-même membre du groupe autorisé | Connexion acceptée | ☐ |
| AUTH-08 [E3] | Blocage | 5 échecs consécutifs puis bon mot de passe | « Trop de tentatives… » pendant 15 min ; **compte AD non verrouillé** (seuil HeptagonesLM < seuil AD) | ☐ |
| AUTH-09 [E3] | Déconnexion | « Se déconnecter », puis bouton Précédent du navigateur | Retour à la connexion ; données non accessibles | ☐ |
| AUTH-10 [E3] | Expiration | `session_hours: 0.05` (3 min) ; attendre 4 min puis changer d'onglet | Retour à la page de connexion | ☐ |
| AUTH-11 [E3] | Redondance des DC | Deux `ldap_servers`, le premier arrêté / filtré | Connexion via le second | ☐ |
| AUTH-12 [E3] | Annuaire injoignable | Tous les DC filtrés | « Annuaire indisponible, réessayez plus tard » ; cause dans `heptagoneslm.log` | ☐ |
| AUTH-13 🔴 [E3] | Journal d'audit | Se connecter, exporter un rapport, se déconnecter | `heptagoneslm.log` : connexion, export (utilisateur, rapport, format, filtres), déconnexion ; **aucun mot de passe** | ☐ |
| AUTH-14 [E3] | SSO IIS (si mode header retenu) | Poste du domaine, navigateur Edge | Accès **sans saisie** ; nom affiché ; accès direct au port HeptagonesLM impossible depuis un autre poste | ☐ |
| AUTH-15 [E3] | Usurpation d'en-tête (mode header) | Requête vers IIS avec un en-tête `X-Remote-User: admin` forgé | Ignoré : l'utilisateur reste celui authentifié par IIS | ☐ |
| AUTH-16 [E3] | Service permanent | `register_tasks.ps1 … -WithWebInterface` ; redémarrer le serveur | Interface disponible sans intervention | ☐ |

### 5.10 Rapport par e-mail (MAIL)

Prévoir 2 abonnements dans le YAML (tous produits / un seul serveur) et une boîte de test.

| Id | Objectif | Étapes | Résultat attendu | Statut |
|---|---|---|---|:---:|
| MAIL-01 🔴 | Aperçu sans envoi | `send-report --dry-run apercu` | Un `.eml` par abonnement, ouvrable dans Outlook ; rien n'est envoyé ni tracé | ☐ |
| MAIL-02 🔴 [E3] | Envoi de test | `send-report --to moi@entreprise.local` | Message reçu (pas en indésirables) ; sujet « HeptagonesLM — Utilisation des licences <mois> — <abonnement> » | ☐ |
| MAIL-03 🔴 [E3] | Contenu | Lire le message reçu | Indicateurs, produits en pénurie / sous-utilisés, tableau par produit, lien qui ouvre l'interface **sur la bonne période et les bons filtres** ; lisible dans Outlook (bureau, web, mobile) | ☐ |
| MAIL-04 🔴 [E3] | Pièce jointe | Ouvrir le classeur joint | Classeur 8 onglets du mois précédent, filtré comme l'abonnement | ☐ |
| MAIL-05 | Abonnement filtré | Abonnement `servers: [ANSYS]` | Seuls les produits Ansys apparaissent | ☐ |
| MAIL-06 🔴 [E3] | Une fois par période | Lancer 2 fois `send-report --skip-sent` | 2ᵉ exécution : `SKIPPED déjà envoyé…` ; un seul message reçu | ☐ |
| MAIL-07 [E3] | Rattrapage | Tâche « Envoi des rapports » désactivée le 1er, réactivée le 2 | Envoi le 2, une seule fois | ☐ |
| MAIL-08 [E3] | SMTP indisponible | `smtp_host` erroné | `ERROR` + code retour 2 ; échec tracé dans `report_delivery` ; nouvel essai le lendemain | ☐ |
| MAIL-09 [E3] | Authentification SMTP | `security: starttls`, port 587, `username` / `password: ${HEPTAGONESLM_SMTP_PASSWORD}` | Envoi réussi ; mot de passe absent du YAML et des journaux | ☐ |
| MAIL-10 | Pièce jointe trop lourde | `max_attachment_mb: 0.01` | Message envoyé sans pièce jointe, avec la mention « Classeur Excel non joint… » | ☐ |
| MAIL-11 | Configuration invalide | Abonnement sans destinataire ; période inconnue ; abonnements sans `smtp_host` | Erreur de configuration explicite | ☐ |
| MAIL-12 [E3] | Traçabilité | `SELECT * FROM report_delivery ORDER BY sent_at DESC` | Une ligne par envoi : abonnement, période, destinataires, statut | ☐ |

---

### 5.11 Assistant d'installation (INST)

Sur un serveur Windows de recette vierge, avec `HeptagonesLM-Setup.exe`.

| Id | Objectif | Étapes | Résultat attendu | Statut |
|---|---|---|---|:---:|
| INST-01 🔴 | Élévation | Double-clic sur HeptagonesLM-Setup.exe | Demande d'élévation UAC ; console avec l'adresse ; navigateur ouvert sur l'assistant | ☐ |
| INST-02 | Accès protégé | Ouvrir `http://127.0.0.1:<port>/` sans le jeton, depuis un autre profil | 403 | ☐ |
| INST-03 🔴 | Prérequis | Étape 2 sur un serveur sans IIS | Rôles IIS, URL Rewrite, ARR signalés ; « Installer » les installe ; relance de la vérification verte | ☐ |
| INST-04 | Hors ligne | Serveur sans Internet, MSI déposés dans `prerequis\` | Modules installés depuis les fichiers locaux | ☐ |
| INST-05 | Compte de service | Tester un compte inconnu, puis un mauvais mot de passe, puis le bon | Erreur explicite, puis « mot de passe valide » | ☐ |
| INST-06 🔴 | Base | Tester l'instance ; installer avec « Créer la base » | Base créée (SIMPLE), connexion Windows du compte de service, rôles lecture / écriture | ☐ |
| INST-07 | Licences | Tester chaque serveur ; tester une adresse erronée | Nombre de features et de jetons ; erreur « injoignable » | ☐ |
| INST-08 🔴 | IIS | Mode IIS, certificat du magasin, groupe AD | Site HTTPS démarré, authentification Windows seule, accès limité au groupe ; un membre du groupe arrive connecté sous son nom (en-tête `X-Remote-User` forgé par le navigateur ignoré) | ☐ |
| INST-09 🔴 | Progression | Lancer l'installation | Barre de 0 à 100 %, étapes cochées, journal détaillé | ☐ |
| INST-10 | Reprise | Provoquer un échec (compte sans droit), corriger, « Réessayer » | Reprise à l'étape en échec, installation terminée | ☐ |
| INST-11 🔴 | Vérification | Fin d'installation | Tous les contrôles verts ; « Ouvrir HeptagonesLM » ouvre l'interface sans saisie de mot de passe | ☐ |
| INST-12 | Réinstallation | Relancer l'assistant avec les mêmes réponses | Aucune erreur ; ancienne configuration sauvegardée en `.bak` | ☐ |
| INST-13 | Silencieux | `HeptagonesLM-Setup.exe setup --answers reponses.json` | Progression texte, code retour 0, rapport dans `logs\setup-*.log` | ☐ |
| INST-14 | Secrets | Rechercher les mots de passe dans `heptagoneslm.yaml`, `logs\*`, le rapport | Absents | ☐ |
| INST-15 🔴 | SPN / zone Intranet | Vérification finale avec un nom DNS alias ; « Déclarer le SPN » (compte administrateur du domaine) | SPN déclaré sur le compte ordinateur, puis contrôle vert ; zone Intranet signalée si la GPO est absente, avec la commande | ☐ |
| INST-16 🔴 | Test depuis un poste | Depuis 2 postes (membre du groupe, poste hors GPO), ouvrir `/acces`, répondre, puis « Relancer la vérification » | Utilisateur reconnu avec la méthode (Kerberos / NTLM) ; poste sans GPO signalé « mot de passe demandé » ; non-membre refusé | ☐ |

## 6. Validation sur les serveurs réels

Objectif : prouver que les chiffres de HeptagonesLM sont **ceux de la réalité**, pour chaque
serveur de licences (Autodesk, Ansys, MATLAB…). Environnement E3, collecte planifiée
active depuis au moins 24 h.

### 6.1 Contrôle instantané (par serveur)

| Id | Étapes | Critère d'acceptation | Statut |
|---|---|---|:---:|
| VSR-01 🔴 | À un instant T : `lmutil lmstat -a -c <serveur>` manuellement, puis `collect` ; comparer avec l'onglet Sessions (filtre serveur, période du jour, sessions « en cours ») | Même liste d'utilisateurs / postes / features ; même nombre de jetons en cours | ☐ |
| VSR-02 🔴 | Comparer « Jetons émis » (Synthèse) avec le fichier de licences / l'outil éditeur | Identique pour chaque feature | ☐ |
| VSR-03 | Vérifier le mapping produit | Chaque feature suivie a un produit lisible (sinon compléter `products:`) ; `SELECT name, vendor FROM feature WHERE product IS NULL` vide pour les features utiles | ☐ |

### 6.2 Contrôle sur une journée

| Id | Étapes | Critère d'acceptation | Statut |
|---|---|---|:---:|
| VSR-04 🔴 | Serveur avec log : compter les refus de la veille dans le log (`findstr /C:"DENIED:" license.log` sur la journée) | Égal au nombre de refus de l'onglet Refus pour ce jour | ☐ |
| VSR-05 🔴 | Serveur avec log : compter les `OUT:` de la veille | Égal au nombre de sessions du jour (source log) | ☐ |
| VSR-06 | Pic de la journée vs relevés lmstat manuels à 3 moments de forte charge | Pic HeptagonesLM ≥ chacun des relevés et ≤ jetons émis | ☐ |
| VSR-07 | Si OpenLM (ou un autre outil) est encore en service : comparer pic journalier et heures par produit sur 1 semaine | Écart de pic ≤ 1 jeton ; écart d'heures ≤ 5 % | ☐ |

### 6.3 Test contrôlé avec un utilisateur volontaire

| Id | Étapes | Critère d'acceptation | Statut |
|---|---|---|:---:|
| VSR-08 🔴 | L'utilisateur ouvre le logiciel à une heure notée (ex. 10:00), le ferme à 10:45 | Session visible : début à la minute près ; fin ± intervalle de sondage (lmstat) ou à la seconde (log) | ☐ |
| VSR-09 | Deux postes du même utilisateur simultanément | Deux sessions distinctes | ☐ |
| VSR-10 | Laisser le logiciel inactif jusqu'au délai d'inactivité (si configuré) | Session fermée, motif « inactive » (log) | ☐ |
| VSR-11 | Prendre le dernier jeton disponible d'une feature (ou simuler en heure creuse), puis tenter un 2ᵉ lancement | Heure saturée comptée ; refus enregistré (si log) | ☐ |

## 7. Performance

| Id | Mesure | Seuil d'acceptation | Mesuré | Statut |
|---|---|---|---|:---:|
| PRF-01 🔴 [E3] | Durée de `collect` (tous serveurs) | < 60 s, et nettement < intervalle de planification | | ☐ |
| PRF-02 [E3] | Première lecture d'un gros log (> 100 Mo) | Se termine ; exécutions suivantes < 10 s | | ☐ |
| PRF-03 [E3] | Interface, onglet Synthèse sur 1 an | < 5 s | | ☐ |
| PRF-04 [E3] | Onglet Sessions sur 1 mois | < 5 s ; défilement fluide | | ☐ |
| PRF-05 [E3] | Rapport complet sur 1 an | < 2 min | | ☐ |
| PRF-06 [E3] | Volume de base après 1 mois | Conforme à l'estimation (≈ 60 000 sondages / jour pour 200 features à 5 min) ; extrapolation sur 3 ans < capacité de l'instance | | ☐ |

Aide : `heptagoneslm demo --days 365 --connection-string "<base de test>"` produit un an de
données simulées pour mesurer PRF-03 à PRF-05 avant d'avoir un an d'historique réel.

## 8. Sécurité

| Id | Étapes | Résultat attendu | Statut |
|---|---|---|:---:|
| SEC-01 🔴 | `netstat -ano | findstr 8765` avec `heptagoneslm web` par défaut (`auth.mode: none`) | Écoute sur `127.0.0.1` uniquement | ☐ |
| SEC-02 | Depuis un autre poste : `http://<serveur>:8765/` (configuration par défaut) | Injoignable ; accès réseau uniquement avec authentification + HTTPS (§ 5.9) | ☐ |
| SEC-03 🔴 | Lecture seule | Aucune action de l'interface ne modifie la base (vérifier `SELECT COUNT(*)` des tables avant / après une session d'utilisation) | ☐ |
| SEC-04 | Injection | Paramètres forgés dans l'URL : `server=' OR 1=1 --`, `type=../../etc`, `start=2026-01-01;DROP` | Réponse 400 ou résultat vide ; aucune erreur SQL | ☐ |
| SEC-05 | Traversée de répertoire | `http://localhost:8765/../heptagoneslm.yaml`, `/static/../../heptagoneslm.yaml` | 404 | ☐ |
| SEC-06 | Secrets | Parcourir `heptagoneslm.log` et l'onglet Collectes | Aucun mot de passe affiché | ☐ |
| SEC-07 [E3] | Moindre privilège | Retirer `db_ddladmin` après `init-db` | Collecte et interface fonctionnent | ☐ |
| SEC-09 🔴 | DNS rebinding | `curl -H "Host: attaquant.example.com" http://localhost:8765/api/meta` | **421** ; avec le vrai nom du serveur : 200 | ☐ |
| SEC-10 | Connexions lentes | Ouvrir une connexion TCP sur le port sans rien envoyer | Fermée par le serveur après `request_timeout_s` (30 s) | ☐ |
| SEC-11 🔴 | Injection de formule | Utilisateur de test nommé `=1+1` (ou poste) ; exporter Sessions en CSV et Excel | Affiché en texte `=1+1`, jamais calculé | ☐ |
| SEC-12 [E3] | Compte des tâches | `Get-ScheduledTask -TaskPath \HeptagonesLM\ | Select TaskName, @{n="Compte";e={$_.Principal.UserId}}` | Compte de service dédié, jamais SYSTEM ; `register_tasks.ps1` sans `-ServiceAccount` refusé | ☐ |
| SEC-13 [E3] | Chiffrement SQL | Chaîne de connexion de production | `Encrypt=yes`, pas de `TrustServerCertificate=yes` ; aucun avertissement « SÉCURITÉ » au démarrage | ☐ |
| SEC-14 | Intégrité de l'exécutable | `Get-FileHash HeptagonesLM.exe -Algorithm SHA256` | Identique à `SHA256SUMS` de la release | ☐ |
| SEC-08 | Données personnelles | Identifier les destinataires des rapports (noms, services, horaires) ; informer le DPO | Traitement déclaré / validé (RGPD) | ☐ |

## 9. Robustesse

| Id | Situation provoquée | Résultat attendu | Statut |
|---|---|---|:---:|
| ROB-01 🔴 [E3] | SQL Server arrêté pendant une collecte | Échec journalisé dans `heptagoneslm.log`, code retour 1 ; collecte suivante normale une fois SQL Server revenu | ☐ |
| ROB-02 [E3] | Serveur de licences arrêté 1 h | Collectes `ERROR` pendant l'arrêt ; sessions non fermées à tort ; reprise automatique | ☐ |
| ROB-03 [E3] | Collecte interrompue (tâche désactivée) 1 journée | À la reprise, sessions disparues fermées avec une fin estimée ≤ 30 min après leur dernière observation | ☐ |
| ROB-04 | Changement d'heure (fin octobre / fin mars) | Pas de doublon ni de session négative autour de 2 h–3 h | ☐ |
| ROB-05 | Caractères accentués / cp1252 dans les noms (logs, AD) | Affichage correct dans l'interface et les exports | ☐ |
| ROB-06 [E3] | Disque plein sur le serveur HeptagonesLM | Message d'erreur, pas de corruption de la base | ☐ |
| ROB-07 [E3] | Deux collectes simultanées (lancement manuel pendant la tâche planifiée) | La 2ᵉ s'arrête aussitôt avec « Une collecte est déjà en cours : exécution ignorée » (verrou SQL Server) ; aucune session en double | ☐ |

## 10. Recette métier

Scénarios à dérouler **par les utilisateurs des rapports** (responsable CAO / simulation,
achats, DSI) sur les données réelles (E3), après au moins 2 semaines de collecte.

| Id | Question métier | Où trouver la réponse | Réponse obtenue et jugée fiable ? | Statut |
|---|---|---|---|:---:|
| MET-01 | Combien de licences de <produit> sont réellement nécessaires ? | Efficacité (jetons recommandés) + Synthèse (pic, heures saturées) + Usage dans le temps | | ☐ |
| MET-02 | Quelles licences sont sous-utilisées (pistes d'économie) ? | Optimisation & coûts (économies potentielles), alerte « sous-utilisé » du tableau de bord | | ☐ |
| MET-03 | Qui utilise <produit>, et combien de temps ? | Répartition par utilisateur, filtre produit | | ☐ |
| MET-04 | Quels services consomment le plus (refacturation) ? | Répartition / Croisement par service | | ☐ |
| MET-05 | Quand y a-t-il pénurie ? | Contention, Refus, Profil horaire | | ☐ |
| MET-06 | Le rapport mensuel est-il exploitable tel quel (comité, direction) ? | Rapport complet Excel | | ☐ |
| MET-07 | Les données peuvent-elles être retravaillées dans Excel ? | Export CSV / Sessions + tableau croisé dynamique | | ☐ |
| MET-08 | En un coup d'œil, la situation est-elle saine ? | Tableau de bord (alertes, KPI, serveurs) | | ☐ |
| MET-09 | Qui bloque <produit> en ce moment ? | En direct | | ☐ |

## 11. Planning et procès-verbal

### 11.1 Planning indicatif

| Étape | Contenu | Durée |
|---|---|---|
| J1 | Vérifications automatisées (§ 4), recette sur la démo (§ 5, E2) | ½ jour |
| J2 | Installation E3 (INS, CFG, COL-01/09, LOG-01), planification | 1 jour |
| J3 → J4 | Collecte en continu ; validation réelle § 6.1 et 6.2 | 2 jours (dont 1 jour de collecte) |
| J5 | Tests contrôlés § 6.3, sécurité, robustesse | 1 jour |
| J6 | Connexion AD / HTTPS (§ 5.9), e-mails (§ 5.10) | 1 jour |
| J5 → J15 | Collecte continue (≥ 2 semaines) | — |
| J15 | Performance sur l'historique, recette métier § 10 | 1 jour |
| J16 | Bilan, procès-verbal, décision de mise en production | ½ jour |

### 11.2 Suivi des anomalies

Chaque anomalie est ouverte en [issue GitHub](https://github.com/Valou94460/FREE-LM/issues/new/choose)
(modèle « Signaler un bug ») avec l'identifiant du cas (ex. `[COL-03]`), la gravité et
les journaux (`heptagoneslm.log`, lancé avec `-v`). Pour un problème de parsing, joindre la
sortie `lmstat` ou l'extrait de log **anonymisé** : il deviendra un test automatisé.

### 11.3 Procès-verbal de recette

| Élément | Valeur |
|---|---|
| Version testée | |
| Période de la campagne | |
| Cas exécutés / conformes / en anomalie / non applicables | / / / |
| Cas bloquants 🔴 conformes | / |
| Anomalies ouvertes (bloquantes / majeures / mineures) | / / |
| Réserves | |
| **Décision** | ☐ Mise en production autorisée ☐ Autorisée avec réserves ☐ Refusée |

| Rôle | Nom | Date | Signature |
|---|---|---|---|
| Administrateur applicatif (HeptagonesLM) | | | |
| Administrateur des licences | | | |
| Responsable métier | | | |
| Responsable sécurité / DPO | | | |
