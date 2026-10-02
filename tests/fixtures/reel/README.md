# Sorties réelles des serveurs de licences (tests de non-régression)

Chaque paire de fichiers est rejouée par `tests/test_sorties_reelles.py` :

- `<nom>.txt` : sortie brute de `lmutil lmstat -a -c port@serveur` (FlexLM) ou de
  `rlmutil rlmstat -a -c port@serveur` (RLM), telle qu'enregistrée par la recette de
  `HeptagonesLM-Setup.exe` ;
- `<nom>.attendu.json` : le résumé attendu, produit par la même recette au moment où la
  comparaison avec HeptagonesLM a été validée sur le site du client (jetons émis et utilisés
  par feature, lus directement dans la sortie brute ; sessions ; nombre d'utilisateurs).

## Ajouter la sortie d'un client

1. Sur le serveur du client, lancer la recette dans `HeptagonesLM-Setup.exe` (option
   « Anonymiser les sorties enregistrées » cochée par défaut : utilisateurs et postes
   remplacés par `utilisateur001`, `poste001`…).
2. Récupérer l'archive « Télécharger les preuves (.zip) » de l'assistant, ou le dossier
   `C:\HeptagonesLM\recette\<horodatage>\`.
3. Copier ici les fichiers `lmstat_<SERVEUR>.txt` et `lmstat_<SERVEUR>.attendu.json`
   (ou `rlmstat_…`), en les renommant `<client>_<serveur>.txt` / `.attendu.json`.
4. Lancer `python -m unittest tests.test_sorties_reelles` : l'analyseur doit retrouver
   exactement les mêmes jetons, sessions et utilisateurs.

Ne déposer que des sorties anonymisées ou dont le client a autorisé la diffusion.
Les deux fichiers `exemple_*` reprennent les jeux d'essai historiques du projet.
