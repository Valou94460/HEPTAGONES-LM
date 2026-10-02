"""Secrets chiffrés (mots de passe et jetons des outils ITSM) hors du fichier de configuration.

L'assistant d'installation chiffre chaque secret avec Windows (DPAPI, portée machine) dans
``<installation>\\secrets\\<nom>.dpapi`` ; le dossier n'est lisible que par SYSTEM, les
administrateurs et le compte de service. La configuration y fait référence :

    password: dpapi:C:/HeptagonesLM/secrets/itsm_password.dpapi

Le secret n'est déchiffré qu'au moment de l'appel (ticket, test), jamais écrit en clair
sur le disque ni affiché dans les journaux. Le fichier chiffré ne peut être déchiffré que
sur le serveur qui l'a produit : copié ailleurs, il est inutilisable.
"""

from __future__ import annotations

import os
from pathlib import Path

PREFIX = "dpapi:"
ENTROPY = b"HeptagonesLM secret v1"          # sel applicatif : un autre logiciel ne déchiffre pas « par hasard »

_CRYPTPROTECT_UI_FORBIDDEN = 0x01
_CRYPTPROTECT_LOCAL_MACHINE = 0x04


class SecretError(RuntimeError):
    """Secret illisible : fichier absent, droits insuffisants ou autre serveur."""


def is_reference(value: str | None) -> bool:
    return isinstance(value, str) and value.lower().startswith(PREFIX)


def reference(path: str) -> str:
    """Valeur à écrire dans la configuration pour un fichier chiffré."""
    return PREFIX + str(path).replace("\\", "/")


def _dpapi(data: bytes, encrypt: bool) -> bytes:
    if os.name != "nt":
        raise SecretError("Secrets chiffrés (dpapi:) disponibles uniquement sous Windows")
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(raw: bytes) -> tuple[Blob, ctypes.Array]:
        buf = ctypes.create_string_buffer(raw, len(raw))
        return Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    src, _keep = blob(data)
    ent, _keep2 = blob(ENTROPY)
    out = Blob()
    flags = _CRYPTPROTECT_UI_FORBIDDEN | (_CRYPTPROTECT_LOCAL_MACHINE if encrypt else 0)
    if encrypt:
        ok = crypt32.CryptProtectData(ctypes.byref(src), "HeptagonesLM", ctypes.byref(ent), None, None, flags,
                                      ctypes.byref(out))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(src), None, ctypes.byref(ent), None, None, flags,
                                        ctypes.byref(out))
    if not ok:
        raise SecretError(f"DPAPI : erreur Windows {ctypes.GetLastError()}")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def protect(value: str) -> bytes:
    """Chiffre un secret pour ce serveur (DPAPI, portée machine)."""
    return _dpapi(value.encode("utf-8"), encrypt=True)


def unprotect(data: bytes) -> str:
    return _dpapi(data, encrypt=False).decode("utf-8")


def resolve(value: str | None) -> str | None:
    """Valeur en clair d'un paramètre : déchiffre ``dpapi:<fichier>``, renvoie les autres tels quels."""
    if not is_reference(value):
        return value
    path = Path(value[len(PREFIX):])
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise SecretError(f"Secret introuvable : {path} (relancer HeptagonesLM-Setup.exe pour le saisir)") from None
    except PermissionError:
        raise SecretError(f"Secret {path.name} : accès refusé (réservé au compte de service et aux "
                          "administrateurs)") from None
    try:
        return unprotect(data)
    except SecretError as exc:
        raise SecretError(f"Secret {path.name} indéchiffrable sur ce serveur ({exc}) : le ressaisir avec "
                          "HeptagonesLM-Setup.exe") from None
