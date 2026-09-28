# Sécurité de HeptagonesLM

Ce document résume l'audit de sécurité de la version 26.1.0, les corrections
appliquées et les recommandations de déploiement. Signaler une vulnérabilité :
ouvrir une issue **sans détail exploitable** et demander un contact privé.

## 1. Audit (27/09/2026)

Périmètre : code (collecte, serveur web, authentification, API, exports, e-mails,
base), interface, scripts de déploiement Windows, CI et publication. Les
vulnérabilités marquées « prouvé » ont été exploitées sur une instance locale ;
chaque correction est couverte par un test qui rejoue l'attaque
(`tests/test_security.py`).

| Note | Avant correction | Après P1 + P2 |
|---|:---:|:---:|
| Globale | **12,5 / 20** | **17 / 20** |

### Corrigé

| Id | Gravité | Vulnérabilité | Correction |
|---|---|---|---|
| H1 | Élevée | DNS rebinding : l'en-tête `Host` n'était pas contrôlé ; un site tiers pouvait lire l'interface locale sans authentification (prouvé) | Liste blanche des noms d'hôte (`localhost`, `127.0.0.1`, `::1`, `public_url`, nom et adresses du serveur, `web.allowed_hosts`) ; sinon **421** |
| H2 | Élevée | STARTTLS LDAP non vérifié : mot de passe AD en clair si le chiffrement échouait | Refus avant tout bind si TLS n'est pas établi |
| H3 | Élevée | Tâches planifiées en `SYSTEM` (privilèges maximaux) par défaut | `-ServiceAccount` obligatoire ; SYSTEM refusé pour l'interface web, ailleurs seulement avec `-AllowSystem` et sans élévation |
| M1 | Moyenne | `Content-Length` négatif : thread bloqué indéfiniment, sans authentification (prouvé) | Taille validée (0 – 8 Kio), sinon 400 |
| M2 | Moyenne | Pas de délai d'expiration : connexions lentes maintenues sans limite (prouvé) | `web.request_timeout_s` (30 s) et `web.max_connections` (64) |
| M3 | Moyenne | Injection de formules dans les exports CSV / Excel via les noms d'utilisateur ou de poste (prouvé) | CSV : préfixe `'` devant `= + - @` ; Excel : cellules forcées en texte |
| M4 | Moyenne | Falsification du journal d'audit par un identifiant contenant des retours à la ligne (prouvé) | Caractères de contrôle neutralisés (`\x0a`) dans les journaux |
| M5 | Moyenne | `TrustServerCertificate=yes` dans les exemples (certificat SQL Server non vérifié) | Exemples en `Encrypt=yes` ; avertissement au démarrage si l'option est utilisée |
| M6 | Moyenne | Identifiants SMTP acceptés sans chiffrement ; vérification du certificat LDAP désactivable sans alerte | Configuration refusée ; avertissement si `ldap_validate_cert: false` |
| M7 | Moyenne | Publication : job de release en écriture exécutant du code tiers ; pas d'empreinte | Construction en lecture seule, publication séparée, `SHA256SUMS` joint ; Playwright figé en CI |
| L1 | Faible | Messages d'erreur internes renvoyés au client (prouvé) | Message générique ; détail dans le journal |
| L2 | Faible | Versions de HeptagonesLM et de Python dans l'en-tête `Server` | `Server: HeptagonesLM` |
| L4 | Faible | Le refus « hors groupe » confirmait la validité du mot de passe | Message unique ; cause précise uniquement dans l'audit |
| L5 | Faible | Droit `db_ddladmin` laissé en permanence | Script et documentation : retrait après `init-db` |

### Risques résiduels (évolutions prévues)

| Id | Constat | Recommandation |
|---|---|---|
| M7b | Dépendances Python en `>=` sans empreintes ; actions GitHub référencées par étiquette ; exécutable non signé | Fichier de verrouillage avec empreintes (`pip-compile --generate-hashes`), actions épinglées par SHA, signature de code Authenticode |
| M8 | Tout utilisateur authentifié voit toutes les données nominatives ; e-mails vers toute adresse | Droits par service ; `allowed_recipient_domains` ; pseudonymisation optionnelle des exports ; validation DPO |
| L3 | Compteur d'échecs de connexion sans plafond global | Plafond et purge périodique |
| L6 | En mode `none`, « poste local » = tous les utilisateurs d'un serveur partagé (RDS) | Utiliser `ldap` sur les serveurs multi-utilisateurs |
| L7 | Serveur HTTP de la bibliothèque standard | Pour une exposition large : IIS en frontal (mode `header`) |

## 2. Liste de contrôle de déploiement

- [ ] Compte de service **dédié, non administrateur** (gMSA recommandé) pour toutes les tâches.
- [ ] Base : `db_datareader` + `db_datawriter` ; `db_ddladmin` retiré après `init-db`.
- [ ] Chaîne de connexion avec `Encrypt=yes` et certificat SQL Server valide (pas de `TrustServerCertificate=yes`).
- [ ] Interface partagée : `auth.mode: ldap` (ou `header` derrière IIS) + **HTTPS** + `allowed_groups`.
- [ ] `public_url` / `allowed_hosts` renseignés avec les noms réellement utilisés par les navigateurs.
- [ ] LDAPS (`ldaps://`) avec vérification du certificat (`ldap_ca_file` si AC interne).
- [ ] SMTP en `starttls` ou `ssl` ; mots de passe dans des variables d'environnement, jamais dans le YAML.
- [ ] Droits NTFS : `heptagoneslm.yaml`, clé TLS et journaux lisibles uniquement par le compte de service et les administrateurs.
- [ ] Journal `heptagoneslm.log` (audit) collecté par le SIEM ou archivé ; durée de conservation définie avec le DPO.
- [ ] Pare-feu : port de l'interface ouvert uniquement vers les réseaux des utilisateurs ; flux sortants limités (serveurs de licences, SQL, DC, SMTP).
- [ ] Intégrité de `HeptagonesLM.exe` vérifiée (`Get-FileHash … -Algorithm SHA256` = `SHA256SUMS` de la release).
