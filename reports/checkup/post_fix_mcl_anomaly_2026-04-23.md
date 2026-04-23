# Post-fix MCL — Anomalie detectee wake-up 14h30 UTC 2026-04-23

## Verdict synthetique

**3/6 propre, 1/6 anomalie reelle, 2/6 OK avec caveat.**

Le fix MCL contract resolution (commit `1217acf`) fonctionne : 0 occurrence "no contract details for MCL" aujourd'hui. CAM a fire BUY MCL sur LIVE et PAPER cycles. Le contract resolve bien via NYMEX (conId=661016519, localSymbol=MCLZ6, multiplier=100).

**MAIS** : Error IBKR 10349 "Order TIF was set to DAY based on order preset" est apparue sur les DEUX ordres CAM (paper + live) ce matin. Le paper a ete resubmitted auto et FILLED sur DUP573894 (compte paper ghost). Le LIVE a ete cancelled definitivement sur U25023333. Resultat : U25023333 canonical live ne detient aucune position MCL malgre le fire CAM, et DUP573894 paper ghost a +1 MCL @ 77.31 avec bracket SL 56.28 / TP 109.49 actifs.

## Detail des 6 checks

| # | Check | Statut |
|---|-------|--------|
| 1 | Cycle futures 14h UTC execute | ✅ FUTURES PAPER 14:00:24, FUTURES LIVE 14:00:38 |
| 2 | "no contract details for MCL" disappeared | ✅ 0 occurrences 23/04 (9 historiques pre-fix) |
| 3 | CAM fire + resolve | ⚠️ Fire OK, resolve OK, mais Error 10349 cancel LIVE order |
| 4 | Legacy strats absentes | ✅ 0 lignes legacy, volume log 152 vs 255 hier (-40%) |
| 5 | GOR + mcl_overnight MCL free | ⚠️ SKIP "CAM reserved MCL" (coherent car CAM a fire aujourd'hui) |
| 6 | DUP573894 positions | ⚠️ +1 MCL @77.31 nouveau sur DUP573894 ; U25023333 canonical vide |

## Anomalie reelle (Check 3) — Error IBKR 10349 TIF preset

### Sequence PAPER cycle (clientId=74)

```
14:00:27.938  worker: Cross-Asset Mom (PAPER): BUY MCL
14:00:28.099  placeOrder MarketOrder MCL BUY 1 -> PendingSubmit
14:00:32.099  [ERROR] Error 10349: Order TIF was set to DAY based on order preset
14:00:32.100  Canceled order
14:00:32.101  orderStatus: Submitted (resubmission auto)
14:00:32.102  Fill: 1.0 @ 77.31 sur DUP573894 (paper ghost account)
14:00:32.103  orderStatus: Filled (confirmation)
14:00:34.103  placeOrder StopOrder SL 56.28 (orderId=6, OCA group OCA_MCL_e19af239)
14:00:35.104  placeOrder LimitOrder TP 109.49 (orderId=7)
14:00:37.106  SL PreSubmitted (whyHeld=trigger)
14:00:37.109  TP Submitted
```

Position IB finale : DUP573894 MCL +1 @ avgCost 77.31, bracket OCA actif.

### Sequence LIVE cycle (clientId=78)

```
14:00:41.824  worker: Cross-Asset Mom (LIVE): BUY MCL
14:00:42.076  placeOrder MarketOrder MCL BUY 1 -> PendingSubmit
14:00:46.076  [ERROR] Error 10349: Order TIF was set to DAY based on order preset
14:00:46.077  Canceled order
14:00:46.078  orderStatus: PendingCancel
14:00:46.079  orderStatus: Cancelled (status=Cancelled, filled=0)
14:00:48.079  worker: Cross-Asset Mom: entry not filled (Cancelled) — cancelling
```

Position IB finale : U25023333 canonical MCL qty=0 (inchange depuis TP 19/04).

### Asymetrie observee

Meme erreur 10349 sur les deux cycles, comportement post-error different :
- PAPER (clientId=74) : resubmitted automatiquement par IB Gateway -> Filled.
- LIVE (clientId=78) : cancel definitif, pas de resubmission.

L'erreur 10349 indique que l'order preset IBKR rewrite le TIF a DAY. Possible explication :
- Le paper account DUP573894 accepte le rewrite et execute.
- Le canonical U25023333 a un preset plus strict qui cancel l'ordre modifie.

OU bien : la resubmission automatique est une feature IB Gateway cote paper qui n'est pas replique cote live.

### Conditions requises pour retry CAM demain

CAM est affecte du bug fresh-instance (pas de persistance `_last_rebal_ts`), donc elle va refire BUY MCL chaque cycle 14h UTC tant que la position canonical U25023333 est vide. Consequences previsibles :
- Chaque cycle genere 2 ordres (paper + live) qui vont hit Error 10349.
- Paper continue a empiler des MCL sur DUP573894 (demain +2, apres +3, etc) - pollution paper ghost.
- Live reste vide car cancel persistant.
- Logs worker se remplissent de "entry not filled (Cancelled)" quotidien.

## Ce qui a marche (vraiment)

- **Fix 1217acf MCL contract** : contract resolve NYMEX/conId=661016519/MCLZ6 sans erreur.
- **CAM fire via get_top_pick + live capable bloc** : CAM a emit BUY MCL cote paper ET live.
- **Phase 3.5 cleanup 21cc040** : 0 ligne legacy dans les logs 23/04, volume divise par 1.7x.
- **btc_asia q80 live_micro** : cycle matin 8h30 UTC ok, `entry_skipped signal_side=NONE` car mes_sig -0.0018 < seuil 0.006. Strategy tourne proprement.
- **Service** : `trading-worker.service` active, `runtime_audit --strict` exit 0.

## Question ouverte pour Marc

**Comment resoudre Error 10349 cote live ?**

Trois pistes possibles (non validees, requieres intervention Marc) :

1. **Ajuster order preset cote IB Gateway canonical U25023333** : modifier le TIF default en MKT/IOC dans le preset IB workstation ou via API `OrderPreset.tif = "IOC"`. Si le preset permet, plus d'erreur 10349.

2. **Detection + retry cote core/broker/ibkr_bracket.py** : si Error 10349 detected, worker resubmit automatiquement avec tif=GTC ou tif=IOC explicit. Symmetrique avec le comportement paper.

3. **Changer de MarketOrder a LimitOrder** : fill au prix market avec limit price = last + N ticks. Probablement resiste au preset rewrite.

**Sous-question** : l'accumulation MCL sur DUP573894 est-elle acceptable en l'etat ? Le paper ghost account n'est pas tracke par le worker, mais il grossit avec chaque cycle. Fix latent `_last_rebal_ts` persistence limiterait le rate (~1/20j vs quotidien).

## Donnees utiles

- Positions IB U25023333 canonical : vides (pos.contract.symbol iter empty)
- Position DUP573894 : MCL +1 @ 77.31 avgCost 7731.77 (via `updatePortfolio` log)
- Unrealized PnL DUP573894 : -9.23 USD (MCL a 77.22 vs entry 77.31)
- Bracket DUP573894 : StopOrder permId=1225401853 aux=56.28 whyHeld=trigger + LimitOrder permId=1225401854 lmt=109.49 Submitted
- Runtime audit strict : exit 0 (pas de divergence critique detectee sur strats canoniques)
- 3816 tests (pas encore run post-checkup, mais matin a pass)

## Lien vers logs sources

- `/opt/trading-platform/logs/worker/worker.log` (section 2026-04-23 14:00:24..14:00:48 pour sequence complete)
- `/opt/trading-platform/data/state/btc_asia_mes_leadlag_q80_live_micro/_last_cycle.json` (bonus q80)

## Recommandation

**Ne pas agir en autonomie.** Decision Marc requise sur :
1. Choix piste 1/2/3 pour Error 10349
2. Decision sur bug persistance CAM `_last_rebal_ts` (latent depuis semaines, visible maintenant)
3. Netoyage DUP573894 bracket si considere parasite

Ce rapport ne propose pas de fix immediat — les 3 pistes touchent l'execution live et merite validation humaine.

---
Rapport genere par wake-up auto 2026-04-23 14h30 UTC (cron `fe904f8b`).
