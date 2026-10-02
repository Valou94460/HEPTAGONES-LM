# Test manuel : installer HeptagonesLM avec HeptagonesLM-Setup.exe et le filmer

Installation **à la main**, par l'assistant graphique `HeptagonesLM-Setup.exe`, sur un PC de test,
**sans PowerShell** et sans script. Vous cliquez dans l'assistant et Windows filme l'écran.

Deux essais, du plus simple au plus complet :

| Essai | Mode de l'interface | Prouve | Durée |
|---|---|---|---|
| **A** | Poste local | Installation, tâches planifiées, compte de service, **collecte réelle** depuis vos serveurs de licences, interface avec les données | 30 min |
| **B** | IIS · connexion Windows | Tout l'essai A, plus IIS installé par l'assistant, HTTPS, connexion Windows automatique, restriction par groupe | 45 min |

---

## 1. Ce qu'il faut

| Élément | Exigence |
|---|---|
| PC | **Windows 10/11 Pro ou Entreprise**, ou Windows Server. Pas Windows Famille (pas d'authentification Windows dans IIS). Un poste de test, pas un serveur de production. |
| Droits | Un compte **administrateur** du PC. L'assistant demande l'élévation au lancement. |
| Serveur de licences | Pour de **vraies données** : un serveur FlexLM joignable depuis le PC (adresse `port@serveur`, par exemple `27000@srvlic01`), et **`lmutil.exe`**. Ce dernier se trouve dans le dossier du serveur de licences (à côté de `lmgrd.exe`) ; le copier par exemple dans `C:\Flexlm\lmutil.exe` sur le PC. |
| Setup | **`HeptagonesLM-Setup.exe`** : voir la section 2. |

## 2. Récupérer HeptagonesLM-Setup.exe

Télécharger **`HeptagonesLM-Setup.exe`** depuis la release **v26.3.0** :
https://github.com/Valou94460/HEPTAGONES-LM/releases/tag/v26.3.0

C'est le seul fichier nécessaire : l'application `HeptagonesLM.exe` est incluse dedans. Vérification d'intégrité
(facultative), dans une invite de commandes :

```
certutil -hashfile HeptagonesLM-Setup.exe SHA256
```

Le résultat doit correspondre à la ligne `HeptagonesLM-Setup.exe` du fichier `SHA256SUMS` de la release.

Windows SmartScreen peut afficher « Windows a protégé votre ordinateur » (exécutable non signé) :
**Informations complémentaires** > **Exécuter quand même**.

## 3. Préparer le PC (sans PowerShell)

### Compte de service (essais A et B)

Il exécute la collecte et l'interface. Pour un test, un compte local suffit :

1. `Win + R` > `lusrmgr.msc` > **Utilisateurs** > clic droit > **Nouvel utilisateur**.
2. Nom `svc_heptagoneslm`, mot de passe fort ; décocher « doit changer le mot de passe », cocher
   « Le mot de passe n'expire jamais » > **Créer**.

Dans un domaine, un compte AD dédié fourni par l'équipe AD convient aussi (`ENTREPRISE\svc_heptagoneslm`).

### Groupe autorisé et utilisateur de test (essai B uniquement)

1. `lusrmgr.msc` > **Groupes** > clic droit > **Nouveau groupe** : `HeptagonesLM_Lecteurs`.
2. **Utilisateurs** > nouvel utilisateur `hlm_lecteur`, puis l'ajouter au groupe.
   (Dans un domaine : un groupe AD et un utilisateur membre.)

### Démarrer l'enregistrement de l'écran

- **Windows 11** : Outil Capture d'écran (`Win + Maj + S`, puis l'icône caméra **Enregistrer**), sélectionner tout l'écran.
- **Windows 10 / 11** : Xbox Game Bar (`Win + Alt + R`) filme la fenêtre active, ici le navigateur de l'assistant.

---

## 4. Essai A : installation « Poste local » avec vos vraies données

Lancer `HeptagonesLM-Setup.exe` et accepter l'élévation. La fenêtre noire doit rester ouverte, et le
navigateur s'ouvre sur l'assistant. **Aucun bandeau jaune « Mode simulation » ne doit apparaître** :
c'est une installation réelle.

| Étape de l'assistant | Quoi faire | Attendu |
|---|---|---|
| 1. Bienvenue | **Suivant** | — |
| 2. Vérification du serveur | Lire les contrôles, **Suivant** | Aucun contrôle rouge (en mode Poste local, IIS n'est pas requis). |
| 3. Emplacement et compte de service | Dossier `C:\HeptagonesLM` ; compte `NOMDUPC\svc_heptagoneslm` + mot de passe ; **Tester le compte** | Vert : « compte trouvé et mot de passe valide ». |
| 4. Base de données | Choisir **SQLite local** | — |
| 5. Serveurs de licences | Chaque serveur : nom (ex. `AUTODESK`), type FlexLM, adresse `27000@srvlic01` ; bouton **Rechercher l'outil** (ou le chemin `C:\Flexlm\lmutil.exe`, ou simplement le dossier) ; **Tester** sur chaque ligne | Vert : « N features, x / y jetons utilisés », avec les vrais noms de vos licences. |
| 6. Interface web | Choisir **Poste local** | — |
| 7. Rapport par e-mail | Laisser désactivé (ou renseigner votre SMTP puis **Envoyer un message de test au premier destinataire**) | — |
| 8. Tickets ITSM | Laisser désactivé, ou cocher **Ouvrir automatiquement des tickets**, choisir l'outil (ServiceNow, Jira, GLPI, Teams / Slack), saisir l'adresse et le compte technique, puis **Ouvrir un ticket de test** | Vert : « ticket de test ouvert, commenté puis résolu », avec son numéro ; le ticket est visible (résolu) dans l'outil. |
| 9. Récapitulatif | Relire, **Installer** | Tickets ITSM : « Secrets chiffrés par Windows (DPAPI) ». |
| 10. Installation | Attendre | Toutes les étapes vertes. |
| 11. Vérification | Lire | « HeptagonesLM est installé et opérationnel » (les avertissements orange sont acceptables) ; si l'ITSM est activé, « Secret chiffré itsm_password.dpapi » vert. |

Ensuite, vérifier les **vraies données** :

1. Attendre 5 minutes (première collecte planifiée), ou lancer la tâche tout de suite : menu
   Démarrer > **Planificateur de tâches** > Bibliothèque > **HeptagonesLM** > « HeptagonesLM - Collecte »
   > clic droit > **Exécuter**. Colonne « Résultat de la dernière exécution » : `L'opération a réussi (0x0)`.
2. Ouvrir **http://localhost:8765** (le port indiqué à l'étape 6). Contrôler que :
   - la vue **En direct** affiche vos produits et les utilisateurs connectés en ce moment ;
   - ces chiffres correspondent à la sortie de `lmutil lmstat -a` au même instant.

Arrêter l'enregistrement.

## 5. Essai B : IIS et connexion Windows automatique

L'interface IIS demande un **certificat HTTPS**. Sur un PC de test, on le crée avec le Gestionnaire
IIS, disponible une fois IIS installé par l'assistant :

1. Lancer `HeptagonesLM-Setup.exe`, aller à l'étape **Vérification du serveur** et cliquer sur les
   boutons **Installer** proposés (rôles IIS, URL Rewrite, ARR). L'installation est réelle et prend
   quelques minutes. Fermer ensuite l'assistant (fenêtre noire).
2. Menu Démarrer > **Gestionnaire des services Internet (IIS)** > le nom du PC > **Certificats de
   serveur** > **Créer un certificat auto-signé** > nom `heptagoneslm-test`, magasin **Personnel** > OK.
3. Relancer `HeptagonesLM-Setup.exe` (démarrer l'enregistrement de l'écran) et suivre l'essai A, sauf :

| Étape | Différence |
|---|---|
| 6. Interface web | **IIS · connexion Windows automatique**. Nom DNS : le **nom du PC** (ex. `pc-test01`, car le certificat auto-signé porte ce nom) ; certificat `heptagoneslm-test` ; groupe `NOMDUPC\HeptagonesLM_Lecteurs`. **Vérifier le certificat** et **Vérifier le groupe** doivent passer au vert ; un avertissement sur le nom du certificat est acceptable pour un test. |

4. Après l'installation, ouvrir `https://nom-du-pc` dans Edge, **depuis la session de `hlm_lecteur`**
   (fermer la session, se connecter avec `hlm_lecteur`), ou depuis un autre poste du domaine avec un
   utilisateur du groupe. Attendu :
   - l'interface s'ouvre **sans saisie de mot de passe** (ou avec une invite de connexion sur un
     poste hors domaine), et le nom de l'utilisateur apparaît en haut à droite ;
   - un utilisateur **hors du groupe** reçoit un refus (401).
5. Page de test d'accès, depuis un poste utilisateur : `https://nom-du-pc/acces`.

## 6. Après les essais

Pour retirer l'application du PC de test :

1. **Planificateur de tâches** > dossier **HeptagonesLM** : supprimer les tâches.
2. *(Essai B)* **Gestionnaire IIS** > **Sites** > supprimer `HeptagonesLM` ; **Pools d'applications** > supprimer `HeptagonesLM`.
3. Supprimer le dossier `C:\HeptagonesLM`.
4. `lusrmgr.msc` : supprimer `svc_heptagoneslm`, `hlm_lecteur` et `HeptagonesLM_Lecteurs`.

Les rôles IIS peuvent rester installés.

## 7. Ce qu'il faut me renvoyer

- Les **vidéos** des essais.
- Le **rapport d'installation** : bouton **Télécharger le rapport d'installation** à la dernière étape de l'assistant.
- La sortie de `lmutil lmstat -a -c port@serveur` pour chaque serveur, pour que j'en fasse des tests
  de non-régression. Noms d'utilisateurs anonymisés si besoin.
- En cas de problème : une capture de l'étape, plus le dossier `C:\HeptagonesLM\logs`.
