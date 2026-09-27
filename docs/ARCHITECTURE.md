# Architecture de FreeLM

## 1. Vue d'ensemble

FreeLM est un outil **en ligne de commande**, sans interface graphique ni agent
sur les postes clients. Il tourne sur un serveur Windows (ou n'importe quelle
machine avec Python 3.10+) via le Planificateur de tâches :

```mermaid
flowchart LR
    subgraph Serveurs de licences
        A1[lmgrd + adskflex<br/>27000@licsrv1]
        A2[lmgrd + ansyslmd<br/>1055@licsrv2]
        L2[(license.log<br/>partage SMB)]
    end

    subgraph "Serveur FreeLM (tâches planifiées)"
        C1[collect<br/>toutes les 5 min]
        R1[report<br/>mensuel]
        P1[purge<br/>hebdo]
    end

    DB[(SQL Server<br/>base FreeLM)]
    X[/Rapport .xlsx/]

    A1 -- "lmutil lmstat -a" --> C1
    A2 -- "lmutil lmstat -a" --> C1
    L2 -- "lecture incrémentale" --> C1
    C1 --> DB
    DB --> R1 --> X
    P1 --> DB
```

## 2. Modules

```
freelm/
├── cli.py                  Point d'entrée (init-db, collect, report, import-log, purge…)
├── config.py               Chargement/validation du YAML, mapping feature → produit
├── models.py               Modèle normalisé : ServerStatus, FeatureStatus, Checkout, LicenseEvent
├── service.py              Orchestration : exécute les collecteurs, reconstruit les sessions, écrit en base
├── collectors/
│   ├── base.py             Interfaces StatusCollector / LogCollector + registre
│   └── flexlm/
│       ├── lmstat.py       Exécution de lmutil lmstat + parser de la sortie
│       └── logfile.py      Parser du log debug + lecture incrémentale (offset, rotation)
├── storage/
│   ├── db.py               Connexion DB-API (pyodbc / sqlite3) et différences de dialecte
│   ├── schema.py           Schéma unique → DDL SQL Server et SQLite, vues de reporting
│   └── repository.py       Toutes les requêtes d'écriture
└── reporting/
    ├── dataset.py          Calcul des indicateurs (pur Python, testable)
    └── excel.py            Mise en forme du classeur (openpyxl)
```

Principes :

* **Séparation parsing / persistance** : les collecteurs ne connaissent pas la
  base ; ils renvoient des objets de `models.py`. On peut tester un parser sur
  une sortie texte sans aucune base (`freelm parse-lmstat fichier.txt`).
* **Registre de collecteurs** : `@register("flexlm", "lmstat")`. Ajouter RLM
  revient à écrire `collectors/rlm/rlmstat.py` qui produit les mêmes objets
  (voir §6) ; le service, la base et les rapports ne changent pas.
* **SQL portable** : requêtes paramétrées `?` compatibles pyodbc et sqlite3.
  SQL Server en production, SQLite pour les tests et les essais.
  Pas d'ORM : moins de dépendances, SQL lisible par un DBA.
* **Une transaction par serveur et par collecteur** : un serveur injoignable
  n'empêche pas la collecte des autres ; l'échec est tracé dans `collection_run`.

## 3. Flux de collecte

```mermaid
sequenceDiagram
    participant T as Tâche planifiée
    participant S as CollectionService
    participant C as Collecteur
    participant DB as SQL Server

    T->>S: freelm collect
    S->>DB: sync license_server (depuis le YAML)
    loop pour chaque serveur, chaque collecteur
        alt collecteur d'état (lmstat)
            S->>C: poll()
            C-->>S: ServerStatus (features, jetons, checkouts)
            S->>DB: usage_snapshot + maj usage_hourly
            opt sessions_from = lmstat
                S->>DB: diff checkouts ↔ sessions ouvertes (ouvrir / fermer)
            end
        else collecteur de log
            S->>DB: lire collector_state (offset, date courante)
            S->>C: read_new_events(state)
            C-->>S: LicenseEvent[] + nouvel état
            S->>DB: license_event (DENIED, QUEUED…)
            opt sessions_from = log
                S->>DB: OUT → ouvre, IN → ferme, arrêt démon → ferme tout
            end
            S->>DB: enregistrer le nouvel état
        end
        S->>DB: collection_run (OK / ERROR) puis COMMIT
    end
```

### Deux sources, un choix par serveur

| | `lmstat` (polling) | `log` (debug log) |
|---|---|---|
| Accès requis | réseau vers le port lmgrd + `lmutil` | lecture du fichier log (partage SMB) |
| Jetons émis / en cours | ✅ | ❌ |
| Sessions | ✅ précision = intervalle de sondage | ✅ à la seconde |
| Refus (DENIED) / files d'attente | ❌ (seulement la file courante) | ✅ |
| Reprise d'historique | ❌ | ✅ (`freelm import-log`) |

Recommandation : `lmstat` partout ; ajouter `log` là où le fichier est
accessible, en prenant alors `sessions_from: log`. Les deux collecteurs
alimentent la même base, mais **une seule source de sessions par serveur**
pour éviter les doublons.

### Reconstruction des sessions depuis lmstat

À chaque sondage, chaque checkout est identifié par
`(feature, utilisateur, poste, handle)` (le *handle* FlexLM est unique par
checkout) :

* présent maintenant, absent en base → nouvelle session (début = heure de
  début donnée par lmstat) ;
* présent en base, absent maintenant → session fermée ; fin estimée au milieu
  de l'intervalle entre les deux sondages (plafonné à 30 min si la collecte a
  été interrompue) ;
* démon vendeur en panne → les sessions de ce démon restent ouvertes (on ne
  sait pas si les jetons ont été rendus) ;
* serveur injoignable → aucune modification.

### Log debug : date et incrémental

Les lignes du log FlexLM ne portent que l'heure. La date vient des lignes
`TIMESTAMP M/D/YYYY` et `started on … (M/D/YYYY)` ; un recul de l'heure de plus
de 2 h est interprété comme un passage de minuit. L'état persistant
(`collector_state`) contient l'offset lu, la signature des 256 premiers octets
(détection de rotation / recréation du fichier) et la date courante. Une ligne
incomplète (en cours d'écriture) n'est jamais consommée.

## 4. Schéma de base de données

Script complet : [`sql/sqlserver/01_schema.sql`](../sql/sqlserver/01_schema.sql)
(généré par `freelm ddl --dialect mssql`).

```mermaid
erDiagram
    license_server ||--o{ feature : "héberge"
    license_server ||--o{ collection_run : "journal"
    license_server ||--o{ collector_state : "état"
    license_server ||--o{ license_event : ""
    feature ||--o{ usage_snapshot : "sondages bruts"
    feature ||--o{ usage_hourly : "agrégats horaires"
    feature ||--o{ license_session : ""
    feature ||--o{ license_event : ""
    app_user ||--o{ license_session : ""
    workstation ||--o{ license_session : ""
    app_user ||--o{ license_event : ""
    workstation ||--o{ license_event : ""

    license_server {
        int id PK
        nvarchar name UK
        nvarchar license_type "flexlm, rlm…"
        nvarchar port_at_host
        bit enabled
    }
    feature {
        int id PK
        int server_id FK
        nvarchar name "UK avec server_id"
        nvarchar vendor "démon (adskflex, MLM…)"
        nvarchar product "libellé issu du mapping"
        nvarchar publisher
        datetime2 first_seen
        datetime2 last_seen
    }
    app_user {
        int id PK
        nvarchar username UK "minuscules"
        nvarchar display_name "à compléter (AD)"
        nvarchar department "à compléter"
    }
    workstation {
        int id PK
        nvarchar hostname UK
        nvarchar site
    }
    license_session {
        bigint id PK
        int feature_id FK
        int user_id FK
        int workstation_id FK
        nvarchar handle
        int license_count
        datetime2 checkout_time
        datetime2 checkin_time "NULL = en cours"
        datetime2 last_seen
        nvarchar source "lmstat | log"
        nvarchar close_reason "checkin, not_seen, inactive, server_down"
    }
    license_event {
        bigint id PK
        int server_id FK
        int feature_id FK
        datetime2 event_time
        nvarchar event_type "DENIED, QUEUED, UNSUPPORTED, SERVER_DOWN"
        nvarchar detail
    }
    usage_snapshot {
        bigint id PK
        int feature_id FK
        datetime2 snapshot_time
        int issued
        int in_use
        int queued
    }
    usage_hourly {
        int feature_id PK
        datetime2 hour_start PK
        int samples
        bigint sum_in_use
        int max_in_use
        int min_in_use
        int issued
        int max_queued
    }
    collection_run {
        bigint id PK
        int server_id FK
        nvarchar collector
        datetime2 started_at
        nvarchar status "OK | ERROR"
        nvarchar message
    }
    collector_state {
        int server_id PK
        nvarchar collector PK
        nvarchar state_json
    }
```

Choix de conception :

* **`usage_hourly`** est mis à jour à chaque sondage (samples, somme, min, max).
  Les rapports ne lisent que cette table pour les taux d'utilisation : un an
  de rapport = quelques centaines de milliers de lignes au lieu de dizaines de
  millions de sondages. Les sondages bruts (`usage_snapshot`) sont purgés après
  `retention.snapshots_days` jours.
* **OUT/IN ne sont pas stockés comme évènements** : ils sont matérialisés en
  `license_session`. `license_event` ne garde que ce qui n'est pas une session
  (refus, files d'attente, arrêts).
* **Dimensions `app_user` / `workstation`** : les colonnes `display_name`,
  `department`, `site` sont vides à la collecte ; elles peuvent être alimentées
  par un script (export AD, CSV RH) et apparaissent alors dans les rapports.
* **Dates en heure locale** (`DATETIME2(0)`), celle du serveur FreeLM et des
  logs. Tous les serveurs de licences sont supposés dans le même fuseau.
* **Vues** `v_session_detail`, `v_usage_hourly`, `v_denial` : données
  dénormalisées pour des requêtes ad hoc, Power Query ou un tableau croisé
  connecté directement à SQL Server depuis Excel.

## 5. Rapport Excel

`freelm report` produit un classeur avec les feuilles :

| Feuille | Contenu |
|---|---|
| Synthèse | Par produit : jetons émis, pic, moyenne, taux moyen / au pic / heures ouvrées, heures saturées, refus, utilisateurs, heures ; graphique des taux |
| Usage quotidien | Croisement date × produit (pic de jetons) + courbes du top N |
| Utilisateurs | Croisement utilisateur × produit (heures) + graphique top N |
| Profil horaire | Carte de chaleur heure × jour de semaine (taux moyen) |
| Contention | Jours avec saturation et/ou refus, par produit |
| Refus | Détail des DENIED |
| Sessions | Données brutes en Tableau Excel (base de vos propres TCD) |
| À propos | Période, filtres, définitions |

openpyxl ne crée pas de vrais tableaux croisés dynamiques : les croisements
usuels sont précalculés, et les données brutes sont livrées sous forme de
Tableaux Excel (Insertion › Tableau croisé dynamique en un clic).

## 6. Ajouter un autre gestionnaire de licences (ex. RLM)

1. Créer `freelm/collectors/rlm/rlmstat.py` :

   ```python
   from freelm.collectors.base import StatusCollector, register
   from freelm.models import ServerStatus

   @register("rlm", "rlmstat")
   class RlmstatCollector(StatusCollector):
       def poll(self) -> ServerStatus:
           output = ...  # rlmutil rlmstat -a -c port@host
           return parse_rlmstat(output)  # produit FeatureStatus / Checkout
   ```

2. L'importer dans `freelm/collectors/__init__.py`.
3. Déclarer le serveur :

   ```yaml
   - name: RLM_SRV
     license_type: rlm
     port_at_host: "5053@rlmsrv"
     collectors: [rlmstat]
     sessions_from: rlmstat
   ```

Pour un log, hériter de `LogCollector` et implémenter
`read_new_events(state) -> (events, new_state)`.
