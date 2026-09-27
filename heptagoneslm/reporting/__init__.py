"""Reporting : calcul des indicateurs (``dataset``) et export Excel (``excel``).

``excel`` n'est importé qu'à la demande pour ne pas imposer openpyxl aux
commandes de collecte.
"""

from heptagoneslm.reporting.dataset import ReportData, build_report_data, previous_month  # noqa: F401
