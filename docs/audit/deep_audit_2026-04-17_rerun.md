# Audit DEEP - Rerun Apres Correctifs

**Date**: 2026-04-17  
**Scope**: `C:\Users\barqu\trading-platform`  
**Objectif**: rerun complet de l'audit apres correctifs, avec reevaluation du niveau reel de la plateforme pour un **desk perso live**.

---

## Verdict Court

Oui, les correctifs sont reels.

Le projet a monte d'un cran important depuis l'audit de ce matin.

### Score revise

**8.3/10**

### Niveau

**🟠 Fragile controlee**

### Recommandation

**CONTINUE**

Mais avec une nuance importante :

> On n'est plus dans une plateforme "dangereuse par incoherence".  
> On est dans une plateforme "serieuse mais encore inachevee sur quelques points de controle et d'operabilite".

---

## Ce Qui A Vraiment Ete Corrige

## 1. Guards broker-layer

C'etait le principal reproche du dernier audit.

### Binance

Le `pre_order_guard` est maintenant **always on** dans [core/broker/binance_broker.py](C:/Users/barqu/trading-platform/core/broker/binance_broker.py:398).

Avant :
- guard derriere `BINANCE_PRE_ORDER_GUARD=true`

Maintenant :
- guard actif par defaut
- bypass reserve aux callers systeme et aux tests
- import error = **fail-closed**

### IBKR

Le `pre_order_guard` est maintenant **always on** dans [core/broker/ibkr_adapter.py](C:/Users/barqu/trading-platform/core/broker/ibkr_adapter.py:295).

Avant :
- guard derriere `IBKR_PRE_ORDER_GUARD=true`

Maintenant :
- guard actif par defaut
- bypass reserve aux callers systeme et aux tests

### Alpaca

Il existe maintenant un vrai `pre_order_guard` cote Alpaca dans [core/alpaca_client/client.py](C:/Users/barqu/trading-platform/core/alpaca_client/client.py:401).

Avant :
- `_authorized_by + paper mode`

Maintenant :
- `pre_order_guard(book="alpaca_us", strategy_id=_authorized_by, ...)`

**Conclusion**: la critique la plus grave de l'audit precedent est en grande partie **fermee**.

---

## 2. Health branche dans le guard

Le `pre_order_guard` consulte maintenant `book_health` dans [core/governance/pre_order_guard.py](C:/Users/barqu/trading-platform/core/governance/pre_order_guard.py:194).

Regle actuelle :
- `BLOCKED` = refuse
- `UNKNOWN` = fail-closed en live
- `DEGRADED` = warning mais ordre autorise

Avant :
- le health etait surtout informatif

Maintenant :
- il participe au controle pre-order

**Conclusion**: autre critique majeure **fermee partiellement mais serieusement**.

---

## 3. Pollution des state files par les tests

Le bug le plus embarrassant de l'audit precedent etait reel :
- [tests/test_worker_zero_bug.py](C:/Users/barqu/trading-platform/tests/test_worker_zero_bug.py:352) activait un kill switch avec `test_reason_123`
- cela pouvait polluer le vrai state file crypto

Correctifs observes :
- [core/crypto/risk_manager_crypto.py](C:/Users/barqu/trading-platform/core/crypto/risk_manager_crypto.py:210) accepte maintenant un `state_path`
- [tests/conftest.py](C:/Users/barqu/trading-platform/tests/conftest.py:1) redirige automatiquement `CryptoKillSwitch._STATE_PATH` vers `tmp_path`
- [tests/test_worker_zero_bug.py](C:/Users/barqu/trading-platform/tests/test_worker_zero_bug.py:347) instancie desormais `CryptoKillSwitch(state_path=tmp_path / "test_ks.json")`
- le vrai fichier [data/crypto_kill_switch_state.json](C:/Users/barqu/trading-platform/data/crypto_kill_switch_state.json:1) est revenu a un etat inactif

**Conclusion**: la pollution test -> prod sur ce point est **corrigee**.

---

## 4. Preflight plus fail-closed

Le bloc `preflight raised` a ete durci dans [worker.py](C:/Users/barqu/trading-platform/worker.py:5454).

Avant :
- si `run_preflight()` levait une exception, le worker pouvait continuer

Maintenant :
- en live, `PRE-FLIGHT RAISED IN LIVE MODE` -> `sys.exit(3)`
- en paper seulement, le worker peut encore continuer

**Conclusion**: bon correctif, coherent avec l'objectif `desk perso live`.

---

## 5. Revalidation de socle

### Tests

`pytest` rerun pendant cet audit :
- **3593 passed**
- **1 skipped**
- **2380 warnings**

### Registries

Validation :
- `REGISTRIES_OK`

### Health avec `.env` charge

Resultat du rerun :
- `alpaca_us = GREEN`
- `binance_crypto = DEGRADED`
- `ibkr_eu = GREEN`
- `ibkr_fx = GREEN`
- `ibkr_futures = DEGRADED`

Le point important ici :
- on n'a plus de faux `BLOCKED` crypto du a `test_reason_123`
- la lecture du health est devenue plus credible

---

## Ce Qui Reste Vraiment Ouvert

## 1. IBKR garde encore un bypass sur `ImportError`

Dans [core/broker/ibkr_adapter.py](C:/Users/barqu/trading-platform/core/broker/ibkr_adapter.py:330), le bloc :

- `except ImportError: logger.warning("pre_order_guard module not available, bypass")`

reste encore **fail-open**.

Sur Binance et Alpaca, ce cas a ete corrige vers le `fail-closed`.

### Impact

Faible en exploitation normale si le module est present.  
Mais conceptuellement, ce n'est pas `12/10`.

### Correction

Aligner IBKR sur Binance/Alpaca :
- `pre_order_guard unavailable -> BLOCKING order`

---

## 2. `ibkr_futures` est encore degrade sur la sante runtime

Le health rerun avec `.env` charge donne encore :
- `futures_positions_live.json` manquant
- parquets `MES/MGC/MCL` stale
- `ibkr_equity.json` manquant

Voir [core/governance/book_health.py](C:/Users/barqu/trading-platform/core/governance/book_health.py:104).

### Impact

Le guard autorise encore les ordres si le book est `DEGRADED`.

Donc :
- la gouvernance est meilleure
- mais la qualite operatoire du book futures n'est pas encore au niveau attendu

### Correction

- remettre a plat la production des state files futures
- clarifier si `DEGRADED` doit encore permettre les ordres sur futures
- reduire les faux `DEGRADED` si certains fichiers sont seulement optionnels

---

## 3. Le pipeline EU reste legacy

Le chemin EU a ete neutralise intelligemment, mais il reste sale :

- [scripts/live_portfolio_eu.py](C:/Users/barqu/trading-platform/scripts/live_portfolio_eu.py:58) utilise encore `paper_portfolio_eu_state.json`
- [scripts/live_portfolio_eu.py](C:/Users/barqu/trading-platform/scripts/live_portfolio_eu.py:66) garde `_FALLBACK_CAPITAL_EU = 10_000.0`

### Impact

Le risque live est faible tant que :
- `ibkr_eu = paper_only`
- et que le script force `dry_run=True`

Mais ce n'est pas propre.

### Correction

- migration vers `data/state/ibkr_eu/...`
- suppression des fallbacks economiques ambigus
- suppression definitive du naming `paper_*`

---

## 4. Le worker reste un monolithe puissant

[worker.py](C:/Users/barqu/trading-platform/worker.py:1) reste le centre de gravite.

### Impact

Ce n'est plus le meme risque qu'avant, car les guards sont meilleurs.  
Mais ce n'est toujours pas un systeme `12/10` en isolation runtime.

### Correction

- un runtime par book
- un superviseur leger
- timeouts durs par process

---

## 5. Les warnings restent massifs

Le socle test est vert, ce qui est excellent.

Mais `2380 warnings`, dont beaucoup sur `test_live_endpoints`, montrent encore un bruit important.

### Impact

Ce n'est pas un bloquant live immediat.

Mais :
- ca masque les vrais signaux
- ca ralentit les futures migrations Python/FastAPI

### Correction

- campagne de reduction des warnings framework
- budget de warning cible par sprint

---

## Relecture de la These Projet

La these devient plus credible.

### Ce que le projet est maintenant

Un **desk perso live gouverne**, en cours de consolidation, avec :
- plusieurs books pertinents
- une meilleure discipline de promotion live
- une governance qui commence a etre enforcee au bon endroit

### Ce qu'il n'est pas encore

- un systeme "boringly safe"
- un runtime parfaitement isole
- un plan de controle totalement homogene entre books

---

## Audit Strategie - Rerun

Le rerun ne change pas materially le verdict strategie, mais il change le **cadre de confiance**.

### Futures

- `cross_asset_momentum`: toujours **✅ exploitable**
- `gold_oil_rotation`: toujours **✅ exploitable**
- `gold_trend_mgc`: toujours **⚠️ re-promotion prudente**

### Crypto

- `volatility_breakout`: toujours **✅**
- `btc_eth_dual_momentum`: toujours **⚠️ with watch**
- probation crypto: toujours **⚠️ a surveiller**
- `btc_dominance_rotation_v2`: toujours **❌**
- `borrow_rate_carry`: toujours **❌ / a requalifier**

### EU

- toujours **paper_only justifie**

### Alpaca

- toujours **paper_only justifie**, mais la voie de reactivation future est maintenant plus propre grace au guard ajoute

---

## Risque Systemique - Rerun

Le risque systemique a baisse.

### Baisse reelle du risque

Parce que :
- l'ordre doit maintenant passer par plus de points de controle
- les tests ne polluent plus le kill switch crypto
- le preflight live est plus dur

### Risque systemique residuel

Il est maintenant concentre sur :
- `worker.py`
- l'operabilite `ibkr_futures`
- l'heterogeneite des chemins d'etat

---

## Plan D'Amelioration Residuel

## Phase 1 - Fermer les derniers fail-open

1. `IBKR import error -> fail-closed`
2. harmoniser le comportement `ImportError` sur les 3 brokers
3. ajouter des tests explicites sur ce comportement

## Phase 2 - Remettre `ibkr_futures` en etat sain

1. regenerer / standardiser les fichiers d'etat futures
2. remettre les parquets au niveau de fraicheur attendu
3. clarifier la vraie definition de `DEGRADED`

## Phase 3 - Nettoyer le chemin EU

1. retirer `paper_portfolio_eu_state.json`
2. supprimer `_FALLBACK_CAPITAL_EU`
3. converger vers la convention canonique `data/state/ibkr_eu/...`

## Phase 4 - Sortir du mega-worker

1. processes par book
2. supervision mince
3. restart independant

## Phase 5 - Reduire le bruit technique

1. warnings framework
2. checks de freshness plus precis
3. documentation runtime a jour

---

## Test de Realite

### Le projet survivra-t-il en conditions reelles ?

**Oui, plus crediblement qu'au dernier audit.**

### Probabilite de succes revisee

**75-80%** si :
- tu ne rouvres pas trop vite de nouveaux chemins live
- tu termines les derniers durcissements broker/runtime

### Temps avant un probleme majeur

Beaucoup moins lie a une erreur conceptuelle globale qu'avant.

Le prochain vrai incident probable serait plutot :
- un probleme d'operabilite sur `ibkr_futures`
- ou un residu legacy sur EU

---

## Conclusion Finale

Le rerun confirme que les correctifs ne sont pas cosmetiques.

Les findings suivants sont **fermes ou quasi fermes** :
- guard broker-layer opt-in
- absence de guard Alpaca
- pollution de state par les tests
- preflight live trop permissif
- health trop decoratif

Le systeme n'est pas encore `12/10`.

Mais il est sorti de la zone :
- "beaucoup de code, peu de certitude"

et entre dans la zone :
- "beaucoup de code, certitude en hausse, residus bien identifies"

Le prochain objectif rationnel n'est plus de "sauver la plateforme".  
C'est de **finir les 20% restants qui separent un bon desk live d'un desk vraiment fiable**.
