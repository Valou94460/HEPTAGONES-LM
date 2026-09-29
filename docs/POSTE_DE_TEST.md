# Poste de test automatisé (facultatif) : installation filmée par un runner GitHub

> **Pour installer à la main avec `HeptagonesLM-Setup.exe`, sans PowerShell, voir
> [TEST_MANUEL_SETUP.md](TEST_MANUEL_SETUP.md).** Ce guide-ci automatise le même parcours pour le
> rejouer à chaque version.

Ce guide transforme un PC de l'entreprise en **runner GitHub auto-hébergé**. Depuis GitHub, un clic
lance sur ce PC une installation **réelle** de HeptagonesLM par l'assistant graphique, pilotée par un
navigateur et **filmée** :

1. prérequis (installation réelle des rôles IIS, d'URL Rewrite et d'ARR) ;
2. compte de service, vérifié par Windows ;
3. serveurs de licences, bouton « Rechercher l'outil », test lmstat ;
4. site IIS en HTTPS et groupe autorisé ;
5. installation, puis vérification finale ;
6. connexion Windows via IIS, collecte par la tâche planifiée, visite de l'interface.

On récupère ensuite la vidéo, les captures et les journaux. Pour finir, le PC est désinstallé et
nettoyé automatiquement.

Les jobs qui tournent sur votre PC ne consomment **pas** de minutes GitHub Actions.

> **Le PC doit être un poste de test.** L'essai installe IIS et un site HTTPS, crée des tâches
> planifiées et, en mode « local », des comptes locaux. Le nettoyage retire tout ce qui a été créé,
> sauf les rôles IIS. Ne pas utiliser un serveur de production.

---

## 1. Préparer le PC (≈ 20 min)

| Élément | Exigence |
|---|---|
| Windows | **Windows 10/11 Pro ou Entreprise**, ou Windows Server 2016+. Pas Windows Famille : l'authentification Windows d'IIS n'y existe pas. |
| Compte | Un compte **administrateur local** du PC, avec lequel le runner sera lancé. |
| Réseau | Accès Internet sortant HTTPS vers github.com et les téléchargements (Python, Node, Chromium, modules IIS). |
| Disque | 10 Go libres. |
| Domaine *(facultatif)* | PC **membre du domaine** pour tester avec de vrais comptes et groupes AD (mode `domaine`). |
| Serveur de licences *(facultatif)* | Un serveur FlexLM joignable depuis le PC (mode `reel`). |

Dans une **PowerShell administrateur**, installer PowerShell 7 et Git :

```powershell
winget install --id Microsoft.PowerShell -e
winget install --id Git.Git -e
```

Python et Node.js sont installés automatiquement par le workflow ; il n'y a rien d'autre à installer.

## 2. Déclarer le PC comme runner du dépôt (≈ 10 min)

1. Sur GitHub, ouvrir le dépôt **HEPTAGONES-LM** > **Settings** > **Actions** > **Runners** >
   **New self-hosted runner** > **Windows** / **x64**.
2. GitHub affiche des commandes à copier. Les exécuter **dans une PowerShell administrateur**, dans un
   dossier court, par exemple `C:\actions-runner` :
   - le bloc **Download** (téléchargement et décompression de l'agent) ;
   - le bloc **Configure** (`./config.cmd --url … --token …`).
3. Pendant `config.cmd`, répondre :

   | Question | Réponse |
   |---|---|
   | runner group | Entrée (Default) |
   | name of runner | Entrée (nom du PC) |
   | **additional labels** | **`heptagoneslm-test`** (indispensable : le workflow cible ce libellé) |
   | work folder | Entrée (`_work`) |
   | run as service? | **N** (lancement manuel, voir ci-dessous) |

4. Lancer le runner **dans la PowerShell administrateur** et laisser la fenêtre ouverte :

   ```powershell
   cd C:\actions-runner
   .\run.cmd
   ```

   Le message `Listening for Jobs` indique que le PC attend les essais. Dans **Settings > Actions >
   Runners**, le PC apparaît « Idle » avec les libellés `self-hosted`, `Windows`, `X64` et
   `heptagoneslm-test`.

> Pourquoi pas en service ? Par défaut, le service tourne sous NETWORK SERVICE, sans droits
> d'administrateur, et l'installation échouerait. Le lancement manuel dans une console administrateur
> est le plus simple pour des essais. Pour un runner permanent, configurer le service avec un compte
> administrateur dédié.

**Sécurité** : le workflow ne se lance qu'à la main (pas sur les pull requests), et les autres jobs de
la CI visent les machines GitHub (`windows-latest`) et non ce PC. Garder le dépôt **privé** tant qu'un
runner auto-hébergé y est déclaré.

## 3. Choisir le mode d'essai

| Paramètre | Valeurs | Effet |
|---|---|---|
| `comptes` | **local** *(défaut)* | Compte de service, lecteur de test et groupe **locaux** créés pour l'essai, mot de passe aléatoire à chaque fois. Aucun prérequis. |
| | **domaine** | Vrais comptes et groupe **AD**. Le PC doit être membre du domaine. Renseigner les secrets ci-dessous. |
| `lmutil` | **test** *(défaut)* | Un `lmutil.exe` de test est placé dans `C:\Flexlm` : il répond comme lmstat avec les données de la démonstration. Aucun serveur de licences nécessaire. |
| | **reel** | Aucun outil de test : l'assistant doit **trouver le vrai lmutil.exe**, sur le PC ou sur vos serveurs de licences (partage `C$`, droits administrateur), puis interroger vos **vrais serveurs**. |
| `serveurs` | `NOM=port@serveur;…` | Serveurs déclarés dans l'assistant. En mode `reel`, vos serveurs, par exemple `AUTODESK=27000@srvlic01;ANSYS=1055@srvlic02`. |
| `nettoyer` | oui *(défaut)* / non | Désinstaller et nettoyer à la fin. « Non » laisse l'application installée pour l'explorer, à l'adresse https://heptagoneslm.test.local depuis le PC. |

### Secrets pour le mode `domaine`

Dans **Settings > Secrets and variables > Actions > New repository secret** :

| Secret | Exemple | Rôle |
|---|---|---|
| `HLM_SVC_ACCOUNT` | `ENTREPRISE\svc_heptagoneslm_test` | Compte de service (collecte, interface). **Pas** membre du groupe autorisé : il sert aussi à vérifier le refus d'accès. |
| `HLM_SVC_PASSWORD` | … | Son mot de passe. |
| `HLM_ACCESS_GROUP` | `ENTREPRISE\GG_HeptagonesLM_Lecteurs` | Groupe AD autorisé à ouvrir l'interface. |
| `HLM_TEST_USER` | `ENTREPRISE\jdupont` | Utilisateur **membre** du groupe, pour la connexion Windows de test. |
| `HLM_TEST_PASSWORD` | … | Son mot de passe. |

Les mots de passe sont masqués dans les journaux GitHub et ne sont écrits dans aucun fichier.

## 4. Lancer l'essai

1. GitHub > **Actions** > **Installation réelle (poste de test)** > **Run workflow**.
2. Choisir la branche, les paramètres, puis **Run workflow**.
3. Suivre le déroulement en direct. La fenêtre `run.cmd` du PC affiche aussi `Running job`.
   Compter 20 à 45 minutes ; le premier essai est plus long (installation des rôles IIS, téléchargements).

> Le bouton **Run workflow** n'apparaît que lorsque le fichier `.github/workflows/installation-reelle.yml`
> est présent sur la branche **par défaut** (main), c'est-à-dire après la fusion de la PR qui l'apporte.

## 5. Récupérer la preuve

En bas de la page du run, l'artefact **`installation-reelle-poste-de-test`** (zip) contient :

| Fichier | Contenu |
|---|---|
| `installation_assistant.webm` | La vidéo de l'installation par l'assistant, sur **votre** PC, sans bandeau « simulation ». |
| `interface_installee.webm` | La vidéo de l'interface ouverte via IIS avec la connexion Windows de l'utilisateur de test. |
| `01_…png` à `12_…png`, `interface_*.png` | Une capture par étape. |
| `deroule.txt` | Le déroulé horodaté : version de Windows du PC, chaque test, chaque étape d'installation, chaque contrôle final. |
| `journal_installation.txt`, `rapport_installation.txt` | Journal et rapport de l'assistant. |
| `heptagoneslm.yaml`, `web.config`, `logs/` | Configuration générée, configuration IIS, journaux de l'application. |

Le parcours **échoue** s'il détecte le mode simulation, si un test ou un contrôle est en erreur, si la
connexion Windows via IIS ne transmet pas le bon utilisateur, si un compte hors du groupe ou un
accès anonyme est accepté, ou si la collecte planifiée échoue.

## 6. Après l'essai

- Avec `nettoyer = oui`, le PC retrouve son état initial. Seuls restent les rôles et modules IIS
  (Windows Authentication, URL Rewrite, ARR), réutilisés par l'essai suivant.
- Pour arrêter le runner : `Ctrl+C` dans la fenêtre `run.cmd`.
- Pour le retirer du dépôt : **Settings > Actions > Runners** > le PC > **Remove**, puis
  `.\config.cmd remove --token …` sur le PC.

## Dépannage

| Symptôme | Cause probable |
|---|---|
| Le run reste « Queued » | Runner arrêté (fenêtre `run.cmd` fermée) ou libellé `heptagoneslm-test` absent. |
| « Le runner doit tourner avec des droits d'administrateur » | `run.cmd` lancé depuis une PowerShell non administrateur. |
| « Windows Famille/Home » | Édition sans authentification Windows pour IIS : utiliser un PC Pro ou Entreprise. |
| Échec à l'étape « prérequis » | Téléchargement d'URL Rewrite ou d'ARR bloqué (proxy, filtrage) : voir `deroule.txt` et le guide d'installation. |
| Mode `reel` : « lmutil.exe non retenu » | Aucun lmutil.exe sur le PC, et partage `C$` des serveurs de licences inaccessible : copier lmutil.exe dans `C:\Flexlm` sur le PC, puis relancer. |
| Mode `domaine` : « Secret … manquant » | Secret non créé ou nom mal orthographié (section 3). |
| « C:\Flexlm\lmutil.exe existe déjà » | Un vrai lmutil est présent : lancer en mode `lmutil = reel`. |
