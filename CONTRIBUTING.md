# Contribuer à HeptagonesLM

Merci de votre intérêt ! Les contributions sont bienvenues : signalements de bugs,
sorties `lmstat` ou logs d'éditeurs non encore gérés, nouveaux rapports, nouveaux
gestionnaires de licences (RLM…).

## Environnement

```bash
git clone https://github.com/Valou94460/HEPTAGONES-LM.git && cd HEPTAGONES-LM
python -m venv .venv && . .venv/bin/activate      # Windows : .venv\Scripts\activate
pip install -e ".[dev]"
python -m pytest
heptagoneslm web --demo --open
```

Aucun serveur de licences ni SQL Server n'est nécessaire : les tests utilisent
SQLite en mémoire et le simulateur intégré.

## Avant d'ouvrir une pull request

- [ ] `python -m pytest` passe.
- [ ] Toute modification du schéma (`heptagoneslm/storage/schema.py`) est accompagnée de la
      régénération du script : `heptagoneslm ddl --dialect mssql > sql/sqlserver/01_schema.sql`.
- [ ] Toute modification de l'interface web a été vérifiée dans le navigateur
      (`heptagoneslm web --demo --open`), en clair et en sombre.
- [ ] Un test couvre le comportement ajouté ou le bug corrigé.
- [ ] Le [CHANGELOG](CHANGELOG.md) est complété (section « Non publié »).

La CI vérifie tout cela automatiquement, y compris sous Windows, contre SQL Server
et dans un vrai navigateur.

## Signaler une sortie lmstat / un log non reconnu

C'est la contribution la plus utile : ajoutez l'extrait **anonymisé** (noms
d'utilisateurs et de postes remplacés) dans `tests/fixtures/` avec un test dans
`tests/test_lmstat_parser.py` ou `tests/test_log_parser.py`. Diagnostic rapide :

```bash
heptagoneslm parse-lmstat sortie.txt
heptagoneslm parse-log license.log
```

## Conventions

- Code et identifiants en anglais, documentation et messages en français.
- Pas de nouvelle dépendance sans nécessité : la collecte n'utilise que la
  bibliothèque standard, PyYAML et pyodbc ; l'interface web n'utilise ni framework
  ni CDN (elle doit fonctionner hors ligne sur un réseau d'entreprise).
- Requêtes SQL paramétrées (`?`) et portables SQL Server / SQLite.
- Ajouter un gestionnaire de licences : voir [docs/ARCHITECTURE.md §6](docs/ARCHITECTURE.md#6-ajouter-un-autre-gestionnaire-de-licences-ex-rlm).

## Publier une version

1. Mettre à jour `heptagoneslm/__init__.py`, `pyproject.toml` et le CHANGELOG.
2. `git tag v26.2.0 && git push origin v26.2.0` (numérotation `AA.N.C` : année, version de l'année, correctif)
   ou onglet *Actions* › *Release* › *Run workflow* (branche `main`, étiquette `v26.2.0`) :
   l'étiquette est alors créée au moment de la publication.
3. Le workflow *Release* vérifie que l'étiquette correspond à la version du code, construit
   `HeptagonesLM.exe` et `HeptagonesLM-Setup.exe`, puis publie la release GitHub
   (ou y joint les fichiers si elle a été créée depuis l'interface GitHub).
