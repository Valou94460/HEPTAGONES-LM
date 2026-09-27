"""FreeLM - suivi et reporting de consommation de licences FlexLM/FlexNet.

Architecture en couches :

* ``freelm.collectors``  : récupération et parsing des données brutes
  (lmstat, logs debug), indépendants de la base.
* ``freelm.service``     : orchestration d'une collecte et application des
  résultats en base (snapshots, reconstruction des sessions, évènements).
* ``freelm.storage``     : schéma et accès SQL (SQL Server en production,
  SQLite pour les tests).
* ``freelm.reporting``   : calcul des indicateurs et export Excel.
"""

__version__ = "0.1.0"
