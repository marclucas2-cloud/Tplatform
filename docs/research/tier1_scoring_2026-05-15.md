# Scoring Tier 1 - candidats decorrelation

Date: 2026-05-17  
Scope: scoring documentaire uniquement, pas de backtest complet, pas de strategie codee.

Contexte lu:

- `docs/research/hypothesis_registry.md` - WP-05
- `docs/research/diversification_gap_map.md` - WP-04
- `scripts/research/portfolio_marginal_score.py` - WP-03
- `data/research/portfolio_baseline_timeseries.parquet` - WP-01
- `data/research/us_single_stock_anomalies.md`
- `data/research/us_equity_universe.md`

Methodologie:

- J'ai cree des proxies PnL journaliers synthetiques, calibres sur "edge litterature transposee a 50%".
- Ces proxies ne sont pas des backtests. Ils servent uniquement a voir si une version plausible de l'edge passerait les hard gates corr/tail/maxDD du moteur `score_candidate()`.
- Global score sur 5:
  - 25% `edge_strength`
  - 20% `data_availability`
  - 20% `faisabilite_technique`
  - 20% `decorrelation_estimee`
  - 15% penalite overfit via `(6 - risque_overfit)`
- `risque_overfit`: 1 = faible risque, 5 = risque eleve.
- Aucun resultat ci-dessous ne vaut autorisation paper/live.

## Table recap

| ID | Candidat | Edge | Data | Tech | Decorr | Overfit risk | Score global | score_candidate | Reco |
|---|---|---:|---:|---:|---:|---:|---:|---|---|
| T1-03 | Futures mean reversion intraday MES/MGC | 3.5 | 4.5 | 4.0 | 5.0 | 3.0 | **4.03** | 0.229 / raw PROMOTE_PAPER | **A backtester PRIORITAIRE** |
| T1-01 | Crypto basis / funding carry market neutral | 4.0 | 5.0 | 2.0 | 5.0 | 2.0 | **4.00** | 0.251 / raw PROMOTE_PAPER | A backtester apres verification perps |
| T1-04 | Futures calendar / session effects MES | 2.5 | 5.0 | 4.5 | 5.0 | 4.0 | **3.83** | 0.182 / raw PROMOTE_PAPER | A backtester apres priorite |
| T1-02 | US PEAD regime-aware | 4.0 | 2.0 | 3.0 | 5.0 | 3.5 | **3.38** | 0.164 / raw PROMOTE_PAPER | A parquer tant que data gratuite |
| T1-05 | Crypto L/S cross-sectional alts vs majors | 3.5 | 4.0 | 3.0 | 3.5 | 3.5 | **3.35** | 0.083 / raw KEEP_FOR_RESEARCH | A parquer / batch secondaire |

Important: le moteur marginal donne un verdict "PROMOTE_PAPER" pour T1-01/T1-02/T1-03/T1-04 sur proxies synthetiques, mais je cappe tous les verdicts a `KEEP_FOR_RESEARCH` tant qu'il n'y a pas de vrai WF net costs. La recommandation "A backtester" signifie seulement "prochaine Phase research".

## Score marginal synthetique

| ID | Proxy standalone Sharpe | Total PnL proxy | dSharpe | dMaxDD pp | Corr portf | Max corr strat | Tail overlap | Marginal score | Corr CAM | Corr GOR | Corr BTC |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| T1-01 | 0.890 | $4,949 | +0.051 | +9.25 | 0.043 | 0.046 | 0.033 | 0.251 | 0.012 | 0.020 | 0.032 |
| T1-02 | 0.352 | $2,454 | +0.024 | +12.98 | -0.002 | 0.020 | 0.000 | 0.164 | 0.020 | -0.003 | -0.004 |
| T1-03 | 1.235 | $7,688 | +0.083 | +10.43 | 0.016 | 0.021 | 0.033 | 0.229 | 0.011 | -0.004 | 0.003 |
| T1-04 | 0.403 | $1,248 | +0.013 | +4.16 | 0.005 | 0.021 | 0.000 | 0.182 | 0.004 | -0.007 | 0.014 |
| T1-05 | 0.331 | $5,645 | +0.023 | +13.57 | 0.108 | 0.117 | 0.033 | 0.083 | -0.006 | 0.025 | 0.079 |

### Hypotheses proxies

- T1-01: 50% de 5-15% annuel attendu -> environ 5% annuel sur 10k, carry continu low-vol, haircut 2022 et jours crypto tail.
- T1-02: 50% de 8-20% annuel PEAD -> event days sparse, degradation 2020/2022 et bruit timestamps gratuits.
- T1-03: 50% short-horizon reversal -> petits PnL intraday frequents, haircut sur jours de continuation forte.
- T1-04: 50% de 3-8% annuel calendar/session -> PnL sparse sur turn-of-month / jours de semaine, pas d'optimisation.
- T1-05: 50% de 15-25% annuel crypto dispersion -> rendement espere plus haut mais volatilite/tail crypto et beta BTC residuel.

## T1-01 - Crypto basis / funding carry market neutral

### Identite

Hypothese economique:

Le marche des perps crypto paie periodiquement un funding entre longs et shorts pour ancrer le contrat au spot. Quand le funding est durablement positif, une position long spot + short perp peut monetiser le carry sans prendre beaucoup de beta directionnel. L'edge vient de la demande structurelle de levier long, de la segmentation des marches et des frictions d'arbitrage.

Reference principale:

- Makarov & Schoar, "Trading and Arbitrage in Cryptocurrency Markets", Journal of Financial Economics, 2020. Le resume CFA souligne les ecarts persistants entre marches crypto et les limites d'arbitrage.
- Source operationnelle: Binance USD-M Futures expose l'historique via `GET /fapi/v1/fundingRate`.

Edge attendu litterature / desk:

- WP-05 estime 5-15% annuel, faible DD, regime bull/calm.
- Proxy 50%: Sharpe standalone 0.89, dSharpe portefeuille +0.051.

### Faisabilite data

Data necessaire:

- Funding rates historiques par symbole.
- Mark/spot/perp OHLCV.
- Fees spot/perp, funding timestamps, borrow/margin constraints.
- Liquidite et open interest pour filtrer les perps trop petits.

Sources gratuites:

- Binance API: funding history, klines spot/perp, mark price.
- Pas besoin de yfinance/Polygon/Norgate.

Lacunes full gratuit:

- Historique des frais reels par tier VIP et rebates.
- Historique exact de contraintes compte France / disponibilite perps.
- Slippage book-level si on veut scaler.

Cout data payante minimale:

- Aucun pour Phase research.
- Vaut-le-coup: oui, gratuit d'abord. Ne pas payer avant un WF net costs.

Time-to-data:

- 1-2 jours pour dataset propre BTC/ETH/SOL/BNB perps + funding.

### Faisabilite technique

Broker requis:

- Ideal: Binance spot + Binance USD-M perps.
- Point bloquant: le compte de Marc est documente comme spot/earn/margin BTC/ETH/SOL, pas explicitement futures/perps. Si perps non accessibles en France, la strategie devient research-only ou doit etre adaptee en cross-broker spot Binance + micro BTC futures IBKR.

Horizon:

- Continuous / weekly rebalance. N'adresse pas le gap intraday, mais occupe du capital idle avec un moteur carry different.

Backtest engine:

- Nouveau simulateur market-neutral spot/perp requis, mais simple: funding accrual, mark-to-market, fees, margin.
- Estimation Codex: 6-10h pour V1 propre + tests no-lookahead/costs.

### Marginal score estime

Proxy `score_candidate()`:

- Marginal score: 0.251
- Raw engine verdict: PROMOTE_PAPER
- Verdict research cappe: KEEP_FOR_RESEARCH
- Corr attendue: CAM 0.012, GOR 0.020, BTC proxy 0.032

Interpretation:

Le proxy passe les hard gates corr/tail/maxDD. C'est le meilleur marginal score, mais il ne peut pas etre recommande #1 tant que l'acces perps Binance n'est pas confirme.

### Risques d'echec / blockers connus

- Funding negatif prolonge en bear/deleveraging.
- Liquidations si hedge ratio spot/perp mal gere.
- Regulatory/execution: acces Binance perps France a verifier.
- Stablecoin / exchange risk.
- Capacity OK sur BTC/ETH, plus fragile sur alts.
- Risque de repetition QMJ faible cote data: donnees gratuites objectives, timestamps exchange, pas de fundamentals restated.

### Verdict & priorite

Notes:

- edge_strength: 4/5
- data_availability: 5/5
- faisabilite_technique: 2/5
- decorrelation_estimee: 5/5
- risque_overfit: 2/5
- score global: 4.00/5

Recommandation:

- **A backtester apres verification perps**.
- Si Binance perps sont confirmes utilisables sans nouveau KYC, ce candidat remonte immediatement en #1 ex-aequo.

## T1-02 - US PEAD regime-aware

### Identite

Hypothese economique:

Apres une annonce de resultats, le marche sous-reagit parfois aux surprises EPS/revenue. Les beats confirmes par les ventes continuent a deriver positivement; les misses confirmes continuent a deriver negativement. Le signal est event-driven et devrait etre moins correle au book futures.

References principales:

- Bernard & Thomas, "Post-Earnings-Announcement Drift: Delayed Price Response or Risk Premium?", Journal of Accounting Research, 1989.
- Jegadeesh & Livnat / Livnat, "Post-Earnings-Announcement Drift: The Role of Revenue Surprises", 2003/2006: drift plus fort quand revenue surprise et earnings surprise vont dans le meme sens.

Edge attendu litterature:

- Historiquement robuste sur SUE deciles; WP-05 estime 8-20% annuel sur univers filtre.
- Proxy 50% avec bruit data: Sharpe standalone 0.352, dSharpe +0.024.

### Faisabilite data

Data necessaire:

- Earnings calendar avec timestamp BMO/AMC.
- EPS surprise, revenue surprise, consensus revisions point-in-time.
- Prix intraday ou open event-day, ETB/shortability daily.
- Corporate actions/dividends pour short PnL.

Sources gratuites:

- yfinance earnings: coverage et timestamps imparfaits.
- EDGAR: filings, mais pas consensus surprise propre.
- Alpaca: paper execution, assets shortable/ETB en live, pas historique complet PIT.

Lacunes full gratuit:

- Timestamps BMO/AMC bruyants.
- Revenue surprise et consensus PIT absents ou incomplets.
- Survivorship bias si S&P actuel.
- Short availability historique non disponible.

Cout data payante minimale:

- Polygon/Benzinga/I/B/E/S-like earnings timestamps et surprises. Contexte Marc: Polygon environ $199/mois pour un flux utile.
- Norgate Platinum (~$630/an) aide survivorship/delisted mais ne resout pas les timestamps earnings/revenue surprise.
- Vaut-le-coup: **non maintenant**. ROI attendu sur $20k n'est pas etabli: a 50% edge, l'esperance annuelle plausible est inferieure ou comparable au cout $2,388/an de Polygon, avant slippage/borrow.

Time-to-data:

- Gratuit bruyant: 3-5 jours.
- Dataset payant admissible: 1-2 semaines apres choix provider et mapping.

### Faisabilite technique

Broker requis:

- Alpaca paper pour US equities.
- Live impossible avant gate PDT / capital >25k et validation Marc.

Horizon:

- 5-20 jours. Comble le trou event-driven/short horizon, mais pas intraday.

Backtest engine:

- Peut reutiliser `core/backtest/us_equity_event_driven.py`.
- Necessite module data events + timestamp guard.
- Estimation Codex: 12-20h si data gratuite; 20-35h si normalisation provider payant.

### Marginal score estime

Proxy `score_candidate()`:

- Marginal score: 0.164
- Raw engine verdict: PROMOTE_PAPER
- Verdict research cappe: KEEP_FOR_RESEARCH
- Corr attendue: CAM 0.020, GOR -0.003, BTC -0.004

Interpretation:

Le profil decorrelation est excellent, mais le score ne paie pas le risque data. Apres QMJ V1, il faut etre plus dur: sans timestamps fiables, PEAD peut devenir un backtest flatteur et non executable.

### Risques d'echec / blockers connus

- Meme piege que QMJ: edge academique solide mais data gratuite non-PIT ou imprecise.
- Short misses peuvent squeeze.
- High VIX >30: beta marche domine l'event drift.
- PDT si on derive vers intraday.
- Provider payant difficile a amortir sur $20k.

### Verdict & priorite

Notes:

- edge_strength: 4/5
- data_availability: 2/5
- faisabilite_technique: 3/5
- decorrelation_estimee: 5/5
- risque_overfit: 3.5/5
- score global: 3.38/5

Recommandation:

- **A parquer tant que data gratuite**.
- Ne pas acheter Polygon/Norgate juste pour PEAD sans preuve ROI plus forte.

## T1-03 - Futures mean reversion intraday MES/MGC

### Identite

Hypothese economique:

Les mouvements intraday excessifs forcent des liquidations, stops et flux de hedging qui peuvent aller trop loin a court terme. Sur MES/MGC, des extensions >2x ATR ou des ranges d'ouverture extremes peuvent retracer partiellement dans la meme session ou la session suivante. L'edge est microstructure/liquidity-provision, pas facteur fondamental.

Reference principale:

- Lehmann, "Fads, Martingales, and Market Efficiency", 1990: evidence de reversals court terme apres winners/losers, avec interpretation overreaction/liquidity.

Edge attendu:

- WP-05: petit mais stable, tres dependant du capital deploye.
- Proxy 50%: Sharpe standalone 1.235, dSharpe +0.083, marginal score 0.229.

### Faisabilite data

Data necessaire:

- MES/MGC 5m ou 1h OHLCV.
- Tick size, multipliers, commissions, slippage.
- RTH/ETH session calendar, holidays, roll futures.

Sources gratuites:

- IBKR paper gateway historical gratuit, avec limites de pacing.
- Local `data/futures/` si 5m/1h deja disponible.
- yfinance futures proxies en fallback pour smoke uniquement, pas validation finale intraday.

Lacunes full gratuit:

- Profondeur historique 5m via IBKR limitee / lente a reconstruire.
- Pas de carnet/order book pour slippage regime stress.
- Roll continuous futures a auditer.

Cout data payante minimale:

- Pas necessaire avant un premier WF sur donnees locales/IBKR recentes.
- Si besoin de 5-10 ans intraday propre CME, un provider payant peut etre discute plus tard, mais ROI non justifie aujourd'hui.

Time-to-data:

- 1-3 jours si fichiers locaux suffisants.
- 3-7 jours si backfill IBKR par chunks + audit roll/session.

### Faisabilite technique

Broker requis:

- IBKR futures deja disponible.
- Aucun nouveau KYC, pas de PDT, micro futures compatibles capital $20k.

Horizon:

- Intraday <= 1 jour. C'est le meilleur fit avec le gap WP-04: 0% intraday aujourd'hui et 86% de jours idle.

Backtest engine:

- BacktesterV2 probablement reutilisable si single-leg stops intraday ok.
- Il faut surtout un data feed intraday propre, session calendar, anti-lookahead 9:35-15:55 ET, et stops obligatoires.
- Estimation Codex: 10-16h pour V1 event-driven + tests.

### Marginal score estime

Proxy `score_candidate()`:

- Marginal score: 0.229
- Raw engine verdict: PROMOTE_PAPER
- Verdict research cappe: KEEP_FOR_RESEARCH
- Corr attendue: CAM 0.011, GOR -0.004, BTC 0.003

Interpretation:

Le proxy passe les hard gates et ameliore le portefeuille sans augmenter tail overlap. Contrairement a PEAD/QMJ, les donnees critiques sont prix/execution, pas fundamentals/timestamps proprietaires.

### Risques d'echec / blockers connus

- Forte tendance intraday: le mean reversion fade un vrai breakout.
- CPI/FOMC/NFP: spreads et continuations peuvent casser les stops.
- Slippage ticks sous-estime si backtest bar-only.
- Capacity faible mais suffisante pour micro futures.
- Risque overfit moyen: beaucoup de seuils possibles; il faudra figer peu de variantes.

### Verdict & priorite

Notes:

- edge_strength: 3.5/5
- data_availability: 4.5/5
- faisabilite_technique: 4/5
- decorrelation_estimee: 5/5
- risque_overfit: 3/5
- score global: 4.03/5

Recommandation:

- **A backtester PRIORITAIRE**.
- C'est le meilleur compromis entre edge plausible, data gratuite, broker disponible, gap intraday et absence de piege PIT type QMJ.

## T1-04 - Futures calendar / session effects MES day-of-week

### Identite

Hypothese economique:

Certains flux calendaires recurrent: turn-of-month, pre-holiday, day-of-week, FOMC/NFP. Ces flux peuvent creer des primes temporelles independantes des signaux momentum/rotation du book actuel. L'edge est simple, transparent, mais vulnerable au data-mining.

References principales:

- Lakonishok & Smidt, "Are Seasonal Anomalies Real? A Ninety-Year Perspective", 1988.
- McConnell & Xu, "Equity Returns at the Turn of the Month", 2006: effet turn-of-month persistant sur longues periodes.
- Lucca & Moench, "The Pre-FOMC Announcement Drift", 2015, avec avertissement: litterature recente documente un affaiblissement apres 2015.

Edge attendu:

- WP-05: 3-8% annuel standalone.
- Proxy 50%: Sharpe standalone 0.403, dSharpe +0.013.

### Faisabilite data

Data necessaire:

- MES daily/open-close, holiday calendar, FOMC/NFP calendar.
- Commissions/tick slippage.

Sources gratuites:

- Fichiers futures daily locaux.
- FRED/Fed calendar public, calendar exchange.
- IBKR daily history gratuit.

Lacunes full gratuit:

- Effets intraday/session precis demandent open/close fiables autour holidays.
- Economic calendar historique a normaliser.

Cout data payante minimale:

- Aucun pour V1 daily/session.
- Vaut-le-coup: non, gratuit suffit pour trier.

Time-to-data:

- 0.5-1 jour.

### Faisabilite technique

Broker requis:

- IBKR futures deja disponible.

Horizon:

- 1-3 jours, parfois overnight. Comble `calendar_seasonal` absent, pas le gap intraday pur.

Backtest engine:

- BacktesterV2 ou script event-driven simple.
- Estimation Codex: 4-8h pour WF/MC propre car un script in-sample existe deja.

### Marginal score estime

Proxy `score_candidate()`:

- Marginal score: 0.182
- Raw engine verdict: PROMOTE_PAPER
- Verdict research cappe: KEEP_FOR_RESEARCH
- Corr attendue: CAM 0.004, GOR -0.007, BTC 0.014

Interpretation:

Tres decorrelant, facile a tester, mais l'edge attendu est faible et le risque de data-mining est le plus eleve du Tier 1.

### Risques d'echec / blockers connus

- Calendar effects s'erodent ou disparaissent.
- Forte dependance a quelques sous-effets.
- Risque de selection de variantes apres coup: deja 11 variantes testees dans WP-05.
- Effets FOMC documentes comme affaiblis apres 2015.
- Capacity OK pour micro futures.

### Verdict & priorite

Notes:

- edge_strength: 2.5/5
- data_availability: 5/5
- faisabilite_technique: 4.5/5
- decorrelation_estimee: 5/5
- risque_overfit: 4/5
- score global: 3.83/5

Recommandation:

- **A backtester apres T1-03**.
- Bon candidat de validation rapide, mais pas celui a prioriser si l'objectif est une nouvelle source robuste.

## T1-05 - Crypto long/short cross-sectional alts vs majors

### Identite

Hypothese economique:

Les cryptos ont des facteurs cross-sectionnels type marche, taille et momentum. Une strategie long alts forts vs short alts faibles, beta-neutralisee contre BTC/ETH, cherche a capter dispersion et relative value sans prendre tout le beta crypto. L'edge vient de flux retail, attention, rotation narrative et segmentation de liquidite.

Reference principale:

- Liu, Tsyvinski & Wu, "Common Risk Factors in Cryptocurrency", NBER 2019 / Journal of Finance 2022: facteurs market, size, momentum et strategies long-short crypto significatives.

Edge attendu:

- WP-05: 15-25% annuel si bien execute.
- Proxy 50% avec crash tails: Sharpe standalone 0.331, marginal score 0.083.

### Faisabilite data

Data necessaire:

- Binance daily/hourly klines top 20 alts.
- Funding rates si short perps; margin borrow si short spot/margin.
- Delist history, symbol migrations, stablecoin quote changes.
- Liquidity/volume filters.

Sources gratuites:

- Binance API klines.
- Binance funding history endpoint si perps utilises.
- Exchange info for delist/current symbols, mais historique delist a reconstruire.

Lacunes full gratuit:

- Survivorship crypto: top 20 actuels ignore morts/delists.
- Historique borrow/funding par compte.
- Crash slippage et forced deleveraging.

Cout data payante minimale:

- Aucun pour first pass Binance.
- Coinalyze/CoinMetrics/Kaiko utiles mais non justifies avant premier rejet/validation gratuite.

Time-to-data:

- 1-2 jours pour top current symbols.
- 4-7 jours si reconstruction delist/survivorship plus propre.

### Faisabilite technique

Broker requis:

- Binance spot/margin deja disponible pour quelques majors.
- Short alts/perps peut etre bloque selon compte France et pairs margin disponibles.

Horizon:

- 10-20 jours, donc ressemble au swing actuel. Comble dispersion/relative_value mais pas intraday.

Backtest engine:

- Multi-asset long/short crypto engine avec funding/borrow, delist handling, beta hedge.
- Estimation Codex: 12-18h pour V1.

### Marginal score estime

Proxy `score_candidate()`:

- Marginal score: 0.083
- Raw engine verdict: KEEP_FOR_RESEARCH
- Verdict research: KEEP_FOR_RESEARCH
- Corr attendue: CAM -0.006, GOR 0.025, BTC 0.079

Interpretation:

Le proxy passe les hard gates mais le score est faible pour son risque executionnel. La correlation a BTC reste acceptable dans le proxy, mais en vrai elle peut grimper brutalement en crash.

### Risques d'echec / blockers connus

- 2022-like universal alt crash: alts se correlent a 1.
- Delists et survivorship bias tres forts.
- Funding/borrow short alt peut tuer l'edge.
- Capacity faible hors BTC/ETH/SOL/BNB.
- Repetition QMJ possible: facteur academique documente, mais fragile post-fees et post-2021 regime.

### Verdict & priorite

Notes:

- edge_strength: 3.5/5
- data_availability: 4/5
- faisabilite_technique: 3/5
- decorrelation_estimee: 3.5/5
- risque_overfit: 3.5/5
- score global: 3.35/5

Recommandation:

- **A parquer / batch secondaire**.
- Interessant si Binance perps/margin short coverage est confirmee, mais moins propre que T1-03 et moins carry-pur que T1-01.

## Recommandation Codex

Le candidat a backtester en phase suivante est **T1-03 - Futures mean reversion intraday MES/MGC**.

Pourquoi celui-la:

1. Il evite le piege QMJ: pas de fundamentals restated, pas de PIT vendor, pas de survivorship single-stock.
2. Il utilise IBKR futures deja disponible, sans nouveau KYC et sans PDT.
3. Il cible directement le plus gros gap WP-04: 0% intraday et 86% de jours idle.
4. Son proxy synthetique passe les hard gates marginal score avec corr quasi nulle vs CAM/GOR/BTC.
5. L'edge est moins "joli academiquement" que PEAD, mais les donnees/execution sont beaucoup plus verifiables gratuitement.
6. T1-01 a un meilleur score quant brut, mais l'acces Binance perps/funding carry est un blocker operationnel non resolu.
7. T1-04 est facile mais trop expose au data-mining calendar.
8. T1-02 ne doit pas etre pousse avant une justification ROI data payante; QMJ vient de rappeler que les facteurs US sans vraie data peuvent etre des mirages.

Decision proposee:

- Phase suivante: backtest event-driven intraday **T1-03 MES/MGC mean reversion**, avec peu de variantes, stops obligatoires, filtres calendrier macro, et couts IBKR/ticks.
- T1-01: lancer seulement une micro-etude d'accessibilite broker/data perps en parallele, sans coder la strategie.

## Sources

- Binance API funding history: https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History
- Makarov & Schoar crypto arbitrage summary: https://rpc.cfainstitute.org/research/cfa-digest/2020/10/dig-v50-n10-4
- Liu, Tsyvinski & Wu crypto factors: https://www.nber.org/papers/w25882
- Lehmann short-term reversal: https://www.nber.org/papers/w2533
- Bernard & Thomas PEAD citation: https://www.scirp.org/reference/referencespapers?referenceid=4123617
- Livnat revenue surprise PEAD paper: https://pages.stern.nyu.edu/~jlivnat/drift%20revenue%20and%20earnings.pdf
- McConnell & Xu turn-of-month: https://docs.lib.purdue.edu/ciberwp/43/
- Norgate US stock package pricing/coverage: https://norgatedata.com/stockmarketpackages.php
- IBKR historical data limitations: https://www.interactivebrokers.com/campus/ibkr-api-page/twsapi-doc/
