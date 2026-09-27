"""Authentification de l'interface web par les comptes Windows / Active Directory.

Deux modes (``web.auth.mode``) :

``ldap``
    Page de connexion : l'utilisateur saisit son identifiant Windows et son mot
    de passe, vérifiés par une connexion (bind) LDAP à un contrôleur de domaine.
    Deux façons de trouver le compte :

    * ``upn_suffix`` (Active Directory) : bind direct en ``identifiant@suffixe``,
      puis lecture du compte ;
    * ``bind_dn`` / ``bind_password`` : un compte de service recherche le DN de
      l'utilisateur (``user_filter``), puis bind avec ce DN (tout annuaire LDAP).

    L'accès peut être réservé à des groupes (``allowed_groups``, CN ou DN),
    groupes imbriqués compris. Le mot de passe n'est jamais stocké ni journalisé.

``header``
    Authentification unique (SSO) : un reverse proxy — typiquement IIS avec
    l'authentification Windows — authentifie l'utilisateur et transmet son nom
    dans un en-tête (``X-Remote-User``). L'en-tête n'est accepté que depuis les
    adresses de ``trusted_proxies``. Voir docs/AUTHENTIFICATION.md.

Les sessions sont conservées en mémoire (cookie aléatoire ``HttpOnly``,
``SameSite=Strict``, ``Secure`` en HTTPS) : un redémarrage du service
déconnecte les utilisateurs.
"""

from __future__ import annotations

import logging
import re
import secrets
import ssl
import threading
import time
from dataclasses import dataclass, field

from heptagoneslm.config import AuthConfig

log = logging.getLogger(__name__)
audit = logging.getLogger("heptagoneslm.audit")

COOKIE_NAME = "heptagoneslm_session"
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f\u2028\u2029]")


def clean_for_log(value, limit: int = 200):
    """Neutralise les caractères de contrôle (retours à la ligne…) d'une valeur journalisée :
    un identifiant saisi par un attaquant ne doit pas pouvoir forger de fausses lignes d'audit."""
    if not isinstance(value, str):
        return value
    text = _CONTROL_CHARS.sub(lambda m: f"\\x{ord(m.group()):02x}", value)
    return text[:limit] + ("…" if len(text) > limit else "")


class _SanitizeFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(clean_for_log(a) for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: clean_for_log(v) for k, v in record.args.items()}
        record.msg = clean_for_log(record.msg, limit=2000) if isinstance(record.msg, str) else record.msg
        return True


audit.addFilter(_SanitizeFilter())
log.addFilter(_SanitizeFilter())


class AuthError(Exception):
    """Échec d'authentification.

    Le message est présenté à l'utilisateur ; ``reason`` (facultatif) détaille la
    cause pour le journal d'audit sans la révéler au client.
    """

    def __init__(self, message: str, reason: str | None = None):
        super().__init__(message)
        self.reason = reason or message


@dataclass
class UserInfo:
    username: str
    display_name: str | None = None
    department: str | None = None
    groups: list[str] = field(default_factory=list)


@dataclass
class Session:
    token: str
    user: UserInfo
    expires: float


class SessionStore:
    def __init__(self, hours: float):
        self.ttl = hours * 3600
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, user: UserInfo) -> Session:
        session = Session(secrets.token_urlsafe(32), user, time.time() + self.ttl)
        with self._lock:
            self._purge()
            self._sessions[session.token] = session
        return session

    def get(self, token: str | None) -> Session | None:
        if not token:
            return None
        with self._lock:
            session = self._sessions.get(token)
            if session and session.expires < time.time():
                del self._sessions[token]
                return None
            return session

    def delete(self, token: str | None) -> None:
        with self._lock:
            self._sessions.pop(token or "", None)

    def _purge(self) -> None:
        now = time.time()
        for token in [t for t, s in self._sessions.items() if s.expires < now]:
            del self._sessions[token]


class LoginThrottle:
    """Blocage temporaire après trop d'échecs (par identifiant et par adresse IP)."""

    def __init__(self, max_failures: int, lockout_minutes: int):
        self.max_failures = max_failures
        self.lockout = lockout_minutes * 60
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        return [t for t in self._failures.get(key, []) if now - t < self.lockout]

    def blocked(self, *keys: str) -> bool:
        now = time.time()
        with self._lock:
            return any(len(self._recent(k, now)) >= self.max_failures for k in keys)

    def failure(self, *keys: str) -> None:
        now = time.time()
        with self._lock:
            for k in keys:
                self._failures[k] = self._recent(k, now) + [now]

    def success(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                self._failures.pop(k, None)


def _cn(dn: str) -> str:
    first = dn.split(",", 1)[0]
    return first.split("=", 1)[1] if "=" in first else first


class LdapAuthenticator:
    """Vérifie un identifiant / mot de passe auprès de l'Active Directory (ldap3)."""

    GENERIC_ERROR = "Identifiant ou mot de passe incorrect"
    # Même message pour un compte hors des groupes autorisés : ne pas confirmer
    # qu'un mot de passe est valide (la cause exacte va au journal d'audit).
    DENIED_ERROR = "Identifiant ou mot de passe incorrect, ou accès non autorisé à HeptagonesLM"

    def __init__(self, cfg: AuthConfig, server=None, client_strategy=None):
        import ldap3  # dépendance chargée seulement si le mode ldap est utilisé

        self.ldap3 = ldap3
        self.cfg = cfg
        if not cfg.ldap_validate_cert:
            log.warning("SÉCURITÉ : ldap_validate_cert est désactivé — le certificat des contrôleurs de "
                        "domaine n'est pas vérifié (mots de passe exposés à une interception)")
        self.client_strategy = client_strategy or ldap3.SYNC
        if server is not None:          # injection (tests : serveur fictif ldap3)
            self.server = server
        else:
            tls = ldap3.Tls(
                validate=ssl.CERT_REQUIRED if cfg.ldap_validate_cert else ssl.CERT_NONE,
                ca_certs_file=cfg.ldap_ca_file,
            )
            servers = [
                ldap3.Server(url, use_ssl=url.lower().startswith("ldaps://"), tls=tls,
                             connect_timeout=5, get_info=ldap3.NONE)
                for url in cfg.ldap_servers
            ]
            self.server = ldap3.ServerPool(servers, ldap3.FIRST, active=True, exhaust=True) if len(servers) > 1 else servers[0]

    def _connection(self, user: str | None, password: str | None):
        conn = self.ldap3.Connection(
            self.server, user=user, password=password, authentication=self.ldap3.SIMPLE,
            client_strategy=self.client_strategy, raise_exceptions=False, receive_timeout=10,
            read_only=True,
        )
        if self.cfg.ldap_starttls:
            conn.open()
            # Sans chiffrement établi, le mot de passe partirait en clair au bind : refus.
            if not conn.start_tls() or not getattr(conn, "tls_started", False):
                log.error("STARTTLS refusé ou échoué vers l'annuaire (%s) : connexion annulée", conn.result)
                try:
                    conn.unbind()
                except Exception:
                    pass
                raise AuthError("Annuaire indisponible, réessayez plus tard", "STARTTLS non établi")
        return conn

    def _find_user(self, conn, username: str):
        from ldap3.utils.conv import escape_filter_chars

        search_filter = self.cfg.user_filter.replace("{username}", escape_filter_chars(username))
        conn.search(self.cfg.base_dn, search_filter, self.ldap3.SUBTREE,
                    attributes=["displayName", "department", "memberOf"], size_limit=2)
        if len(conn.entries) != 1:
            return None
        return conn.entries[0]

    def _groups(self, conn, entry) -> list[str]:
        """Groupes de l'utilisateur, imbrications comprises (profondeur 5)."""
        seen: dict[str, None] = {}
        frontier = list(self._values(entry, "memberOf"))
        for _ in range(5):
            next_frontier = []
            for dn in frontier:
                if dn.lower() in (s.lower() for s in seen):
                    continue
                seen[dn] = None
                conn.search(dn, "(objectClass=*)", self.ldap3.BASE, attributes=["memberOf"])
                if conn.entries:
                    next_frontier.extend(self._values(conn.entries[0], "memberOf"))
            if not next_frontier:
                break
            frontier = next_frontier
        return list(seen)

    @staticmethod
    def _values(entry, attr: str) -> list[str]:
        try:
            return [str(v) for v in entry[attr].values]
        except Exception:  # attribut absent
            return []

    @staticmethod
    def _value(entry, attr: str) -> str | None:
        values = LdapAuthenticator._values(entry, attr)
        return values[0] if values else None

    def authenticate(self, username: str, password: str) -> UserInfo:
        username = (username or "").strip()
        if "\\" in username:              # DOMAINE\identifiant -> identifiant
            username = username.split("\\", 1)[1]
        if "@" in username:               # identifiant@domaine -> identifiant
            username = username.split("@", 1)[0]
        # Un mot de passe vide provoquerait un bind anonyme « réussi » : refus explicite.
        if not username or not password:
            raise AuthError(self.GENERIC_ERROR)
        try:
            if self.cfg.upn_suffix:
                conn = self._connection(f"{username}@{self.cfg.upn_suffix}", password)
                if not conn.bind():
                    raise AuthError(self.GENERIC_ERROR)
                entry = self._find_user(conn, username)
                if entry is None:
                    raise AuthError(self.GENERIC_ERROR)
            else:
                svc = self._connection(self.cfg.bind_dn, self.cfg.bind_password)
                if not svc.bind():
                    log.error("Connexion du compte de service LDAP refusée : %s", svc.result)
                    raise AuthError("Annuaire indisponible, réessayez plus tard")
                entry = self._find_user(svc, username)
                svc.unbind()
                if entry is None:
                    raise AuthError(self.GENERIC_ERROR)
                conn = self._connection(entry.entry_dn, password)
                if not conn.bind():
                    raise AuthError(self.GENERIC_ERROR)
            groups = self._groups(conn, entry)
            conn.unbind()
        except AuthError:
            raise
        except Exception as exc:  # contrôleur injoignable, certificat refusé…
            log.error("Erreur LDAP : %s", exc)
            raise AuthError("Annuaire indisponible, réessayez plus tard") from None

        if self.cfg.allowed_groups:
            wanted = {g.lower() for g in self.cfg.allowed_groups}
            if not any(g.lower() in wanted or _cn(g).lower() in wanted for g in groups):
                raise AuthError(self.DENIED_ERROR, "compte valide mais hors des groupes autorisés")
        return UserInfo(
            username=username.lower(),
            display_name=self._value(entry, "displayName"),
            department=self._value(entry, "department"),
            groups=[_cn(g) for g in groups],
        )


class Auth:
    """Point d'entrée utilisé par le serveur HTTP."""

    def __init__(self, cfg: AuthConfig, authenticator=None):
        self.cfg = cfg
        self.mode = cfg.mode
        self.sessions = SessionStore(cfg.session_hours)
        self.throttle = LoginThrottle(cfg.max_failures, cfg.lockout_minutes)
        self.authenticator = authenticator
        if self.mode == "ldap" and self.authenticator is None:
            self.authenticator = LdapAuthenticator(cfg)

    @property
    def enabled(self) -> bool:
        return self.mode != "none"

    def login(self, username: str, password: str, client_ip: str) -> Session:
        key_user = "u:" + (username or "").strip().lower()
        key_ip = "ip:" + client_ip
        if self.throttle.blocked(key_user, key_ip):
            audit.warning("Connexion bloquée (trop d'échecs) : %s depuis %s", username, client_ip)
            raise AuthError(f"Trop de tentatives : réessayez dans {self.cfg.lockout_minutes} minutes")
        try:
            user = self.authenticator.authenticate(username, password)
        except AuthError as exc:
            self.throttle.failure(key_user, key_ip)
            audit.warning("Échec de connexion : %s depuis %s (%s)", username, client_ip, exc.reason)
            raise
        self.throttle.success(key_user, key_ip)
        audit.info("Connexion : %s depuis %s", user.username, client_ip)
        return self.sessions.create(user)

    def user_from_header(self, headers, client_ip: str) -> UserInfo | None:
        """Mode header : utilisateur transmis par le reverse proxy de confiance."""
        if client_ip not in self.cfg.trusted_proxies:
            return None
        raw = (headers.get(self.cfg.header_name) or "").strip()
        if not raw:
            return None
        username = raw.split("\\", 1)[-1].split("@", 1)[0].lower()
        if self.cfg.allowed_users and username not in self.cfg.allowed_users:
            return None
        return UserInfo(username=username)
