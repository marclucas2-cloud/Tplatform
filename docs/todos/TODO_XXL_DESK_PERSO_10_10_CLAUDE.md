# TODO XXL - Desk Perso 10/10

## Mission

Transformer `C:\Users\barqu\trading-platform` en un **desk perso live robuste, gouvernable, cohérent et opérable**, noté `10/10` selon les critères suivants :

- une seule vérité runtime
- une seule vérité quant
- aucun faux live
- aucun ordre possible hors garde-fous
- books réellement réconciliés
- opérabilité simple sous stress

Le projet n'est **pas** un produit SaaS à vendre.  
L'objectif est un **desk de trading personnel multi-book** qui peut être exploité de façon disciplinée, traçable et stable.

---

## Directive d'exécution pour Claude

Tu dois **dérouler toute cette TODO sans t'arrêter** jusqu'à atteindre la meilleure complétude possible dans le repo courant.

Règles d'exécution :

1. Ne t'arrête pas à l'analyse. Implémente, vérifie, corrige, puis continue.
2. Ne pose pas de question sauf si une décision a un impact destructif ou irréversible.
3. Si tu découvres une incohérence, traite-la ou documente-la immédiatement dans un fichier d'audit de travail.
4. Après chaque lot significatif, exécute les tests pertinents, puis continue.
5. Ne reviens jamais à un état ambigu `paper/live`.
6. Tout ce qui touche au live doit être `fail-closed`.
7. Aucun ordre ne doit pouvoir contourner `whitelist`, `book health`, `kill switch`, `risk`, `capital`.
8. Toute promotion de stratégie doit être traçable jusqu'à une preuve machine-readable.
9. Toute divergence entre config, code, artefacts et docs doit être réduite, pas contournée.
10. Quand une phase est terminée, passe immédiatement à la suivante.

Tu n'as pas le droit de considérer le projet comme `10/10` tant que la section `Definition of Done 10/10` n'est pas satisfaite.

---

## Contexte de départ

Le repo a déjà beaucoup progressé, mais il reste plusieurs faiblesses structurelles :

- `worker.py` reste le runtime dominant et un gros SPOF.
- `binance_crypto` est le seul book clairement exploitable en live.
- `ibkr_futures` n'est pas encore réellement `GREEN`.
- `alpaca_us` reste ambigu entre live affiché et live réellement permis.
- `ibkr_eu` est paper-only mais encore entouré de traces legacy.
- `ibkr_fx` est quant intéressant mais non exploitable opérationnellement aujourd'hui.
- les artefacts quant crypto doivent être revalidés et réconciliés.
- certaines stratégies sont encore promues avec des noms ou des preuves incohérents.

Le but de cette feuille de route n'est pas d'ajouter des features.  
Le but est de **réduire les illusions, fermer les contournements, consolider le vrai live**.

---

## Interdictions pendant le chantier

Ne pas faire ces actions tant que les phases critiques ne sont pas terminées :

- ne pas ajouter de nouvelles stratégies live
- ne pas augmenter le capital alloué
- ne pas réactiver `ibkr_eu` en live
- ne pas réactiver `ibkr_fx` en live
- ne pas promouvoir une stratégie sur la base d'un markdown ou d'un commentaire
- ne pas tolérer `DEGRADED` comme état live acceptable par défaut
- ne pas conserver de double source de vérité

---

## Ordre d'exécution obligatoire

1. `P0` - Sécurisation immédiate
2. `P1` - Vérité runtime et gouvernance
3. `P2` - Vérité quant et promotion de stratégie
4. `P3` - Fiabilisation book par book
5. `P4` - Enforcement santé, garde-fous et réconciliation
6. `P5` - Réduction du SPOF architecture
7. `P6` - Hygiène opérationnelle
8. `P7` - Risque, capital, sizing, portefeuille
9. `P8` - Tests de non-régression live
10. `P9` - Validation finale 10/10

Ne saute aucune phase.

---

## P0 - Sécurisation immédiate

### Objectif

Stopper tout angle mort qui peut laisser croire qu'un book ou une stratégie est live-ready alors que ce n'est pas vrai.

### Tâches

- publier une matrice réelle d'exploitation des books
- geler toute nouvelle promotion live
- rendre impossible tout faux passage live
- interdire tout ordre sur un book `BLOCKED` ou `DEGRADED` tant qu'une policy stricte n'est pas écrite
- vérifier que `BINANCE_LIVE_CONFIRMED` est réellement bloquant
- clarifier noir sur blanc le statut réel de `alpaca_us`
- retirer du live/probation toute stratégie sans preuve machine-readable propre

### Fichiers à auditer et corriger

- `C:\Users\barqu\trading-platform\config\live_whitelist.yaml`
- `C:\Users\barqu\trading-platform\config\books_registry.yaml`
- `C:\Users\barqu\trading-platform\core\governance\pre_order_guard.py`
- `C:\Users\barqu\trading-platform\core\broker\binance_broker.py`
- `C:\Users\barqu\trading-platform\core\alpaca_client\client.py`
- `C:\Users\barqu\trading-platform\worker.py`

### Livrables

- un état d'exploitation officiel des books
- une policy fail-closed minimale déjà active
- une liste des stratégies explicitement retirées ou dégradées

### Critères d'acceptation

- aucun book ambigu entre `paper`, `probation`, `live`
- aucun ordre possible sans validation centrale
- aucun mode `live` possible sans confirmation explicite si confirmation requise

---

## P1 - Vérité runtime et gouvernance

### Objectif

Créer une seule chaîne de vérité entre config, runtime, stratégie et exécution.

### Tâches

- faire de `live_whitelist.yaml` la source de vérité unique du live
- imposer une relation bijective entre :
  - `strategy_id`
  - `runtime module`
  - `artefact WF`
  - `nom de reporting`
  - `book`
- supprimer tous les alias non maîtrisés
- faire échouer le boot si une stratégie whitelistée n'est pas parfaitement résolue
- supprimer les configs décoratives non alignées avec la vérité runtime
- faire matcher docs, headers, logs et comportement réel

### Fichiers à auditer et corriger

- `C:\Users\barqu\trading-platform\config\live_whitelist.yaml`
- `C:\Users\barqu\trading-platform\config\strategies_eu.yaml`
- `C:\Users\barqu\trading-platform\strategies\crypto\__init__.py`
- `C:\Users\barqu\trading-platform\worker.py`
- tous les registries et loaders de stratégie

### Sous-tâches détaillées

- recenser tous les `strategy_id` réellement utilisés à l'exécution
- recenser tous les modules réellement importés
- recenser tous les artefacts quant de référence
- produire une table de correspondance canonique
- supprimer tout mapping implicite ou heuristique
- ajouter une vérification au démarrage qui échoue si :
  - un module n'existe pas
  - un artefact manque
  - un nom diffère
  - un book est faux

### Critères d'acceptation

- une stratégie live possède exactement un identifiant canonique
- plus aucun drift entre whitelist et module réel
- les books paper-only n'exécutent aucune logique live cachée

---

## P2 - Vérité quant et promotion de stratégie

### Objectif

Faire en sorte que toute stratégie promue ait une preuve quant propre, traçable et exploitable.

### Tâches

- régénérer les artefacts quant machine-readable critiques
- enquêter sur les duplications de résultats
- standardiser le dossier de promotion d'une stratégie
- retirer toute stratégie promue sur storytelling ou sur preuve partielle
- interdire les verdicts `VALIDATED` non traçables jusqu'à un générateur reproductible

### Artefacts à revalider en priorité

- `C:\Users\barqu\trading-platform\data\crypto\wf_results.json`
- `C:\Users\barqu\trading-platform\data\fx\wf_results.json`
- `C:\Users\barqu\trading-platform\data\fx\wf_structural_results.json`
- `C:\Users\barqu\trading-platform\output\wf_eu_results\wf_eu_summary.json`
- `C:\Users\barqu\trading-platform\output\wf_futures_results\wf_futures_summary.json`
- `C:\Users\barqu\trading-platform\reports\research\ib_portfolio_wf_v2.csv`

### Standard de dossier de promotion par stratégie

Chaque stratégie doit avoir un dossier promotionnel contenant :

- nom canonique
- book
- univers tradé
- horizon
- fréquence de trading
- hypothèse économique
- hypothèse de microstructure
- dépendance au régime de marché
- source de données
- période in-sample
- période out-of-sample
- nombre de trades
- impact frais et slippage
- liquidité réelle
- exposition factorielle
- corrélation au portefeuille existant
- apport marginal portefeuille
- risque de breakdown
- raison du statut final

### Critères d'acceptation

- aucune stratégie live sans artefact quant unique
- aucune stratégie promue avec preuve dupliquée ou douteuse
- les stratégies EU rejetées sortent du périmètre live
- les stratégies crypto live/probation sont toutes justifiées proprement

---

## P3 - Fiabilisation book par book

### Objectif

Sortir du discours général et durcir chaque book séparément selon sa réalité opérationnelle.

### Book 1 - Binance

#### Objectif

Faire de `binance_crypto` le premier book live propre du repo.

#### Tâches

- garder uniquement les stratégies avec preuve propre et valeur portefeuille réelle
- vérifier que chaque ordre passe par `pre_order_guard`
- vérifier que le `kill switch` est bloquant
- vérifier que le capital et l'equity utilisés sont réels
- vérifier que chaque stratégie a un nom canonique et un artefact canonique
- sortir du live toute stratégie `probation` non justifiée

#### Critères d'acceptation

- `binance_crypto` peut être considéré `live-ready` sans ambiguïté
- aucune stratégie cassée ou mal mappée n'est encore active

### Book 2 - IBKR Futures

#### Objectif

Faire passer `ibkr_futures` de `BLOCKED` à `GREEN` pour de bonnes raisons, pas en contournant les checks.

#### Tâches

- diagnostiquer précisément pourquoi `ibkr_account` échoue
- distinguer `timeout`, `empty summary`, `permission issue`, `gateway mismatch`, `account unavailable`
- rétablir les états canoniques manquants
- rétablir la fraîcheur des données futures
- réconcilier les positions et l'equity
- vérifier la cohérence des stratégies promues futures

#### Critères d'acceptation

- `ibkr_futures` passe `GREEN` sans bypass
- les raisons de `BLOCKED` sont explicites et actionnables si elles reviennent

### Book 3 - Alpaca

#### Objectif

Mettre fin à l'ambiguïté.

#### Tâches

- choisir entre :
  - `paper-only assumé`
  - `live réellement supporté`
- si `paper-only`, nettoyer toute trace laissant croire au live
- si `live`, implémenter une gouvernance aussi stricte que Binance et IBKR
- ajouter réconciliation et health spécifiques à Alpaca

#### Critères d'acceptation

- il n'existe plus de faux `3e book live`
- le statut réel d'Alpaca est visible et cohérent partout

### Book 4 - IBKR EU

#### Objectif

Sortir définitivement le legacy et cesser les ambiguïtés.

#### Tâches

- conserver `paper-only`
- retirer toutes les formulations “live” trompeuses
- supprimer toute promotion de stratégies EU rejetées OOS
- rendre le pipeline EU proprement paper

#### Critères d'acceptation

- EU n'est plus une source de confusion opérationnelle

### Book 5 - IBKR FX

#### Objectif

Conserver la valeur recherche sans la confondre avec la prod.

#### Tâches

- garder `disabled`
- nettoyer les configs legacy rejetées
- conserver les artefacts quant robustes pour plus tard
- documenter explicitement pourquoi FX n'est pas live

#### Critères d'acceptation

- aucune ambiguïté entre potentiel quant et utilisabilité opérationnelle

---

## P4 - Enforcement santé, garde-fous et réconciliation

### Objectif

Faire en sorte que la santé d'un book soit un mécanisme d'enforcement réel, pas juste un dashboard.

### Tâches

- écrire une matrice de policy par cause de dégradation
- faire appliquer cette matrice dans le chemin d'ordre
- distinguer :
  - `ALLOW_FULL`
  - `ALLOW_REDUCE_ONLY`
  - `ALLOW_CLOSE_ONLY`
  - `BLOCK`
- brancher les `kill switches` partout
- étendre la réconciliation à tous les books
- imposer une réconciliation réussie avant reprise après incident

### Fichiers à auditer et corriger

- `C:\Users\barqu\trading-platform\core\governance\book_health.py`
- `C:\Users\barqu\trading-platform\core\governance\pre_order_guard.py`
- `C:\Users\barqu\trading-platform\core\governance\reconciliation.py`
- tous les adapters broker

### Critères d'acceptation

- un book dégradé ne peut pas ouvrir de nouvelles positions par accident
- les books peuvent au minimum réduire ou fermer selon policy explicite
- la reprise après incident est gouvernée, pas improvisée

---

## P5 - Réduction du SPOF architecture

### Objectif

Réduire le risque systémique porté par `worker.py`.

### Tâches

- inventorier toutes les responsabilités de `worker.py`
- sortir les responsabilités par domaine
- séparer le runtime en processus ou services simples par book
- rendre chaque book supervisable indépendamment
- empêcher qu'un incident IBKR ou EU n'affecte le cycle Binance
- faire en sorte que `TradingEngine` ou une architecture équivalente porte réellement la prod si pertinent

### Sous-tâches

- isoler orchestration
- isoler dispatch broker
- isoler health
- isoler recovery
- isoler logging/audit trail
- isoler reporting

### Critères d'acceptation

- un crash sur un book ne stoppe pas les autres
- l'orchestration devient lisible et testable
- `worker.py` cesse d'être le centre unique de gravité

---

## P6 - Hygiène opérationnelle

### Objectif

Faire d'un repo complexe un système exploitable simplement.

### Tâches

- nettoyer le worktree
- séparer strictement :
  - `paper`
  - `live`
  - `research`
  - `state`
  - `reports`
  - `tests`
- supprimer les noms trompeurs
- standardiser les logs
- créer un runbook d'exploitation minimal mais complet

### Runbook attendu

Le runbook doit couvrir :

- démarrage normal
- démarrage dégradé
- arrêt d'urgence
- reprise après incident
- divergence positions broker/local
- perte de data
- broker unavailable
- kill switch
- retrait d'une stratégie live

### Critères d'acceptation

- un opérateur peut comprendre l'état réel du système en quelques minutes
- les chemins `paper/live` sont immédiatement distinguables

---

## P7 - Risque, capital, sizing, portefeuille

### Objectif

Faire en sorte que le portefeuille soit gouverné par des réalités de capital, de corrélation et de capacité.

### Tâches

- recalculer toutes les politiques de sizing à partir de capital réel
- expliciter budget de risque par book
- expliciter budget de risque par stratégie
- mesurer corrélation marginale et apport portefeuille
- supprimer les stratégies qui augmentent juste le turnover ou consomment du capital sans ajouter d'alpha marginal
- formaliser un mini comité de promotion

### Comité de promotion minimal

Une stratégie ne passe live que si elle passe :

- `quant proof`
- `risk proof`
- `ops proof`
- `runtime proof`

### Critères d'acceptation

- aucune stratégie n'est live sans justification portefeuille
- le capital alloué par book est cohérent avec son niveau de maturité

---

## P8 - Tests de non-régression live

### Objectif

Faire en sorte que les tests verts veulent enfin dire quelque chose pour la prod.

### Tâches

- ajouter des tests de chaîne complète `whitelist -> guard -> broker adapter`
- ajouter des tests sur les books `BLOCKED`, `DEGRADED`, `GREEN`
- ajouter des tests de réconciliation
- ajouter des tests de stratégie id canonique
- ajouter des tests de duplication d'artefacts quant
- ajouter des tests de reprise après incident
- faire échouer la CI si un artefact quant est incohérent

### Critères d'acceptation

- les tests protègent les chemins critiques du live
- une régression de gouvernance devient visible immédiatement

---

## P9 - Validation finale 10/10

### Definition of Done 10/10

Le repo n'est considéré `10/10` que si **tous** les points suivants sont vrais :

- il existe une seule vérité live canonique
- il existe une seule vérité quant canonique
- aucun book n'est ambigu
- aucun ordre ne peut contourner les garde-fous
- `DEGRADED` ne permet pas de nouvelles prises de position sans policy explicite
- chaque stratégie live a une preuve propre, traçable et unique
- chaque book dispose d'un health exploitable et d'une réconciliation
- le repo est lisible, opérable et propre
- les books sont isolés suffisamment pour qu'un incident local ne devienne pas systémique
- les tests couvrent les chemins live critiques
- le runbook permet une exploitation simple sous stress

### Validation finale à produire

Claude doit produire un rapport final contenant :

- score global final
- score par axe :
  - architecture
  - gouvernance
  - quant
  - risque
  - ops
- books réellement live-ready
- stratégies réellement live-ready
- books volontairement non-live
- liste des illusions supprimées
- liste des risques résiduels
- ce qui manque encore pour aller au-delà de `10/10`

---

## Tableau de priorités absolues

### Priorité absolue A

- fail-closed live
- vérité runtime unique
- vérité quant unique
- book health réellement enforce
- suppression des faux live

### Priorité absolue B

- réconciliation par book
- clarification de la promotion des stratégies
- remise à plat de `ibkr_futures`
- clarification du statut Alpaca

### Priorité absolue C

- réduction du monolithe
- nettoyage du repo
- runbook
- ergonomie opérateur

---

## Liste des résultats attendus à la fin

Claude doit idéalement laisser derrière lui :

- des books au statut réel cohérent
- une whitelist propre et canonique
- des stratégies promues pour de bonnes raisons
- des artefacts quant fiables
- des garde-fous réellement bloquants
- une réconciliation live utilisable
- une architecture moins fragile
- une documentation d'exploitation claire
- un rapport final net et sans storytelling

---

## Format de reporting obligatoire pendant l'exécution

Après chaque lot terminé, produire un mini rapport :

- ce qui a été changé
- ce qui a été validé
- ce qui reste risqué
- quels tests ont été exécutés
- si la phase peut être clôturée

Si une phase ne peut pas être terminée, Claude doit :

- lister le blocage exact
- documenter l'impact
- proposer la voie de contournement la moins risquée
- passer au lot suivant si cela ne compromet pas la sécurité du live

---

## Prompt court à utiliser si besoin

Tu es chargé d'exécuter intégralement `C:\Users\barqu\trading-platform\Todo\TODO_XXL_DESK_PERSO_10_10_CLAUDE.md`.

Tu dois :

- dérouler toute la TODO sans t'arrêter
- implémenter les corrections
- tester après chaque lot
- documenter les écarts et les blocages
- garder une logique fail-closed
- traiter ce repo comme un desk perso live, pas comme un produit à vendre

Ton but n'est pas d'ajouter des features.  
Ton but est de faire disparaître les illusions, les contournements, les faux live et les incohérences de gouvernance, jusqu'à atteindre un vrai niveau `10/10`.

