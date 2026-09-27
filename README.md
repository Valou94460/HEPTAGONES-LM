# FreeLM

**Suivi et reporting open source de la consommation de licences FlexLM / FlexNet**
(Autodesk, Ansys, MATLAB, et tout éditeur utilisant FlexNet Publisher).

FreeLM collecte périodiquement l'état des serveurs de licences (`lmutil lmstat`)
et/ou lit leurs logs debug, historise la consommation dans SQL Server, et produit
des rapports Excel : qui utilise quoi, quand, taux d'utilisation par produit, pics
de contention et refus.

- Pas d'agent sur les postes clients, pas d'interface web : une CLI planifiable.
- Python 3.10+, dépendances minimales : `PyYAML`, `openpyxl`, `pyodbc`.
- Architecture modulaire : d'autres gestionnaires (RLM…) peuvent être ajoutés.

➡️ [Architecture, schéma de base et flux](docs/ARCHITECTURE.md) ·
[Plan d'implémentation](docs/PLAN.md)

## Installation (serveur Windows)

Prérequis : Python 3.10+ (64 bits), [ODBC Driver 18 for SQL Server](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server),
et `lmutil.exe` (fourni avec tout gestionnaire FlexLM, ex. Autodesk Network License Manager).

```powershell
cd C:\FreeLM
git clone <url-du-depot> src
py -3 -m venv venv
.\venv\Scripts\pip install .\src
copy src\config\freelm.example.yaml freelm.yaml   # puis éditer
```

Base de données :

```powershell
# 1. (DBA) création de la base et du compte de service
sqlcmd -S SQLSRV01 -i src\sql\sqlserver\00_create_database.sql
# 2. création des tables (ou exécuter sql\sqlserver\01_schema.sql)
.\venv\Scripts\freelm -c freelm.yaml init-db
```

Vérification puis planification :

```powershell
.\venv\Scripts\freelm -c freelm.yaml test-server --server AUTODESK
.\venv\Scripts\freelm -c freelm.yaml collect
powershell -ExecutionPolicy Bypass -File src\scripts\register_tasks.ps1 -FreeLMHome C:\FreeLM -ServiceAccount "DOMAINE\svc_freelm"
```

Le compte de service doit pouvoir joindre les ports lmgrd/démons vendeurs
(pare-feu), lire les logs éventuels (partage SMB) et écrire dans la base.

## Utilisation

| Commande | Rôle |
|---|---|
| `freelm init-db` | Crée / complète le schéma, référence les serveurs du YAML |
| `freelm collect [--server X]` | Une collecte (à planifier toutes les 5 min). Code retour 2 si un serveur est en échec |
| `freelm report [--period last-month\|this-month\|last-week\|last-7d\|last-year]` | Rapport Excel |
| `freelm report --from 2024-09-01 --to 2024-10-01 --server ANSYS -o rapport.xlsx` | Période et serveurs au choix (fin exclue) |
| `freelm import-log --server X fichier1.log fichier2.log` | Reprise d'historique depuis des logs archivés |
| `freelm apply-products` | Réapplique le mapping feature → produit à tout l'historique |
| `freelm purge` | Applique la politique de rétention |
| `freelm test-server --server X` | Interroge un serveur sans rien écrire |
| `freelm parse-lmstat fichier.txt` / `parse-log fichier.log` | Diagnostic des parsers (sans base) |
| `freelm ddl --dialect mssql` | Affiche le script SQL du schéma |

Options globales : `-c freelm.yaml` (ou variable `FREELM_CONFIG`), `-v` (logs détaillés).

## Configuration

Voir [`config/freelm.example.yaml`](config/freelm.example.yaml). L'essentiel :

```yaml
database:
  connection_string: "DRIVER={ODBC Driver 18 for SQL Server};SERVER=SQLSRV01;DATABASE=FreeLM;Trusted_Connection=yes;TrustServerCertificate=yes"
lmutil_path: "C:/FreeLM/bin/lmutil.exe"
servers:
  - name: AUTODESK
    port_at_host: "27000@licsrv1"
    collectors: [lmstat]           # et/ou log
    sessions_from: lmstat          # source des sessions : lmstat ou log
products:
  - { pattern: "87545ACD_2024_*", product: "AutoCAD 2024", publisher: Autodesk }
```

## Développement

```bash
pip install -e .[dev]
python -m pytest          # ou : python -m unittest discover -s tests -t .
```

Les tests utilisent SQLite en mémoire : aucun SQL Server ni serveur de licences
n'est nécessaire. Pour un essai local complet, utiliser
`connection_string: "sqlite:///freelm.db"` et `freelm import-log` sur
`tests/fixtures/flexlm_debug.log`.

## Licence

MIT — voir [LICENSE](LICENSE).
