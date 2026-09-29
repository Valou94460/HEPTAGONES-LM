"""HeptagonesLM - suivi et reporting de consommation de licences FlexLM/FlexNet.

Architecture en couches :

* ``heptagoneslm.collectors``  : récupération et parsing des données brutes
  (lmstat, logs debug), indépendants de la base.
* ``heptagoneslm.service``     : orchestration d'une collecte et application des
  résultats en base (snapshots, reconstruction des sessions, évènements).
* ``heptagoneslm.storage``     : schéma et accès SQL (SQL Server en production,
  SQLite pour les tests).
* ``heptagoneslm.reporting``   : calcul des indicateurs et export Excel.
"""

__version__ = "26.1.1"
