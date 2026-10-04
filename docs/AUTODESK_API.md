# Connecteur API Autodesk (licences nommées)

HeptagonesLM lit l'usage des licences Autodesk « utilisateur nommé » directement chez Autodesk, avec
l'API **Business Success Plan Reporting** (anciennement Premium Reporting, service « Insights »
d'Autodesk Platform Services). Il n'y a plus d'export à faire à la main depuis le portail Autodesk
Account. L'import des exports reste disponible et les deux sources se complètent : voir le § 5.

## 1. Prérequis

| Élément | Détail |
|---|---|
| Abonnement | Équipe Autodesk avec le **Business Success Plan** et l'avantage « Reporting API ». Sans cet avantage, l'API répond mais ne renvoie aucune donnée. |
| Compte | Un **administrateur** de l'équipe dans Autodesk Account (connexion une seule fois). |
| Application Autodesk | À créer sur [aps.autodesk.com](https://aps.autodesk.com) › *My Apps* › *Create application*, avec l'API *Business Success Plan Reporting* (ou *Premium Reporting*) cochée. |
| Réseau | Le serveur HeptagonesLM doit joindre `developer.api.autodesk.com:443` (proxy, pare-feu). |

**Type d'application :**

- *Desktop, Mobile, Single-Page App* : connexion PKCE, seul le **Client ID** est nécessaire. C'est le
  type recommandé.
- *Traditional Web App* : **Client ID** et **Client Secret**. Le secret est chiffré par Windows
  (DPAPI) dans `secrets\`.

**URL de rappel (Callback URL)** à déclarer dans l'application, exactement, sans barre finale :

```
http://localhost:8770/autodesk/callback
```

Le port (8770) se change dans l'assistant. Si vous le changez, déclarez la même URL dans
l'application Autodesk.

## 2. Configuration avec HeptagonesLM-Setup.exe

Étape **« Licences Autodesk nommées »** de l'assistant :

1. Cocher *Suivre les licences Autodesk nommées* : un dossier surveillé est créé pour les exports du
   portail.
2. Cocher *Lire l'usage par l'API Autodesk*, puis saisir le **Client ID** (et le Client Secret pour
   une application *Traditional Web App*).
3. Cliquer sur **Se connecter à Autodesk**. Un onglet s'ouvre et l'administrateur Autodesk s'y
   connecte puis accepte l'accès. L'assistant liste ensuite les équipes. S'il n'y en a qu'une avec
   l'API de reporting, elle est choisie automatiquement.
4. Terminer l'installation comme d'habitude. La collecte (toutes les 5 minutes) synchronise l'usage
   au plus une fois par 24 heures.

Le jeton est conservé chiffré par Windows (DPAPI, portée machine) dans
`C:\HeptagonesLM\data\autodesk_token.dpapi`, et il est renouvelé à chaque synchronisation. Le jeton de
renouvellement Autodesk expire s'il n'est pas utilisé pendant **15 jours** (serveur arrêté, par
exemple). Dans ce cas, relancer la connexion :

```
C:\HeptagonesLM\HeptagonesLM.exe -c C:\HeptagonesLM\heptagoneslm.yaml autodesk-login
```

### Sur une installation existante (26.3.3)

Pas besoin de réinstaller : lancer `HeptagonesLM-Setup.exe` sur le serveur, puis cliquer sur
**« Configurer les licences Autodesk »** sur la page d'accueil. Le formulaire est le même qu'au § 2,
connexion à Autodesk comprise. **« Enregistrer la configuration Autodesk »** :

- crée le dossier surveillé, avec les droits du compte de service ;
- chiffre le Client Secret (DPAPI) dans `secrets\`, s'il y en a un ;
- écrit la seule section `autodesk` de `heptagoneslm.yaml`. Le reste du fichier n'est pas modifié, les
  coûts saisis à la main sont conservés, et une sauvegarde `.bak` est faite avant ;
- redémarre l'interface web et lance une collecte, qui importe les exports déjà déposés.

Base, tâches planifiées, site IIS et mots de passe ne sont pas touchés, et rien n'est ressaisi.
Décocher *Suivre les licences Autodesk nommées* puis enregistrer retire la section.

## 3. Commandes

| Commande | Rôle |
|---|---|
| `HeptagonesLM.exe autodesk-login [--no-browser]` | Connexion (ou reconnexion) d'un administrateur ; liste les équipes et leur `context_id` |
| `HeptagonesLM.exe autodesk-sync --dry-run` | Interroge Autodesk et affiche le résultat **sans rien écrire** (validation) |
| `HeptagonesLM.exe autodesk-sync` | Synchronisation immédiate (la collecte la fait aussi, au plus toutes les `sync_hours`) |

## 4. Paramètres (`heptagoneslm.yaml`, section `autodesk.api`, écrite par l'assistant)

| Paramètre | Défaut | Description |
|---|---|---|
| `client_id` | — | Client ID de l'application Autodesk |
| `client_secret` | — | `dpapi:…` (application *Traditional Web App*) ; absent : PKCE |
| `context_id` | — | Équipe Autodesk ; absent : la seule équipe avec l'API de reporting |
| `callback_port` | 8770 | Port de l'URL de rappel `http://localhost:<port>/autodesk/callback` |
| `lookback_days` | 90 | Période d'usage interrogée à chaque synchronisation (1 à 365) |
| `sync_hours` | 24 | Intervalle minimal entre deux synchronisations |
| `token_file` | `<installation>\data\autodesk_token.dpapi` | Jeton chiffré |

## 5. Ce que fait la synchronisation

1. `GET /insights/v1/contexts` : équipes accessibles au compte connecté.
2. `POST /insights/v1/usage-queries` (asynchrone), avec :
   - champs `fullName`, `emailAddress`, `productName` ;
   - mesures `earliestUsageDate`, `latestUsageDate`, `totalUniqueDays` ;
   - filtre sur `usageDate` (les `lookback_days` derniers jours).
3. `GET /insights/v1/usage-queries/<id>` jusqu'à l'état `COMPLETED`, puis lecture de toutes les pages.
   La limite de débit d'Autodesk (code 429) est respectée : l'attente demandée par Autodesk est
   appliquée.
4. Enregistrement de l'état du jour, qui alimente le rapport « Autodesk · utilisateurs nommés ».

L'API ne renvoie que les utilisateurs **qui ont utilisé** un produit sur la période. Les licences
affectées connues par le dernier export du portail, ou par la synchronisation précédente, sont donc
reprises avec leur dernière utilisation connue. Les licences **inactives** et **jamais utilisées**
restent ainsi visibles. Il suffit de déposer de temps en temps un export du portail dans le dossier
surveillé pour mettre à jour la liste des affectations, nouvelles ou retirées.

Si l'administrateur Autodesk a activé le masquage des données personnelles, l'API refuse les noms
et les e-mails. HeptagonesLM utilise alors l'identifiant haché (`hashedUserId`) et le signale.

## 6. Validation sur votre compte (recette)

Le connecteur est testé contre un faux serveur Autodesk qui reproduit le contrat de l'exemple
officiel [aps-business-success-plan-report](https://github.com/autodesk-platform-services/aps-business-success-plan-report).
Il reste à le valider sur un vrai compte :

1. Faire l'étape de l'assistant (§ 2) jusqu'à **Se connecter à Autodesk** : les équipes doivent
   s'afficher.
2. Après l'installation, lancer `HeptagonesLM.exe -c C:\HeptagonesLM\heptagoneslm.yaml autodesk-sync --dry-run`.
   Le nombre de lignes « utilisateur × produit » et la période doivent s'afficher.
3. Lancer `autodesk-sync`, puis ouvrir le rapport **Autodesk · utilisateurs nommés**. Comparer
   5 utilisateurs avec Autodesk Account › *Reporting* › *Usage report* : dernière utilisation et
   nombre de jours d'utilisation.
4. En cas d'erreur, transmettre le message affiché, sans le Client Secret ni les jetons.

| Message | Cause | Action |
|---|---|---|
| `403 … administrateur d'une équipe Business Success Plan` | Compte non administrateur, ou équipe sans l'avantage Reporting API | Se reconnecter avec un administrateur de l'équipe |
| `Plusieurs équipes Autodesk : indiquer autodesk.api.context_id` | Le compte administre plusieurs équipes | Choisir l'équipe dans l'assistant |
| `Connexion Autodesk expirée ou révoquée` | Jeton inutilisé pendant 15 jours, ou accès retiré | `autodesk-login` |
| `Autodesk injoignable` | Proxy ou pare-feu | Ouvrir `developer.api.autodesk.com:443` |
| `aucun filtre de dates accepté` (avertissement) | Syntaxe de date refusée par l'API | Signaler le cas : tout l'historique est alors interrogé (plus lent) |
