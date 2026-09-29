# Interface web partagée : connexion Windows / AD et HTTPS

Par défaut, l'interface (`heptagoneslm web`, `HeptagonesLM.exe`) n'est accessible que depuis le
poste où elle tourne, sans authentification. Pour la partager sur le réseau, il faut
authentifier les utilisateurs avec leur compte Windows, et chiffrer les échanges.

| Mode (`web.auth.mode`) | Expérience utilisateur | Mise en œuvre |
|---|---|---|
| `none` | Aucune connexion | Poste local uniquement (défaut) |
| **`ldap`** (recommandé) | Page de connexion : identifiant et mot de passe Windows | HeptagonesLM seul + certificat HTTPS |
| `header` | **Aucune saisie** (SSO, session Windows) | IIS en frontal avec l'authentification Windows |

<img src="images/connexion.png" alt="Page de connexion" width="560">

## 1. Mode `ldap` : identifiants Windows vérifiés par l'Active Directory

### Prérequis

- **LDAPS** actif sur les contrôleurs de domaine (port 636 ; certificat délivré par
  l'AC d'entreprise — c'est le cas dès qu'une AD CS est déployée). À défaut :
  `ldap://` + `ldap_starttls: true` (port 389).
- Flux ouvert du serveur HeptagonesLM vers les contrôleurs de domaine (636 ou 389).
- (recommandé) un groupe AD des personnes autorisées, ex. `GG_HeptagonesLM_Lecteurs`.
- Un certificat HTTPS pour le serveur HeptagonesLM (§ 3) : **obligatoire** dès que
  l'interface écoute sur le réseau, sans quoi HeptagonesLM refuse de démarrer (les mots de
  passe circuleraient en clair).

### Configuration

```yaml
web:
  host: 0.0.0.0
  port: 8765
  tls_cert: "C:/HeptagonesLM/certs/heptagoneslm.pem"
  tls_key:  "C:/HeptagonesLM/certs/heptagoneslm.key"
  public_url: "https://heptagoneslm.entreprise.local:8765"
  auth:
    mode: ldap
    ldap_servers: ["ldaps://dc01.entreprise.local", "ldaps://dc02.entreprise.local"]
    base_dn: "DC=entreprise,DC=local"
    upn_suffix: "entreprise.local"          # connexion en identifiant@entreprise.local
    allowed_groups: ["GG_HeptagonesLM_Lecteurs"]  # groupes imbriqués acceptés ; vide = tout le domaine
    session_hours: 8
```

Variante sans `upn_suffix` (annuaire non AD, ou comptes hors du suffixe) : un compte de
service en lecture recherche l'utilisateur, puis HeptagonesLM vérifie son mot de passe.

```yaml
    bind_dn: "CN=svc_heptagoneslm_ldap,OU=Services,DC=entreprise,DC=local"
    bind_password: ${HEPTAGONESLM_LDAP_PASSWORD}
    user_filter: "(sAMAccountName={username})"
```

L'utilisateur peut saisir `jdupont`, `ENTREPRISE\jdupont` ou `jdupont@entreprise.local`.

### Comportement et sécurité

- Mot de passe transmis uniquement au contrôleur de domaine (bind LDAP chiffré), jamais
  stocké ni journalisé ; mot de passe vide refusé (pas de bind anonyme).
- Session : cookie aléatoire `HttpOnly`, `SameSite=Strict`, `Secure`, valable
  `session_hours` ; bouton « Se déconnecter ». Un redémarrage du service déconnecte tout le monde.
- Après `max_failures` échecs (5 par défaut) pour un même identifiant ou une même
  adresse IP : blocage de `lockout_minutes` (15 min). À régler en dessous du seuil de
  verrouillage des comptes AD.
- **Journal d'audit** (`heptagoneslm.log`, logger `heptagoneslm.audit`) : connexions, échecs,
  blocages, déconnexions et **chaque export** (utilisateur, rapport, format, filtres).

### Dépannage

| Symptôme | Cause probable | Solution |
|---|---|---|
| « Annuaire indisponible » + `certificate verify failed` dans le journal | AC interne inconnue de Python | Exporter le certificat racine de l'AC (Base-64) et le déclarer : `ldap_ca_file` |
| « Annuaire indisponible » + délai dépassé | Flux 636 fermé | Ouvrir le pare-feu vers les DC, ou `ldap://` + `ldap_starttls` |
| « Identifiant ou mot de passe incorrect » alors qu'il est bon | Mauvais `upn_suffix` (UPN différent du domaine) | Utiliser la variante `bind_dn` |
| « Accès refusé : … aucun groupe autorisé » | Utilisateur hors de `allowed_groups` | Ajouter au groupe (reconnexion après réplication AD) |

## 2. Mode `header` : authentification unique avec IIS

IIS authentifie l'utilisateur de manière transparente (Kerberos/NTLM) et transmet son
nom à HeptagonesLM, qui n'écoute que sur la boucle locale.

> **Le plus simple : `HeptagonesLM-Setup.exe`** réalise toutes ces étapes et vérifie le
> résultat (voir [INSTALLATION.md](INSTALLATION.md)). Procédure manuelle ci-dessous.

1. Installer IIS avec **Authentification Windows**, **Autorisation d'URL** et
   **ASP.NET 4.x**, puis les modules
   [URL Rewrite](https://www.iis.net/downloads/microsoft/url-rewrite) et
   [Application Request Routing](https://www.iis.net/downloads/microsoft/application-request-routing)
   (ARR › *Server Proxy Settings* › *Enable proxy*).
2. Créer un site HTTPS (certificat du § 3, lié dans IIS), pool **.NET CLR v4.0,
   pipeline intégré** ; *Authentification* : **Windows activé, Anonyme désactivé**.
3. Déposer le module d'identité `App_Code\HeptagonesLMUserHeader.cs` dans le dossier du site.
   Il pose l'en-tête `X-Remote-User` **après** l'authentification et supprime toute
   valeur envoyée par le navigateur.

   > Ne pas utiliser de variable serveur `{LOGON_USER}` dans la règle URL Rewrite.
   > Les règles s'exécutent *avant* l'authentification Windows : la valeur serait
   > toujours vide et HeptagonesLM répondrait « Authentification requise ».

   ```csharp
   using System.Web;

   public class HeptagonesLMUserHeader : IHttpModule
   {
       const string Header = "X-Remote-User";

       public void Init(HttpApplication app)
       {
           app.BeginRequest += (s, e) => ((HttpApplication)s).Context.Request.Headers.Remove(Header);
           app.PostAuthenticateRequest += (s, e) =>
           {
               HttpContext ctx = ((HttpApplication)s).Context;
               ctx.Request.Headers.Remove(Header);
               if (ctx.User != null && ctx.User.Identity != null && ctx.User.Identity.IsAuthenticated)
                   ctx.Request.Headers.Set(Header, ctx.User.Identity.Name);
           };
       }

       public void Dispose() { }
   }
   ```

   Le module généré par `HeptagonesLM-Setup.exe` transmet en plus la méthode d'authentification
   (`Kerberos` ou `NTLM`) dans l'en-tête `X-Remote-Auth-Method`, affichée par la page de test
   `/acces` : préférer le fichier généré (`C:\HeptagonesLM\iis\App_Code\HeptagonesLMUserHeader.cs`).

4. `web.config` du site :

   ```xml
   <configuration>
     <system.webServer>
       <security>
         <authorization>
           <clear />
           <add accessType="Allow" roles="ENTREPRISE\GG_HeptagonesLM_Lecteurs" />
         </authorization>
       </security>
       <modules>
         <remove name="HeptagonesLMUserHeader" />
         <add name="HeptagonesLMUserHeader" type="HeptagonesLMUserHeader" />
       </modules>
       <rewrite>
         <rules>
           <rule name="HeptagonesLM" stopProcessing="true">
             <match url="(.*)" />
             <action type="Rewrite" url="http://127.0.0.1:8765/{R:1}" />
           </rule>
         </rules>
       </rewrite>
     </system.webServer>
   </configuration>
   ```

5. HeptagonesLM :

   ```yaml
   web:
     host: 127.0.0.1            # jamais exposé directement
     port: 8765
     public_url: "https://heptagoneslm.entreprise.local"
     auth:
       mode: header
       header_name: X-Remote-User
       trusted_proxies: ["127.0.0.1", "::1"]
       allowed_users: []        # vide = tout utilisateur authentifié par IIS
   ```

   La section `<authorization>` du `web.config` réserve l'accès au groupe
   `GG_HeptagonesLM_Lecteurs` (supprimer la section pour tout utilisateur authentifié) ;
   la liste `allowed_users` de HeptagonesLM permet un filtrage supplémentaire.

### Connexion sans saisie : SPN, zone Intranet, test depuis un poste

- **SPN** : `setspn -S HTTP/heptagoneslm.entreprise.local NOMSERVEUR` si le nom public n'est
  pas celui du serveur (sinon repli NTLM ; SPN sur un autre compte : Kerberos échoue).
- **Zone Intranet local** (GPO « Liste des attributions de sites aux zones ») :
  `https://heptagoneslm.entreprise.local` = `1`, sinon les navigateurs demandent les identifiants.
- **Test** : ouvrir `https://heptagoneslm.entreprise.local/acces` depuis un poste utilisateur :
  nom reconnu, méthode (Kerberos / NTLM) et saisie ou non d'un mot de passe, résultat repris
  par la vérification de l'assistant (voir [INSTALLATION.md § 8](INSTALLATION.md)).

## 2 bis. Rôles : qui voit quoi

Sans section `web.roles`, tout utilisateur autorisé (groupe d'accès) voit tout. Avec elle :

| Rôle | Attribution | Vue |
|---|---|---|
| Administrateur | `admins` | Globale (fonctions d'administration à venir) |
| Lecteur | `readers` | Globale |
| Responsable de service | `departments` | Uniquement les utilisateurs de ses services — sessions, répartition, refus, utilisateurs connectés, exports ; en option, certains serveurs ou produits |
| Aucun rôle | — | Refusé (`default: none`), ou lecteur (`default: reader`) |

```yaml
web:
  roles:
    admins: ["ENTREPRISE\\GG_HeptagonesLM_Admins"]
    readers: ["ENTREPRISE\\GG_HeptagonesLM_Lecteurs", "direction.achats"]   # groupes ou comptes
    departments:
      - group: "ENTREPRISE\\GG_Resp_Simulation"
        departments: ["Simulation & calcul"]            # valeurs de la colonne « Service » (import-users)
      - user: chef.methodes
        departments: ["Méthodes industrialisation"]
        servers: [AUTODESK]                              # optionnel : limite aussi les serveurs (ou products)
    default: none
```

* **Groupes** : noms `DOMAINE\groupe`, simples ou DN, sans tenir compte de la casse. En mode `ldap`,
  groupes lus dans l'annuaire (groupes imbriqués compris) ; en mode `header`, le module IIS
  `HeptagonesLMUserHeader` transmet les groupes Windows de l'utilisateur (en-tête
  `X-Remote-Groups`, toute valeur envoyée par le navigateur est supprimée).
* **Contrôle par le serveur** : le périmètre est imposé à chaque requête (rapports et exports) ; une
  demande hors périmètre est refusée (403) et tracée dans le journal d'audit. Le classeur complet est
  réservé aux profils à vue globale.
* **Compteurs de produit** (jetons émis / utilisés, saturation, profil horaire) : globaux pour tous,
  ils ne désignent personne.
* Les services sont ceux importés par `import-users` (export AD / RH) : un utilisateur sans service
  n'est visible que des profils à vue globale.

## 3. Certificat HTTPS

Demander un certificat serveur à l'AC d'entreprise pour le nom utilisé par les
navigateurs (ex. `heptagoneslm.entreprise.local`), l'exporter en `.pfx` avec la clé privée,
puis le convertir en PEM :

```powershell
openssl pkcs12 -in heptagoneslm.pfx -clcerts -nokeys -out heptagoneslm.pem
openssl pkcs12 -in heptagoneslm.pfx -nocerts -nodes -out heptagoneslm.key
```

(`openssl` est fourni avec Git pour Windows : `C:\Program Files\Git\usr\bin\openssl.exe`.)
Restreindre les droits NTFS du fichier `.key` au compte de service HeptagonesLM et aux
administrateurs. Pour un simple essai : certificat auto-signé
`openssl req -x509 -newkey rsa:2048 -nodes -days 365 -subj "/CN=heptagoneslm.entreprise.local" -keyout heptagoneslm.key -out heptagoneslm.pem`
(le navigateur affichera un avertissement).

## 4. Noms d'hôte autorisés

Pour contrer le « DNS rebinding », HeptagonesLM n'accepte que les requêtes adressées à un nom
connu : `localhost`, `127.0.0.1`, `::1`, le nom de `public_url` et, en écoute réseau
(`host: 0.0.0.0`), le nom court, le nom complet et les adresses IP du serveur. Les autres
reçoivent une erreur 421. Ajouter les alias DNS ou adresses supplémentaires :

```yaml
web:
  allowed_hosts: ["heptagoneslm", "licences.entreprise.local"]
```

## 5. Interface permanente

```powershell
scripts\register_tasks.ps1 -HeptagonesLMHome C:\HeptagonesLM -ServiceAccount "DOMAINE\gmsa_heptagoneslm$" -WithWebInterface
```

Ajoute la tâche « HeptagonesLM - Interface web », démarrée avec le serveur et relancée
automatiquement en cas d'arrêt ; adresse, HTTPS et authentification sont lus dans la
section `web` du YAML. Ouvrir le port choisi dans le pare-feu Windows du serveur.
Le compte de service est obligatoire et ne peut pas être SYSTEM pour l'interface web
(moindre privilège : voir [SECURITE.md](SECURITE.md)).
