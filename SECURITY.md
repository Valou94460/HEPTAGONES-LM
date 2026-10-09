# Politique de sécurité

## Versions maintenues

Seule la dernière version publiée (`AA.N.C`) reçoit les correctifs de sécurité.

| Version | Correctifs |
|---|---|
| Dernière version publiée | ✅ |
| Versions antérieures | ❌ (mettre à jour avec le Setup : « Mettre à jour vers … ») |

## Signaler une vulnérabilité

**N'ouvrez pas d'issue publique.** Utilisez le signalement privé de GitHub :
onglet **Security › Report a vulnerability** du dépôt.

Merci d'indiquer la version concernée, la configuration (IIS / autonome, mode d'authentification),
les étapes pour reproduire et l'impact estimé. Accusé de réception sous 5 jours ouvrés ;
correctif ou contournement publié dès que possible, avec mention du signalement si vous le souhaitez.

## Bonnes pratiques d'exploitation

- Les mots de passe et jetons (SQL, SMTP, ITSM, Autodesk) sont chiffrés par DPAPI dans
  `C:\HeptagonesLM\secrets` ; ne les placez jamais en clair dans `heptagoneslm.yaml`.
- Publiez l'interface web derrière IIS en HTTPS avec authentification Windows intégrée.
- Restreignez l'Administration à un groupe AD dédié (« Groupe AD des administrateurs »).
- Vérifiez la signature et la somme SHA-256 (`SHA256SUMS`) du Setup téléchargé.

## Dépôt

Chaque push et pull request passe un contrôle de secrets (gitleaks, `.gitleaks.toml`).
Les pull requests venant de forks ne reçoivent aucun secret (signature de code comprise).
