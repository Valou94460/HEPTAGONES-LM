"""Assistant d'installation de HeptagonesLM sur un serveur Windows.

``heptagoneslm setup`` (ou ``HeptagonesLM-Setup.exe``) ouvre un assistant pas à pas dans le
navigateur : contrôle des prérequis, configuration (base de données, serveurs de
licences, IIS, authentification, e-mail), installation avec barre de progression,
puis vérification complète. ``heptagoneslm setup --answers fichier.json`` réalise la
même installation sans interface (industrialisation).

Toutes les actions système passent par :mod:`heptagoneslm.setup.system` : une
implémentation Windows (PowerShell, IIS, Planificateur de tâches) et une
implémentation simulée (démonstration, tests) qui ne modifie rien.
"""
