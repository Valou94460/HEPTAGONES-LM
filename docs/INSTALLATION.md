# Installation assistée : FreeLM-Setup.exe

`FreeLM-Setup.exe` installe FreeLM sur un serveur Windows en une vingtaine de minutes,
sans éditer de fichier : un assistant pas à pas s'ouvre dans le navigateur, contrôle
les prérequis, configure la base, les serveurs de licences, IIS et les tâches planifiées,
installe avec une barre de progression, puis **vérifie chaque composant de bout en bout**.

![Vérification finale](images/installation-verification.png)

## Sommaire

1. [Avant de commencer](#1-avant-de-commencer)
2. [Lancer l'assistant](#2-lancer-lassistant)
3. [Les étapes](#3-les-étapes)
4. [Ce que fait l'installation](#4-ce-que-fait-linstallation)
5. [Installation silencieuse](#5-installation-silencieuse-industrialisation)
6. [Serveur sans accès Internet](#6-serveur-sans-accès-internet)
7. [Sécurité de l'assistant](#7-sécurité-de-lassistant)
8. [Dépannage](#8-dépannage)

## 1. Avant de commencer

| À préparer | Détail |
|---|---|
| Serveur | Windows Server 2016 ou plus récent, membre du domaine, compte administrateur local |
| Compte de service | Compte dédié non administrateur, de préférence un **gMSA** (`DOMAINE\gmsa_freelm$`) ; sinon compte + mot de passe |
| SQL Server | Nom de l'instance ; votre compte doit pouvoir créer la base (sysadmin ou dbcreator), sinon le DBA exécute `sql\sqlserver\00_create_database.sql` |
| Certificat HTTPS | Importé dans *Ordinateur local › Personnel* (avec clé privée) pour le nom DNS de l'interface |
| Nom DNS | Enregistrement A ou CNAME (ex. `freelm.entreprise.local`) vers le serveur |
| Serveurs de licences | Adresses `port@serveur`, flux réseau ouverts (port lmgrd et ports des démons) |
| Groupe AD | Personnes autorisées à consulter FreeLM (ex. `GG_FreeLM_Lecteurs`) |
| lmutil.exe | Fourni avec Autodesk Network License Manager ou tout serveur FlexLM |
| Pilote ODBC | *ODBC Driver 18 for SQL Server* (contrôlé par l'assistant, lien de téléchargement fourni) |

## 2. Lancer l'assistant

1. Copier `FreeLM-Setup.exe` sur le serveur (page Releases) et vérifier son empreinte :
   `Get-FileHash FreeLM-Setup.exe -Algorithm SHA256` (comparer à `SHA256SUMS`).
2. Double-cliquer : Windows demande l'élévation (droits administrateur requis).
3. Une fenêtre console affiche l'adresse de l'assistant et le navigateur s'ouvre.
   **Laisser la console ouverte** pendant toute l'installation.

Démonstration sans rien modifier : `FreeLM.exe setup --simulate` (tout poste, même non Windows
avec Python : `freelm setup --simulate`).

## 3. Les étapes

| # | Étape | Contenu | Tests proposés |
|---|---|---|---|
| 1 | Bienvenue | Résumé, informations sur le serveur, éléments à préparer | — |
| 2 | Vérification du serveur | Système, droits, disque, domaine, pilote ODBC, lmutil, port, rôles et modules IIS, certificats | Corrections automatiques : rôles IIS, URL Rewrite, ARR |
| 3 | Emplacement et compte | Dossier d'installation, compte de service, intervalle de collecte | Compte trouvé dans l'annuaire, mot de passe valide |
| 4 | Base de données | SQL Server (instance, base, pilote, authentification Windows ou SQL, chiffrement, création) ou SQLite | Connexion, existence de la base, droits de création |
| 5 | Serveurs de licences | Chemin de lmutil, serveurs (triades, log debug) | `lmstat` en direct pour chaque serveur |
| 6 | Interface web et IIS | IIS avec connexion Windows automatique (recommandé), HTTPS intégré avec identifiants AD, ou poste local ; pare-feu | Certificat (nom, validité), groupe AD, connexion LDAP |
| 7 | Rapport par e-mail | Relais SMTP, expéditeur, destinataires (optionnel) | Envoi d'un message de test |
| 8 | Récapitulatif | Configuration, contrôle final des prérequis, tâches, étapes, `freelm.yaml` généré | — |
| 9 | Installation | Barre de progression, étapes en direct, journal détaillé ; « Réessayer » reprend à l'étape en échec | — |
| 10 | Vérification | Contrôle de bout en bout, adresse de l'interface, rapport d'installation téléchargeable | — |

<table>
<tr>
<td><img src="images/installation-prerequis.png" alt="Prérequis"></td>
<td><img src="images/installation-licences.png" alt="Serveurs de licences"></td>
</tr>
<tr>
<td align="center"><em>Prérequis et corrections automatiques</em></td>
<td align="center"><em>Serveurs de licences testés en direct</em></td>
</tr>
<tr>
<td><img src="images/installation-iis.png" alt="IIS"></td>
<td><img src="images/installation-recapitulatif.png" alt="Récapitulatif"></td>
</tr>
<tr>
<td align="center"><em>IIS : certificat, groupe autorisé</em></td>
<td align="center"><em>Récapitulatif avant installation</em></td>
</tr>
</table>

![Installation en cours](images/installation-progression.png)

## 4. Ce que fait l'installation

Chaque étape est **idempotente** : relancer l'assistant (ou « Réessayer ») ne casse rien.

| Étape | Action |
|---|---|
| Dossiers | `C:\FreeLM` avec `logs`, `reports` (et `data` en SQLite, `iis` en mode IIS) |
| Copie de FreeLM | `FreeLM.exe` embarqué dans l'assistant copié dans le dossier |
| Configuration | `freelm.yaml` généré et validé ; l'ancienne version est sauvegardée (`.bak`) |
| Secrets | Mots de passe SQL / SMTP dans des variables d'environnement machine (`FREELM_DB_PASSWORD`, `FREELM_SMTP_PASSWORD`), jamais dans le fichier |
| Droits | Compte de service : lecture sur le dossier, modification sur `logs`, `reports`, `data` |
| Base | Création de la base (récupération SIMPLE), de la connexion Windows du compte de service, rôles `db_datareader` / `db_datawriter` |
| Schéma | `FreeLM.exe init-db` |
| Première collecte | `FreeLM.exe collect` : chaque serveur de licences est interrogé |
| IIS | Rôles et modules manquants, pool sans code managé, site HTTPS (liaison SNI + certificat), authentification Windows seule, proxy ARR, variable `HTTP_X_REMOTE_USER`, autorisation du groupe, `web.config` de relais vers FreeLM (écoute locale) |
| Tâches | Collecte, rapport mensuel, envoi des e-mails, purge, interface web permanente, sous le compte de service |
| Pare-feu | Règle entrante sur le port HTTPS (profils Domaine et Privé) |
| Démarrage | Lancement de l'interface et attente de sa réponse |

La vérification finale contrôle : exécutable et configuration, schéma de la base, dernière
collecte de chaque serveur, présence et état des tâches, réponse de FreeLM, site IIS démarré
et chaîne HTTPS + authentification Windows (401 attendu sans identifiants), règle de pare-feu,
résolution DNS. Le rapport d'installation est aussi écrit dans `C:\FreeLM\logs\setup-*.log`.

## 5. Installation silencieuse (industrialisation)

```powershell
$env:SVC_PWD = Read-Host "Mot de passe du compte de service"
FreeLM-Setup.exe setup --answers reponses.json
```

Mêmes contrôles, étapes et vérification que l'assistant, avec une progression en mode texte.
Les valeurs `${VARIABLE}` sont remplacées par les variables d'environnement (mots de passe
hors du fichier). Codes retour : **0** succès, **1** échec, **2** installé avec des erreurs de vérification.

```json
{
  "install_dir": "C:\\FreeLM",
  "service_account": "ENTREPRISE\\gmsa_freelm$",
  "db_server": "SQLSRV01",
  "db_name": "FreeLM",
  "lmutil_path": "C:\\FreeLM\\bin\\lmutil.exe",
  "servers": [
    { "name": "AUTODESK", "port_at_host": "27000@licsrv1" },
    { "name": "ANSYS", "port_at_host": "1055@licsrv2", "collectors": ["lmstat", "log"],
      "log_path": "\\\\licsrv2\\ansys_logs$\\license.log" }
  ],
  "web_mode": "iis",
  "public_host": "freelm.entreprise.local",
  "iis_cert_thumbprint": "3F2A9C4D8E1B7A6F5C0D2E9B8A7F6C5D4E3B2A19",
  "access_group": "ENTREPRISE\\GG_FreeLM_Lecteurs",
  "auto_fix": true
}
```

| Clé | Défaut | Rôle |
|---|---|---|
| `install_dir` | `C:\FreeLM` | Dossier d'installation |
| `service_account` / `service_password` | — | Compte des tâches (mot de passe inutile pour un gMSA) |
| `collect_interval` | 5 | Minutes entre deux collectes |
| `db_mode` | `sqlserver` | `sqlserver` ou `sqlite` |
| `db_server`, `db_name`, `db_driver` | —, `FreeLM`, ODBC 18 | Connexion SQL Server |
| `db_auth`, `db_user`, `db_password` | `windows` | `windows` (compte de service) ou `sql` |
| `db_encrypt`, `db_trust_cert`, `db_create` | `true`, `false`, `true` | Chiffrement, confiance du certificat SQL, création de la base |
| `lmutil_path`, `servers[]` | — | `name`, `port_at_host`, `collectors`, `log_path` |
| `web_mode` | `iis` | `iis`, `https` ou `local` |
| `web_port` | 8765 | Port interne (IIS) ou HTTPS (mode `https`) |
| `public_host`, `access_group` | — | Nom DNS, groupe `DOMAINE\groupe` autorisé |
| `iis_site`, `iis_port`, `iis_cert_thumbprint` | `FreeLM`, 443 | Site IIS et certificat (empreinte) |
| `tls_cert`, `tls_key`, `ldap_servers`, `base_dn`, `upn_suffix` | — | Mode `https` |
| `open_firewall` | `true` | Règle de pare-feu |
| `email_enabled`, `smtp_host`, `smtp_port`, `smtp_security`, `smtp_user`, `smtp_password`, `sender`, `recipients` | désactivé | Rapport par e-mail |
| `auto_fix` | `false` | Installer automatiquement les rôles et modules IIS manquants |

## 6. Serveur sans accès Internet

Les rôles IIS s'installent depuis Windows. Les modules **URL Rewrite** et **ARR** sont
téléchargés chez Microsoft ; hors ligne, déposer les installeurs à côté de
`FreeLM-Setup.exe` ou dans un sous-dossier `prerequis\` :

- `rewrite_amd64_en-US.msi` (URL Rewrite 2.1)
- `requestRouter_amd64.msi` (Application Request Routing 3.0)

## 7. Sécurité de l'assistant

- Écoute sur `127.0.0.1` uniquement, jeton aléatoire propre à la session (retiré de
  l'adresse et conservé dans un cookie `HttpOnly`, `SameSite=Strict`), contrôle de l'en-tête
  `Host`, en-tête obligatoire sur les requêtes de modification (anti requêtes intersites).
- Mots de passe gardés en mémoire le temps de l'installation, transmis à PowerShell par une
  variable d'environnement du seul processus enfant (jamais en ligne de commande), absents du
  journal et du rapport d'installation.
- Compte SYSTEM refusé pour les tâches ; droits minimaux sur les dossiers et la base.
- FreeLM n'écoute que sur la boucle locale derrière IIS ; l'en-tête `X-Remote-User` n'est
  accepté que du proxy local et est toujours écrasé par IIS.

## 8. Dépannage

| Symptôme | Solution |
|---|---|
| « Droits d'administrateur » en erreur | Relancer par clic droit › *Exécuter en tant qu'administrateur* |
| Module URL Rewrite / ARR : échec de téléchargement | Serveur hors ligne ou proxy : déposer les MSI (§ 6) puis « Relancer la vérification » |
| Rôles IIS installés mais « redémarrage nécessaire » | Redémarrer le serveur puis relancer l'assistant (les étapes déjà faites sont rejouées sans effet) |
| Base : « droits insuffisants pour la créer » | Utiliser un compte sysadmin / dbcreator, ou faire exécuter `00_create_database.sql` par le DBA et décocher la création |
| Serveur de licences injoignable au test | Vérifier `port@serveur`, le pare-feu (port lmgrd **et** port du démon éditeur) |
| Échec à l'étape « Tâches planifiées » | Mot de passe du compte de service ; pour un gMSA : `Install-ADServiceAccount` sur le serveur |
| Vérification : « IIS ne joint pas FreeLM (502) » | La tâche « FreeLM - Interface web » est arrêtée : consulter `C:\FreeLM\logs\freelm.log` |
| Navigateur : avertissement de certificat | Le certificat ne couvre pas le nom DNS choisi (test « Vérifier le certificat ») |
| Connexion Windows qui redemande un mot de passe | Ajouter l'adresse à la zone *Intranet local* des navigateurs (GPO) ; nom DNS alias : `setspn -S HTTP/freelm.entreprise.local NOMSERVEUR` |
