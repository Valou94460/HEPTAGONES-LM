"""Protection des exports tableur contre l'injection de formules (CSV / Excel).

Les noms d'utilisateur, de poste et les motifs de refus proviennent des postes
clients et des logs : une valeur comme ``=HYPERLINK("http://…")`` ou
``=cmd|' /c calc'!A1`` serait exécutée à l'ouverture du fichier dans Excel.

* CSV : les textes commençant par ``= + - @``, une tabulation ou un retour chariot
  sont préfixés d'une apostrophe (recommandation OWASP) ;
* Excel (openpyxl) : une chaîne commençant par ``=`` deviendrait une formule ; on
  force le type texte de la cellule, la valeur reste affichée telle quelle.
"""

from __future__ import annotations

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_text(value: str) -> str:
    return "'" + value if value.startswith(FORMULA_PREFIXES) else value


def excel_text(cell) -> None:
    """À appeler après l'affectation d'une valeur à une cellule openpyxl."""
    if isinstance(cell.value, str) and cell.value.startswith("="):
        cell.data_type = "s"


def excel_row(ws) -> None:
    """Neutralise la dernière ligne ajoutée par ``ws.append``."""
    for cell in ws[ws.max_row]:
        excel_text(cell)
