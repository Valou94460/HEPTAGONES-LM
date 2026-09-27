# Plan d'implémentation

Chaque étape produit quelque chose d'utilisable et se valide sur données
réelles avant de passer à la suivante.

## Étape 0 — Socle (fait dans cette version)

- [x] Structure du projet, configuration YAML, CLI
- [x] Parser `lmstat -a` (totaux, checkouts, files d'attente, utilisateurs avec espaces, changement d'année)
- [x] Parser du log debug FlexLM (OUT/IN/DENIED/QUEUED/UNSUPPORTED, dates, minuit, rotation)
- [x] Schéma SQL Server / SQLite, vues de reporting
- [x] Reconstruction des sessions (lmstat et log), agrégats horaires
- [x] Rapport Excel (8 feuilles)
- [x] Scripts de tâches planifiées, tests unitaires
- [x] Simulateur FlexLM + `heptagoneslm demo` + faux `lmutil` ; CI Linux / Windows / SQL Server 2022
- [x] Interface web (`heptagoneslm web`) : 9 rapports, filtres, exports Excel/CSV/JSON ; `HeptagonesLM.exe` autonome
- [x] Import du référentiel utilisateurs (services, noms) depuis un CSV AD/RH

## Étape 1 — Validation des parsers sur vos serveurs (½ à 1 jour)

1. Sur le futur serveur HeptagonesLM, capturer une sortie réelle par serveur :
   `lmutil lmstat -a -c 27000@licsrv1 > lmstat_autodesk.txt`
2. `heptagoneslm parse-lmstat lmstat_autodesk.txt` : vérifier features, totaux,
   utilisateurs, heures de début.
3. Idem pour les logs : `heptagoneslm parse-log \\licsrv2\...\license.log`.
4. Ajouter chaque sortie un peu particulière dans `tests/fixtures/` avec un
   test (anonymiser les noms) : c'est la meilleure protection contre les
   régressions quand les éditeurs changent leur version de FlexNet.

Points d'attention connus selon les éditeurs :
- Ansys : plusieurs démons (`ansyslmd`), features très nombreuses (`a_*`) → mapping produit par motifs.
- MATLAB : toolboxes = une feature chacune ; `lmstat` affiche aussi les réservations (ignorées).
- Autodesk : features codées (`87545ACD_2024_0F`) → table de correspondance à compléter.

## Étape 2 — Mise en service de la collecte (1 jour)

1. Base : `sql/sqlserver/00_create_database.sql` (DBA), puis `heptagoneslm init-db`.
2. Installer Python + venv + ODBC Driver 18 sur le serveur HeptagonesLM (voir README).
3. `heptagoneslm test-server --server AUTODESK` pour chaque serveur.
4. `scripts/register_tasks.ps1` → collecte toutes les 5 minutes.
5. Surveiller la table `collection_run` pendant quelques jours
   (`SELECT * FROM collection_run WHERE status = 'ERROR'`).

## Étape 3 — Référentiel et premiers rapports (1 à 2 jours)

1. Compléter `products:` dans le YAML (liste des features :
   `SELECT DISTINCT name, vendor FROM feature WHERE product IS NULL`),
   puis `heptagoneslm apply-products`.
2. Importer l'historique des logs disponibles : `heptagoneslm import-log --server ANSYS archive1.log archive2.log`.
3. Premier rapport : `heptagoneslm report --period last-7d`, relecture avec les
   utilisateurs clés, ajustement des indicateurs.

## Étape 4 — Enrichissements (à la demande)

- [x] Connexion Windows/AD (LDAPS, groupes) ou SSO via IIS ; HTTPS ; journal d'audit.
- [x] Rapports mensuels par e-mail (abonnements).
- [ ] Droits par service (un responsable ne voit que son service).
- [ ] Gestion des abonnements e-mail depuis l'interface.
- [ ] Onglet « Optimisation » : jetons jamais utilisés au-delà de N
      (pic < émis sur 3 mois) → pistes de réduction du parc.
- [ ] Features regroupées par produit avec calcul du pic simultané
      *réel* (et non la somme des pics) à partir des sondages bruts.
- [ ] Liste d'exclusion d'utilisateurs / postes (comptes de service, tests).
- [ ] Migrations de schéma versionnées (`schema_info.version`).

## Étape 5 — Autres gestionnaires (si besoin)

- [ ] RLM (`rlmutil rlmstat -a`, log RLM « report log ») : voir
      [ARCHITECTURE.md §6](ARCHITECTURE.md#6-ajouter-un-autre-gestionnaire-de-licences-ex-rlm).
- [ ] LM-X, Sentinel RMS, DSLS : même principe, un collecteur par outil.

## Dimensionnement indicatif

Avec 200 features sondées toutes les 5 minutes :
`usage_snapshot` ≈ 58 000 lignes/jour (≈ 5 M sur 90 jours de rétention),
`usage_hourly` ≈ 4 800 lignes/jour (≈ 1,7 M/an, conservées). Largement
dans les capacités d'un SQL Server Express (limite 10 Go) pendant des années.
