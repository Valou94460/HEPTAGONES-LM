"""Assistant d'installation de FreeLM sur un serveur Windows.

``freelm setup`` (ou ``FreeLM-Setup.exe``) ouvre un assistant pas à pas dans le
navigateur : contrôle des prérequis, configuration (base de données, serveurs de
licences, IIS, authentification, e-mail), installation avec barre de progression,
puis vérification complète. ``freelm setup --answers fichier.json`` réalise la
même installation sans interface (industrialisation).

Toutes les actions système passent par :mod:`freelm.setup.system` : une
implémentation Windows (PowerShell, IIS, Planificateur de tâches) et une
implémentation simulée (démonstration, tests) qui ne modifie rien.
"""
