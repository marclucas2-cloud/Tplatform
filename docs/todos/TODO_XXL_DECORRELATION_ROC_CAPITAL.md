# TODO XXL - Recherche de strategies decorrelantes, ROC et capital efficiency

## 0. Mission

Tu es un agent Claude charge de piloter une campagne de recherche portefeuille de niveau senior.

Ton objectif n'est PAS de trouver "des strategies qui gagnent en standalone".
Ton objectif est de trouver, backtester, classer, integrer et prioriser des strategies qui ameliorent MARGINALEMENT le portefeuille global sur 4 axes en meme temps :

1. decorrelation du portefeuille
2. performance nette portefeuille
3. ROC net
4. utilisation du capital / du margin / du cash idle

Le cadre assume 3 books maintenus :

- Alpaca : actions US
- IBKR : FX / EU / futures / overlays cross-asset
- Binance : crypto spot / perp / carry / relative value

Tu travailles en autonomie. Tu n'attends pas de validation intermediaire. Tu documentes tes hypotheses, tu produis des artefacts exploitables, tu rejettes agressivement les idees faibles, et tu pousses seulement les candidats qui ameliorent le portefeuille reel.

## 1. Ce que signifie "reussir"

La mission est reussie si, au terme du chantier, on dispose de :

- une cartographie canonique du portefeuille actuel par book, strategie, facteur et usage du capital
- une baseline quantitative du portefeuille global
- un moteur de score marginal permettant de tester l'effet de chaque nouvelle strategie sur le portefeuille existant
- un backlog priorise de familles de strategies a explorer
- une serie de backtests / walk-forward / stress tests reproductibles
- une shortlist de strategies candidates a promouvoir en paper puis en live
- des criteres explicites pour supprimer les strategies qui n'apportent pas assez de diversification ou d'efficience du capital

Ne considere PAS le travail fini tant qu'il n'existe pas :

- un tableau comparatif "avant / apres ajout de la strategie"
- une mesure de correlation et de diversification marginale
- une mesure de ROC net et de capital occupancy
- une recommandation claire : `DROP`, `KEEP FOR RESEARCH`, `PROMOTE PAPER`, `PROMOTE LIVE`

## 2. These portefeuille sous-jacente

La these correcte n'est pas :

- "ajouter plus de strategies pour gagner plus"

La these correcte est :

- "construire un portefeuille multi-book dont les moteurs de PnL, de timing, de drawdown et de consommation de capital sont suffisamment differents pour lisser la courbe de capital, reduire les trous d'air et augmenter le rendement du capital engage"

Ce que nous cherchons en priorite :

- des moteurs peu ou moyennement correles aux expositions deja presentes
- des moteurs qui travaillent sur des plages horaires differentes
- des moteurs qui consomment peu de capital par unite de PnL espere
- des moteurs qui monetisent des regimes differents
- des moteurs qui ameliorent la convexite ou au moins reduisent la concentration sur les memes facteurs caches

Ce que nous voulons EVITER :

- du beta replique sous un nouveau nom
- des strategies "bonnes" en standalone mais redondantes dans le portefeuille
- des strategies gourmandes en capital sans gain marginal clair
- des strategies qui ont l'air intelligentes mais qui s'effondrent apres frais, slippage, funding ou contraintes d'execution

## 3. Definition stricte des objectifs d'optimisation

Tu dois raisonner avec une fonction objectif portefeuille, pas avec une collection de backtests isoles.

### 3.1 Fonction objectif de reference

Pour chaque strategie candidate `S`, calcule au minimum :

- `Delta_Portfolio_Sharpe`
- `Delta_Portfolio_CAGR`
- `Delta_Portfolio_MaxDD`
- `Delta_Portfolio_Calmar`
- `Delta_Portfolio_ROC_Net`
- `Delta_Capital_Utilization`
- `Delta_Idle_Cash`
- `Corr_To_Portfolio`
- `Corr_To_Book`
- `Tail_Overlap`
- `Worst_Day_Overlap`
- `Crash_Regime_Benefit`

### 3.2 ROC : definition canonique

Tu dois calculer au moins 2 versions du ROC :

- `ROC_margin = Net_PnL / Average_Committed_Margin`
- `ROC_risk = Net_PnL / Average_Capital_At_Risk`

Si la strategie est spot / cash non marginee, remplace le denominateur par :

- capital immobilise moyen
- cash reserve necessaire
- notionnel moyen reellement bloque

### 3.3 Capital utilization : definition canonique

Tu dois mesurer :

- `Capital_Occupancy = Time_Weighted_Committed_Capital / Deployable_Capital`
- `Margin_Occupancy = Time_Weighted_Used_Margin / Margin_Budget`
- `Idle_Cash_Rate = 1 - Capital_Occupancy`
- `PnL_per_Unit_of_Occupancy`

Une strategie peut etre gardee si elle :

- n'a pas le meilleur Sharpe standalone
- MAIS augmente le taux d'occupation utile du capital sans detruire le profil global

## 4. Hypotheses de travail sur le portefeuille actuel

Sur la base du repo et des artefacts existants, pars des hypotheses suivantes et verifie-les :

- le book crypto contient probablement deja plusieurs moteurs momentum / breakout / rotation
- le book IBKR a des briques plus heterogenes mais avec un risque de derive entre recherche, config et runtime
- le book Alpaca est utile surtout s'il apporte des expositions horaires, event-driven ou cross-sectional differentes des books crypto / FX / futures
- le portefeuille risque d'etre plus concentre qu'il n'en a l'air sur quelques grands facteurs caches :
  - beta risk-on
  - trend following simple
  - momentum cross-asset
  - regimes "calmes"

Tu dois donc prioriser la recherche de candidats qui ameliorent :

- diversification par horizon de detention
- diversification par moteur economique
- diversification par regime
- diversification par broker / source de microstructure
- efficience du capital

## 5. Regles d'or non negociables

1. Ne promeus AUCUNE strategie sur son seul resultat standalone.
2. Ne promeus AUCUNE strategie sans comparaison marginale au portefeuille actuel.
3. N'accepte PAS un gain de CAGR s'il est obtenu avec un MaxDD ou un ROC degrade sans justification solide.
4. Modele toujours les frais, slippage, funding, borrow, fees exchange et contraintes de taille.
5. Separe les resultats `gross`, `net fees`, `net realistic`.
6. Toute strategie doit etre testee par regimes, par sous-periodes et par stress.
7. Toute strategie intraday / event-driven doit avoir une hypothese explicite de liquidite et de latence.
8. Toute strategie tres correlee a un moteur deja live doit etre penalisee fortement.
9. Toute strategie faible en nombre de trades doit etre taggee `exploratory only`.
10. Toute strategie doit fournir un rationnel economique lisible en 5 lignes max.
11. Aucune promotion live si l'increment de portefeuille n'est pas documente.
12. Toute suppression de strategie existante est autorisee si elle libere du capital pour un moteur plus utile.

## 6. Artefacts existants a reutiliser d'abord

Tu ne repars pas de zero. Commence par reutiliser, auditer et etendre les briques deja presentes :

- `docs/portfolio_audit.md`
- `docs/allocation_analysis.md`
- `docs/allocation_optimization_v9.md`
- `scripts/correlation_matrix_strats.py`
- `scripts/allocation_optimizer.py`
- `scripts/roc_analysis.py`
- `scripts/roc_projection.py`
- `scripts/backtest_full_portfolio.py`
- `scripts/backtest_ib_portfolio_live.py`
- `scripts/backtest_portfolio_clean.py`
- `scripts/discover_backtest.py`
- `scripts/discover_backtest_long.py`
- `scripts/explore_angles_wave3.py`
- `scripts/explore_deep_angles.py`
- `scripts/explore_new_ib_angles.py`
- `scripts/run_stat_arb_pipeline.py`
- `core/alloc`
- `core/backtest`
- `core/backtester_v2`
- `core/optimization`
- `core/portfolio`
- `core/regime`
- `core/risk`
- `core/validation`

Objectif : etendre l'outillage existant, pas bricoler une nouvelle pile parallele sans besoin.

## 7. Deliverables obligatoires

Tu dois produire au minimum les livrables suivants.

### 7.1 Baseline portefeuille

- inventaire complet des strategies actuelles par book
- mapping strategie -> facteur / horizon / regime / capital model
- serie de rendements quotidienne et/ou intraday harmonisee
- matrice de correlation
- clustering des strategies
- decomposition des drawdowns simultanes
- occupation moyenne du capital et du margin

### 7.2 Scorecards candidates

Pour chaque nouvelle strategie candidate, produire un scorecard unique contenant :

- `strategy_id`
- `book`
- `asset_class`
- `thesis`
- `holding_period`
- `capacity_assumption`
- `execution_assumption`
- `gross_metrics`
- `net_metrics`
- `OOS_metrics`
- `stress_metrics`
- `corr_to_portfolio`
- `corr_to_book`
- `delta_portfolio_metrics`
- `capital_usage_metrics`
- `promotion_recommendation`
- `main_failure_mode`

### 7.3 Shortlists

Produire 4 listes finales :

- `Top decorrelation candidates`
- `Top ROC candidates`
- `Top capital utilization candidates`
- `Top all-around portfolio additions`

## 8. Ordre d'execution impose

Travaille dans cet ordre. Ne saute pas les etapes.

1. Baseline portefeuille actuelle
2. Moteur de score marginal
3. Cartographie des trous de diversification
4. Priorisation des familles de strategies
5. Preparation data / couts / contraintes
6. Batch de backtests par famille
7. Walk-forward / stress / monte carlo
8. Integration portefeuille
9. Classement final
10. Recommandation paper/live

## 9. Work packages XXL

## WP-01 - Construire la baseline canonique du portefeuille

### Objectif

Comprendre ce qui existe VRAIMENT aujourd'hui, pas ce qui est suppose exister.

### Taches

- dresser l'inventaire des strategies par book :
  - live
  - paper
  - research
  - legacy
- attribuer a chaque strategie :
  - un `strategy_id` stable
  - un book
  - une famille de signal
  - un horizon de detention
  - une estimation de marge / capital immobilise
  - une frequence de rotation
- reconstruire les series de PnL / returns harmonisees
- mesurer la contribution par strategie, par book, par regime
- identifier les moteurs qui ne servent presque a rien une fois agreges

### Questions a trancher

- quelles strategies sont vraiment actives ?
- quelles strategies sont des doublons economiques ?
- quelles strategies utilisent du capital sans amelioration reelle ?

### Outputs

- `docs/research/portfolio_baseline_2026-04-15.md`
- `data/research/portfolio_baseline_timeseries.parquet`
- `data/research/portfolio_strategy_inventory.csv`

### Acceptance criteria

- toute strategie existante est mappee
- toute strategie existante a une serie de returns propre ou un tag `missing_data`
- on sait quelles poches du portefeuille dominent le risque et l'usage du capital

## WP-02 - Construire le moteur de correlation, clustering et overlap

### Objectif

Passer d'une correlation simple a une vraie vue de redondance portefeuille.

### Taches

- etendre `scripts/correlation_matrix_strats.py`
- calculer correlation :
  - strategy vs strategy
  - strategy vs book
  - strategy vs portefeuille
- ajouter :
  - rolling correlation
  - downside correlation
  - correlation conditionnelle en regime stress
  - overlap des pires jours
  - overlap des drawdowns
  - cluster analysis / hierarchical clustering
- produire des heatmaps et clusters lisibles

### Outputs

- `scripts/portfolio_correlation_lab.py`
- `output/research/portfolio_correlation_matrix.csv`
- `output/research/portfolio_overlap_report.md`

### Acceptance criteria

- les strategies apparemment differentes mais statistiquement redondantes sont visibles
- les clusters de risque caches sont identifies

## WP-03 - Construire le moteur de score marginal portefeuille

### Objectif

Mesurer la valeur AJOUTEE d'une strategie candidate dans le portefeuille existant.

### Taches

- creer un pipeline `candidate_in / portfolio_out`
- pour chaque candidate, calculer :
  - delta Sharpe portefeuille
  - delta CAGR portefeuille
  - delta MaxDD portefeuille
  - delta Calmar
  - delta ROC margin
  - delta ROC risk
  - delta capital occupancy
  - delta idle cash
  - corr to portfolio
  - tail benefit / tail harm
- integrer des penalites :
  - forte correlation
  - grosse consommation de capital
  - faible nombre de trades
  - forte sensibilite parametrique
  - execution complexe

### Score recommande

Calculer un score composite de travail, par exemple :

`MarginalScore = 0.30 * Delta_Portfolio_Sharpe + 0.20 * Delta_Portfolio_Calmar + 0.20 * Delta_ROC_Net + 0.15 * Diversification_Benefit + 0.15 * Capital_Utilization_Benefit - Penalties`

Tu peux l'ameliorer, mais il doit rester interpretable.

### Outputs

- `scripts/portfolio_marginal_score.py`
- `output/research/marginal_strategy_scorecards/`

### Acceptance criteria

- chaque candidate peut etre notee automatiquement
- le classement n'est plus fonde sur l'intuition ou sur le standalone Sharpe

## WP-04 - Cartographier les trous de diversification

### Objectif

Trouver OU le portefeuille manque de moteurs.

### Taches

- cartographier les manques par :
  - horizon
  - regime
  - actif
  - source d'alpha
  - style
  - besoin de capital
- determiner si le portefeuille manque surtout :
  - mean reversion
  - event-driven
  - carry
  - relative value
  - seasonal / calendar
  - crisis alpha / convexite
  - dispersion / cross-sectional
- mesurer les periodes ou le portefeuille est le plus vulnerable

### Questions cle

- quand tous les moteurs existants perdent ensemble ?
- quel capital reste idle aux pires moments ?
- quels regimes ne sont presque pas monetises ?

### Outputs

- `docs/research/diversification_gap_map.md`

### Acceptance criteria

- la priorisation des familles de recherche est fondee sur les trous du portefeuille reel

## WP-05 - Construire le registre des hypotheses de recherche

### Objectif

Eviter le "spray and pray" et formaliser les hypotheses avant de coder.

### Taches

- creer un registre unique des hypotheses
- pour chaque hypothese, documenter :
  - nom
  - book cible
  - logique economique
  - regime cible
  - horizon
  - decorrelation attendue
  - ROC attendu
  - besoins data
  - couts / frictions
  - fail mode probable
- classer chaque hypothese en :
  - `high priority`
  - `medium priority`
  - `speculative`

### Outputs

- `docs/research/hypothesis_registry.md`
- `data/research/hypothesis_registry.csv`

### Acceptance criteria

- aucune nouvelle strategie n'est lancee sans fiche d'hypothese

## WP-06 - Prioriser les familles de strategies par book

### Objectif

Orienter la recherche vers les moteurs les plus susceptibles d'ameliorer le portefeuille.

### Priorite haute - Alpaca US

- overnight drift / month-end / turn-of-month
- post-earnings announcement drift
- gap continuation vs gap fade conditionnel au regime
- sector rotation cross-sectional
- dispersion / long-strong short-weak si faisable
- event-driven low holding period
- mean reversion sur paniers liquides plutot que sur titres isoles peu liquides

### Priorite haute - IBKR

- FX carry cross-sectional filtre regime
- FX momentum / reversal hybrides
- futures cross-asset trend peu correles au book crypto
- crisis alpha / vol expansion / risk-off overlays
- metals / rates / energy si cela apporte une vraie difference de facteur
- calendar / session effects sur indices ou futures
- pair / spread / relative value quand la these economique est claire

### Priorite haute - Binance

- funding / basis carry market neutral
- long/short cross-sectional crypto
- dominance / rotation regime aware
- liquidation / dislocation events
- intraday mean reversion sur excess moves
- overnight / weekend anomalies
- capital recycling spot/perp si la friction reelle reste favorable

### Rejet a priori

- strategies qui ajoutent surtout du beta BTC ou equity deja present
- strategies de breakout triviales sans preuve de decorrelation
- strategies ultra gourmandes en marge pour faible gain marginal
- strategies "smart sounding" sans these economique claire

## WP-07 - Auditer et etendre les datasets utiles

### Objectif

Supprimer les faux positifs lies a une data pauvre ou incomplete.

### Taches

- verifier la profondeur historique par actif / timeframe / book
- verifier la qualite timezone / session / holidays
- gerer les corporate actions pour les actions US
- gerer funding rates / borrow rates / fee tiers pour Binance
- gerer margin specs / tick value / roll pour futures
- gerer bid/ask proxy ou slippage model par famille
- ajouter les features exogenes utiles si disponibles :
  - VIX / vol regime
  - calendar macro
  - earnings calendar
  - funding / open interest / liquidation proxies
  - rates / yield differentials pour FX carry

### Outputs

- `docs/research/data_readiness_audit.md`
- `data/research/data_coverage_matrix.csv`

### Acceptance criteria

- chaque famille priorisee a une base data suffisante pour backtest robuste

## WP-08 - Standardiser les backtests de recherche

### Objectif

Avoir des backtests comparables et penalises de facon homogene.

### Taches

- definir un template de backtest commun
- harmoniser :
  - fees
  - slippage
  - fill assumptions
  - latency assumptions
  - capital allocation assumptions
  - max concurrent positions
  - margin model
  - portfolio sizing rules
- obliger chaque backtest a sortir :
  - gross
  - net fees
  - realistic net
  - occupancy stats
  - ROC stats
  - OOS stats

### Outputs

- `docs/research/backtest_protocol.md`
- templates/scripts reutilisables

### Acceptance criteria

- les resultats de familles differentes deviennent comparables sans retraitement manuel

## WP-09 - Batch de recherche Alpaca US

### Objectif

Chercher des moteurs US decorrelants, rapides et peu gourmands en capital.

### Lots a explorer

- `US-SEAS-01` : turn-of-month / month-end seasonality sur ETFs et megacaps liquides
- `US-EARN-01` : post-earnings drift filtre surprise / gap / volume
- `US-GAP-01` : gap fade / continuation conditionnel au regime VIX et au sens de la tendance
- `US-SEC-01` : rotation sectorielle cross-sectional sur ETFs
- `US-MR-01` : mean reversion panier liquide avec contrainte de beta / regime
- `US-RV-01` : pairs / ratio spreads robustes si l'infrastructure le permet

### Ce qu'il faut mesurer

- decorrelation vs crypto et FX
- occupation du capital en cash account / margin account
- risque PDT / rotation excessive
- slippage reeliste sur l'univers retenu
- fragilite aux earnings / news / overnight gaps

### Acceptance criteria

- au moins 2 familles US avec score marginal positif clair

## WP-10 - Batch de recherche IBKR FX

### Objectif

Trouver des moteurs FX a fort ROC et faible correlation au reste.

### Lots a explorer

- `FX-CARRY-01` : carry cross-sectional regime-aware
- `FX-MOM-01` : momentum cross-sectional avec filtre macro / vol
- `FX-MR-01` : mean reversion courte sur paires liquides, seulement si microstructure suffisante
- `FX-CALENDAR-01` : session / weekday / event windows
- `FX-RV-01` : spreads ou ranking relatifs par bloc devise

### Ce qu'il faut mesurer

- dependance aux regimes de taux
- stabilite des differentials
- cout reel IBKR
- overlap avec moteurs deja presents
- comportement en stress dollar / risk-off

### Acceptance criteria

- au moins 1 moteur FX ajoute du ROC net sans dupliquer les moteurs existants

## WP-11 - Batch de recherche IBKR futures / cross-asset

### Objectif

Chercher des moteurs cross-asset capables d'apporter diversification de regime et efficience du capital.

### Lots a explorer

- `FUT-TREND-01` : trend following cross-asset sur micro futures liquides
- `FUT-MR-01` : mean reversion courte sur indices / metals si robuste
- `FUT-CRISIS-01` : overlays risk-off / vol expansion / downside convexity
- `FUT-SPREAD-01` : intermarket spreads si data et these economique solides
- `FUT-CALENDAR-01` : session / day-of-week / month-turn effects
- `FUT-RATES-01` : moteurs rates/metal/energy si cela decorrele du risque general

### Ce qu'il faut mesurer

- marge requise vs PnL attendu
- queue risk
- roll costs
- concentration par evenement macro
- overlap avec crypto beta et equity trend

### Acceptance criteria

- au moins 1 moteur futures ameliore Calmar ou ROC du portefeuille global

## WP-12 - Batch de recherche IBKR EU

### Objectif

Ne garder le book EU que s'il apporte vraiment une source d'alpha differente.

### Lots a explorer

- `EU-EVENT-01` : BCE / macro-window / earnings-related drifts si defendable
- `EU-SEC-01` : rotation sectorielle ou relative strength paneuropeenne
- `EU-MR-01` : mean reversion d'ouverture / overnight uniquement si la logique microstructure tient
- `EU-DISP-01` : dispersion sectorielle / country rotation

### Ce qu'il faut mesurer

- frictions de marche europeennes
- couts reels IBKR
- interet reel vs book US
- valeur marginale par rapport aux autres books

### Acceptance criteria

- si le book EU n'apporte pas un moteur distinct, il doit etre simplifie ou mis en veille

## WP-13 - Batch de recherche Binance crypto

### Objectif

Sortir d'un book crypto trop concentre sur le meme beta et le meme type de signal.

### Lots a explorer

- `CR-CARRY-01` : funding / basis carry market neutral
- `CR-RV-01` : long/short cross-sectional alts vs majors
- `CR-DOM-01` : rotation BTC / ETH / alts selon dominance et regime
- `CR-EVENT-01` : liquidation spikes / dislocations / mean reversion event-driven
- `CR-WEEKEND-01` : weekend / overnight anomalies
- `CR-VOL-01` : vol breakout / vol crush conditionnel au regime

### Ce qu'il faut mesurer

- correlation au beta BTC
- sensibilite au funding
- liquidite reellement executable
- cout de rotation
- impact du leverage et de la marge croisee / isolee

### Acceptance criteria

- au moins 2 familles crypto montrent une valeur marginale superieure a un simple beta / momentum deja existant

## WP-14 - Calibrer les couts, slippage, funding et capacite

### Objectif

Eviter les strategies "gagnantes sur Excel" mais inutilisables en vrai.

### Taches

- calibrer par broker :
  - commissions
  - fees exchange
  - spread proxy
  - slippage par taille
  - funding / borrow / carry costs
- mesurer la capacite approximative :
  - taille max
  - temps d'execution
  - fragilite au fill
- distinguer :
  - small capital reality
  - scale-up reality

### Outputs

- `docs/research/cost_capacity_assumptions.md`

### Acceptance criteria

- aucun scorecard ne se contente de couts irreels ou implicites

## WP-15 - Walk-forward, Monte Carlo, stress et stabilite parametrique

### Objectif

Ne pas promouvoir une strategie simplement parce qu'elle a trouve son regime historique.

### Taches

- definir IS / OOS clairs par famille
- lancer walk-forward systematique
- tester stabilite parametrique
- tester permutation / Monte Carlo si approprie
- tester stress par regime :
  - vol haute
  - vol basse
  - crash equity
  - squeeze crypto
  - dollar shock
  - funding stress
- mesurer degradation realistic net

### Outputs

- `output/research/wf_reports/`
- `output/research/stress_reports/`

### Acceptance criteria

- une strategie fragile ou trop parametrisee ne passe pas en shortlist

## WP-16 - Optimisation portefeuille et allocation

### Objectif

Passer de "strategies interessantes" a "portefeuille mieux construit".

### Taches

- reprendre les travaux existants sur allocation / HRP / optimizer
- comparer plusieurs schemas :
  - equal weight strategies
  - risk parity
  - HRP / clustering aware
  - ROC-weighted
  - marginal-score-weighted
  - capital-occupancy-aware
- imposer des contraintes :
  - budget de marge
  - budget de drawdown
  - budget par broker
  - budget par facteur
  - budget de correlation

### A mesurer

- gain de portefeuille net
- baisse ou non du MaxDD
- meilleure utilisation du capital
- robustesse des poids dans le temps

### Outputs

- `docs/research/portfolio_optimizer_results.md`
- `output/research/optimized_allocations.csv`

### Acceptance criteria

- existence d'une allocation cible defendable et lisible

## WP-17 - Construire le comite de promotion / rejection

### Objectif

Ne plus ajouter de strategies sans discipline de gouvernance.

### Taches

- definir des gates explicites :
  - `REJECT`
  - `RESEARCH_MORE`
  - `PAPER_ONLY`
  - `LIVE_SMALL`
  - `LIVE_NORMAL`
- definir des seuils de travail par famille
- exiger une fiche unique par strategie

### Gating minimal recommande

Une strategie ne doit pas etre promue si :

- son `Delta_Portfolio_Sharpe <= 0`
- son `Delta_Portfolio_Calmar <= 0` sans forte justification de ROC
- son `Corr_To_Portfolio > 0.70` ET qu'elle n'apporte pas un ROC nettement superieur
- son `Capital_Occupancy` est eleve pour une faible valeur marginale
- son edge disparait apres couts / slippage / funding
- elle a trop peu de trades OOS pour sa frequence
- elle est trop sensible a un parametre critique

Une strategie peut etre promue meme avec un Sharpe standalone moyen si :

- elle reduit le drawdown global
- elle monetise un regime manquant
- elle augmente le ROC portefeuille
- elle utilise du capital sinon idle

### Outputs

- `docs/research/promotion_committee_rules.md`
- `data/research/final_strategy_committee.csv`

## WP-18 - Mettre en place la boucle continue de recherche

### Objectif

Faire de cette demarche un systeme, pas un one-shot.

### Taches

- definir une cadence hebdo / mensuelle :
  - revue du portefeuille
  - revue des correlations
  - revue du capital usage
  - revue des hypotheses
- tagger automatiquement :
  - nouveaux candidats
  - candidats rejetes
  - candidats a rerun
- maintenir un backlog vivant

### Outputs

- `docs/research/research_operating_system.md`

### Acceptance criteria

- la recherche portefeuille continue meme apres la premiere vague de candidates

## 10. Grille de score obligatoire pour chaque strategie

Chaque strategie candidate doit sortir avec cette grille minimale.

### Identite

- `strategy_id`
- `book`
- `asset_class`
- `universe`
- `signal_family`
- `holding_period`
- `rebalance_frequency`

### These

- these economique en 5 lignes max
- raison attendue de decorrelation
- raison attendue d'amelioration du ROC
- raison attendue d'amelioration de l'usage du capital

### Qualite standalone

- nombre de trades
- win rate
- expectancy
- Sharpe
- Sortino
- MaxDD
- Calmar
- gross PnL
- net PnL
- realistic net PnL

### Robustesse

- IS / OOS split
- walk-forward
- stabilite parametrique
- stress by regime
- capacite
- degrade after costs

### Valeur portefeuille

- correlation au portefeuille
- correlation au book
- downside correlation
- overlap des pires jours
- delta Sharpe portefeuille
- delta MaxDD portefeuille
- delta Calmar portefeuille
- delta ROC portefeuille
- delta capital occupancy
- note finale

### Verdict

- `DROP`
- `KEEP FOR RESEARCH`
- `PROMOTE PAPER`
- `PROMOTE LIVE SMALL`
- `PROMOTE LIVE`

## 11. Priorisation tres concrete des familles a lancer d'abord

L'agent doit lancer en premier les familles qui ont le meilleur ratio :

- probabilite de decorrelation
- probabilite d'ameliorer le ROC
- probabilite d'utiliser du capital actuellement mal employe
- faisabilite data / execution

### Tier 1 - A lancer immediatement

- US post-earnings drift
- US sector rotation cross-sectional
- FX carry cross-sectional regime-aware
- futures cross-asset trend / crisis overlay
- crypto funding / basis carry market neutral
- crypto long/short cross-sectional

### Tier 2 - Lancer apres baseline propre

- US gap regime-aware
- EU sector / relative strength
- futures calendar / session effects
- crypto liquidation event-driven
- crypto weekend anomaly enrichie par regime

### Tier 3 - Speculatif

- pair trading complexe a forte exigence de qualite data
- signaux trop exotiques sans these economique forte
- alpha ML opaque non interpretable a ce stade

## 12. Ce qu'il faut supprimer ou penaliser sans etat d'ame

- toute strategie dont la promesse est surtout un renommage d'un beta deja present
- toute strategie qui detruit le ROC portefeuille
- toute strategie qui immobilise du capital pour un gain marginal negligeable
- toute strategie qui ajoute de la complexite operationnelle sans gain clair
- toute strategie fragile a une seule periode ou a un seul set de params
- toute strategie qui n'existe que grace a des couts irreels

## 13. Format de restitution attendu

L'agent doit produire un rendu final en 5 blocs.

### Bloc 1 - Diagnostic portefeuille actuel

- ce qui diversifie vraiment
- ce qui duplique
- ce qui immobilise du capital
- ce qui merite d'etre coupe

### Bloc 2 - Gap map

- quels regimes / moteurs / horizons manquent

### Bloc 3 - Resultats de recherche

- top candidates par book
- top candidates globaux
- candidats rejetes et pourquoi

### Bloc 4 - Nouvelle allocation cible

- poids par book
- poids par strategie
- budgets de marge / drawdown / correlation

### Bloc 5 - Plan de promotion

- paper first
- live small
- live normal
- kill criteria

## 14. Definition des kill criteria par strategie promue

Pour toute strategie promue en paper ou live, definir des kill criteria explicites :

- drawdown absolu
- drawdown relatif a l'attendu
- degradation du fill / slippage
- perte de ROC
- hausse de correlation au reste du portefeuille
- perte de these economique
- changement de regime rendant la strategie non prioritaire

Chaque strategie promue doit avoir :

- un budget de risque
- un budget de capital
- un niveau de taille initiale
- une condition de scale-up
- une condition de scale-down
- une condition de stop

## 15. Checklist d'execution pour Claude

### Semaine 1 - Baseline et instrumentation

- [ ] finaliser l'inventaire canonique des strategies
- [ ] produire la baseline returns / capital / margin
- [ ] produire la matrice de correlation et le clustering
- [ ] produire le gap map initial
- [ ] produire le protocole de score marginal

### Semaine 2 - Donnees, couts et hypotheses

- [ ] auditer les datasets et les trous de data
- [ ] calibrer frais / slippage / funding / borrow
- [ ] construire le registre des hypotheses
- [ ] prioriser Tier 1 / Tier 2 / Tier 3

### Semaine 3 - Batch de recherche 1

- [ ] lancer US post-earnings drift
- [ ] lancer FX carry regime-aware
- [ ] lancer crypto funding / basis carry
- [ ] lancer futures trend / crisis overlay
- [ ] scorer chaque candidate en marginal

### Semaine 4 - Batch de recherche 2

- [ ] lancer US sector rotation
- [ ] lancer crypto long/short cross-sectional
- [ ] lancer futures calendar / session
- [ ] lancer EU sector / event si la data est suffisante
- [ ] reranker tout le lot

### Semaine 5 - Robustesse

- [ ] walk-forward
- [ ] stress tests
- [ ] monte carlo / permutations si utile
- [ ] stability maps

### Semaine 6 - Allocation et promotion

- [ ] construire la nouvelle allocation cible
- [ ] definir la shortlist paper
- [ ] definir la shortlist live small
- [ ] proposer les strategies a couper

## 16. Recommandation strategique de fond

La bonne question n'est pas :

- "quelle est la meilleure prochaine strategie ?"

La bonne question est :

- "quelle est la prochaine strategie dont l'ajout ameliore le plus la frontiere rendement / drawdown / ROC / usage du capital du portefeuille reel ?"

Cherche donc prioritairement :

- du carry la ou le portefeuille est surtout momentum
- du relative value la ou le portefeuille est surtout directionnel
- du mean reversion la ou le portefeuille est surtout trend
- du crisis alpha la ou le portefeuille souffre en risk-off
- des moteurs capital-light la ou le capital reste idle

## 17. Verdict de depart a utiliser comme biais de priorisation

Point de depart recommande :

- le plus gros potentiel de decorrelation utile semble venir de :
  - FX carry / cross-sectional
  - futures cross-asset / crisis overlays
  - crypto market-neutral carry / RV
  - US event-driven / sector rotation

- le plus gros risque de faux positif semble venir de :
  - breakout redondants
  - momentum trop proche de l'existant
  - strategies jolies en backtest mais gourmandes en capital
  - approches "smart" sans realite de couts / fills

## 18. Derniere consigne

Sois brutalement honnete.

Si une strategie est jolie mais inutile pour le portefeuille, rejette-la.
Si une strategie est moyenne en standalone mais excellente pour le portefeuille, garde-la.
Si une strategie libere du capital en remplacant une strategie existante mediocre, considere-la comme une vraie creation de valeur.
Si une strategie n'augmente ni la robustesse, ni le ROC, ni l'usage du capital, ni la diversification, elle n'a rien a faire dans le systeme.
