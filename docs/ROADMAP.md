# Feuille de route HeptagonesLM

Objectif : couvrir tout ce que propose OpenLM (plateforme de gestion des licences
logicielles), en gardant nos atouts : logiciel libre, installé chez le client, avec un
accompagnement (TMA, support, SLA, dossiers d'exploitation).

Base : comparatif fonctionnel OpenLM / HeptagonesLM du 29/09/2026, chaque ligne étant
vérifiée dans le code de la version 26.2.0. Les dates sont indicatives, à confirmer
selon les priorités des premiers clients.

## 1. Où en est-on (état réel, version 26.2.0)

| Fonction | OpenLM | HeptagonesLM aujourd'hui | Lot |
|---|:---:|---|:---:|
| Monitoring temps réel, par produit / feature, utilisateur / poste | Oui | ✅ Vue « En direct », actualisée chaque minute | — |
| Historique checkout / check-in, durée des sessions | Oui | ✅ Sessions (lmstat à la minute, log debug à la seconde) | — |
| Refus (denials) | Oui | ✅ FlexLM (log debug) · ❌ RLM (report log non lu) | 26.5 |
| Détection de saturation | Oui | ✅ Contention, heures saturées, alerte « plus aucun jeton » | — |
| Licences sous-utilisées | Oui | ✅ Alerte « sous-utilisé », efficacité des jetons, recommandation | — |
| Licences **inutilisées** (aucun usage sur la période) | Oui | 🟡 Visibles dans la synthèse, pas de vue dédiée | 26.3 |
| Alertes | Oui | ✅ Tableau de bord + e-mail immédiat (collecte, refus, saturation, échéances) | — |
| Reporting, tableaux de bord, exports | Oui | ✅ 13 vues, Excel / CSV / JSON, rapport mensuel par e-mail | — |
| Heatmaps | Oui | ✅ Profil horaire (jour × heure) | — |
| Analytics avancées : **prévision** | Oui | ❌ | 26.4 |
| Optimisation des achats / renouvellements | Oui | 🟡 Jetons recommandés, coûts, économies · pas de rapport de renouvellement | 26.4 |
| Chargeback (coût par service) | Oui | ✅ Refacturation par service · ❌ par projet | 26.4 |
| **Idle session monitoring** (inactivité réelle sur le poste) | Oui | 🟡 Seulement les fermetures « INACTIVE » du démon FlexLM | 27.1 |
| **License harvesting** automatique | Oui | ❌ (nécessite un agent sur les postes) | 27.1 |
| **Allocation** de licences (quotas par groupe) | Oui | ❌ | 27.1 |
| **Named user** / réservations | Oui | ❌ (réservations lmstat ignorées) | 26.3 |
| Token monitoring | Oui | ✅ FlexLM et RLM | — |
| **Licences empruntées** (borrow) | Oui | ❌ (non distinguées dans lmstat) | 26.3 |
| **Dongles** (clés USB de licence) | Oui | ❌ | 26.5 |
| Gestionnaires de licences | 70+ | 🟡 FlexLM + RLM · ❌ DSLS, Sentinel RMS/HASP, LM-X, LUM… | 26.5 |
| Autodesk, Ansys, MATLAB (FlexLM) | Oui | ✅ | — |
| **ServiceNow, Jira, GLPI, tickets ITSM automatiques** | Oui / selon intégration | ❌ **Pas d'intégration dans le logiciel** (un e-mail d'alerte peut être relevé par un outil ITSM) | 26.3 |
| **Rôles fins par service**, administration en ligne | Oui | ❌ Un seul groupe AD, tout le monde voit tout | 26.3 |
| On-premise | Oui | ✅ | — |
| Multi-serveurs | Oui | ✅ (triades comprises) | — |
| **Multi-sites** | Oui | 🟡 Champ « site » en base, non alimenté ni filtrable | 26.3 |
| Cloud / SaaS | Oui | ❌ | 27.2 |
| SAM (inventaire logiciel au-delà des serveurs de licences) | Oui | ❌ | 27.2 |
| Documentation d'exploitation (DEX), plan de test | Non | ✅ DEX 66 pages, plan de test, guide d'installation | — |
| TMA, support, SLA, résolution d'incidents, astreinte | Selon offre | Offre de service Heptagones (hors logiciel) | — |

> **À corriger dans le comparatif avant diffusion** : les lignes ServiceNow, Jira, GLPI et
> « création automatique de tickets ITSM » indiquent « Oui » pour HeptagonesLM. Le logiciel
> n'a pas encore ces intégrations : écrire « Lot 26.3 (T4 2026) » ou « Via e-mail d'alerte,
> connecteur natif en 26.3 ». Les lignes « À confirmer » peuvent passer à « Oui » pour les
> licences sous-utilisées, les heatmaps et le chargeback par service (voir tableau).

## 2. Lots de livraison

### 26.2.0 — Alertes et RLM (prête, en revue : PR #7)
- Alertes immédiates par e-mail (collecte, refus, saturation, échéances).
- Collecteur RLM (Foundry, Maxon, Chaos…).

### 26.3.0 — ITSM et gouvernance (cible : fin octobre 2026, ~10 j)

| Fonction | Contenu | Effort |
|---|---|:---:|
| Tickets ITSM automatiques | Chaque alerte ouvre (puis met à jour et résout) un ticket : connecteurs **ServiceNow** (Table API, incident), **Jira** (REST, issue), **GLPI** (API REST, ticket) et **webhook** générique (Teams, Slack, autres outils). Anti-doublon : un ticket par alerte active, commentaire au rappel, clôture au rétablissement. | 4 j |
| Rôles par service | Rôles *administrateur*, *lecteur global*, *responsable de service* (voit uniquement ses services) attribués par groupes AD ; filtres forcés côté serveur, exports compris. | 3 j |
| Licences empruntées | Checkouts « borrow / linger » FlexLM distingués (vue En direct, sessions, rapport). | 1 j |
| Réservations / named user | Réservations `RESERVE` lues dans lmstat : jetons réservés par utilisateur ou groupe, taux d'usage des réservations. | 1 j |
| Multi-sites | Site par poste (règles sur le nom ou import CSV) et par serveur ; filtre et répartition par site. | 1 j |
| Licences inutilisées | Vue dédiée : features sans aucun usage sur la période, coût associé. | ½ j |

### 26.4.0 — Analytics et achats (cible : fin novembre 2026, ~8 j)

| Fonction | Contenu | Effort |
|---|---|:---:|
| Prévision | Projection 3 / 6 / 12 mois du pic et des jetons nécessaires par produit (tendance + saisonnalité hebdomadaire et mensuelle), intervalle de confiance. | 3 j |
| Rapport de renouvellement | Pour chaque échéance à venir : usage réel, jetons recommandés, économie ou besoin chiffré — prêt pour les achats. | 2 j |
| Chargeback par projet | Projet déduit du groupe AD, du poste ou d'un import CSV ; refacturation par projet. | 2 j |
| Rapports planifiés sur mesure | Choix des vues et filtres par abonnement e-mail. | 1 j |

### 26.5.0 — Couverture des gestionnaires (cible : fin décembre 2026, ~10 j)

Chaque collecteur est validé sur une sortie réelle fournie par un client (test de non-régression).

| Gestionnaire | Éditeurs typiques | Effort |
|---|---|:---:|
| DSLS (Dassault) | CATIA, SOLIDWORKS, SIMULIA | 3 j |
| Sentinel RMS / Sentinel LDK (HASP) + **dongles** | Nombreux éditeurs CAO/FAO, clés USB | 3 j |
| LM-X (X-Formation) | Altair (partiel), ESI… | 2 j |
| Refus RLM (report log) | Foundry, Maxon… | 1 j |
| IBM LUM / autres à la demande | — | 1 j chacun |

### 27.1.0 — Optimisation active (cible : T1 2027, ~15 j)

| Fonction | Contenu | Effort |
|---|---|:---:|
| Agent poste (service Windows léger) | Déploiement GPO / SCCM ; remonte l'activité réelle des applications sous licence (clavier / souris, premier plan). | 6 j |
| Idle session monitoring | Temps actif / inactif par session, par utilisateur et par produit. | 2 j |
| License harvesting | Règles par produit (ex. inactif 45 min) : avertissement à l'utilisateur, puis libération de la licence (fermeture propre ou `lmremove`), journal des récupérations. | 4 j |
| Allocation | Quotas par groupe AD générés dans le fichier options FlexLM (`MAX`, `RESERVE`, `INCLUDE`), avec suivi du respect des quotas. | 3 j |

### 27.2.0 — Cloud et SAM (cible : T2 2027, à cadrer)
- Offre hébergée (instance par client, collecteur local relayant vers le cloud).
- Licences SaaS / nominatives des éditeurs (portails Autodesk, etc.) selon les API disponibles.
- Inventaire logiciel des postes (via l'agent 27.1) : premier niveau de SAM.

## 3. Hors logiciel : l'offre de service

TMA applicative, support des serveurs de licences, SLA de prise en charge, résolution
d'incidents et astreinte relèvent de l'offre Heptagones et sont décrits dans les
propositions commerciales, pas dans cette feuille de route.

## 4. Suivi

Chaque lot fait l'objet d'une PR, de tests automatisés, d'une mise à jour du plan de test,
du DEX et du CHANGELOG, puis d'une version publiée (numérotation `AA.N.C`).
