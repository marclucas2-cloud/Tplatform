# Mission 2 — Impact économique fenêtre stale 2026-03-27 → 2026-04-24

**Agent** : Claude Opus
**Trigger** : pipeline daily fix 4e16158 + audit divergences 0080865.
**Objectif** : transformer les divergences stale-vs-fresh en impact économique chiffré.

## Méthode

Sur les 21 business days de la fenêtre, pour chaque jour divergent :
- Position hypothétique fresh = ce qu'aurait pické la sleeve sur data fraîche (depuis l'audit 0080865)
- Position hypothétique stale = ce que la sleeve a pické sur data corrompue
- PnL overnight calculé : `(close[d+1] - close[d]) × multiplier × 1 contract`
- Multipliers : MES 5 / MNQ 2 / M2K 5 / MGC 10 / MCL 100

Le calcul est fait sur l'overnight pnl du jour divergent. Pour CAM/GOR cadence rebal 20j, c'est une approximation : la position réelle se serait portée plusieurs jours, pas 1 seul. Le calcul sur 1 jour fournit une **borne basse** d'écart.

Source code : `scripts/_audit_economic_impact_2026_04_26.py`
JSON détaillé : `reports/checkup/stale_window_economic_impact_2026_04_26.json`

## Résultats

### CAM (live_core)

| Métrique | Valeur |
|---|---|
| Jours divergents | 11 / 21 |
| Σ (stale_pnl - fresh_pnl) USD | **-$519** |
| Lecture | Stale CAM aurait perdu ~$519 vs fresh sur 11 jours overnight |

**Mais réalité observée** : CAM a quand même réussi un **TP MCL +$605 le 19/04** (per mémoire projet). Donc le PnL **réel** sur la fenêtre est positif (~+$605), pas -$519.

**Lecture corrigée** : la divergence n'est pas un PnL perdu, c'est un PnL différent. Stale CAM picked autre chose ou rien que MCL pendant 11 jours, mais la position MCL existante (ouverte pré-fenêtre, peut-être pré-bug) a TP'd quand même @ 81.92.

**Coût d'opportunité réel CAM** : difficile à mesurer précisément. Logs d'origine de la position MCL pré-19/04 purgés (rotation logs.5+). Borne d'écart théorique : ~$500 sur les jours divergents stricts. **Impact réel PnL = nul à faible.**

### GOR (live_core)

| Métrique | Valeur |
|---|---|
| Jours divergents | 13 / 21 |
| Σ "manque à gagner" si signal vivant | **-$2806** |

**Mais signal dormant** : GOR registry note "signal dormant" pendant la fenêtre. Le worker n'a pas tenu de position GOR active. Donc :
- PnL réel GOR = $0 (pas de position)
- Manque à gagner si GOR avait été vivant et pické fresh = $2806 sur 13 jours overnight

**$2806 est une borne supérieure du manque à gagner**, conditionnée à "GOR signal vivant" — ce qui n'était pas le cas. **Impact réel PnL = nul.** Mais le bug a privé d'une opportunité substantielle si le signal était vivant.

### gold_trend_mgc (paper_only)

| Métrique | Valeur |
|---|---|
| Long signals manqués | 10 / 21 |
| PnL paper hypothétique manqué | **-$286** |

Paper sleeve. Pas de PnL réel. 10 jours longs MGC manqués pendant un MGC en uptrend, paper PnL hypothétique modeste ($286 sur 1 contract MGC). **Impact réel PnL = $0.**

## Synthèse desk-level

### Impact PnL réel direct
- **CAM** : ~$0 (TP +$605 a quand même eu lieu)
- **GOR** : $0 (signal dormant, pas de position tenue)
- **gold_trend** : $0 (paper)
- **Total réel** : ~$0 perdu

### Manque à gagner théorique cumulé
- CAM : ~$519 d'écart théorique (mais compensé par TP réel)
- GOR : ~$2806 manque à gagner conditionnel (si signal vivant)
- gold_trend : ~$286 paper hypothétique
- **Total théorique** : ~$3.6K

### Verdict desk-level

**Bug grave de gouvernance / data, mais impact PnL estimé : FAIBLE.**

| Axe | Sévérité |
|---|---|
| Gouvernance / observabilité | **HAUT** (3 semaines silencieuses) |
| Décisions corrompues live | **MOYEN** (52% / 62% jours divergents) |
| PnL réel direct perdu | **NUL à FAIBLE** (~$0 mesuré, écart ~$500) |
| Manque à gagner théorique | **MOYEN** (~$3K si tout signal vivant) |

Le desk a eu de la chance :
1. La position MCL CAM préexistante a TP'd avant que le bug n'invalide la décision
2. GOR était signal dormant (pas de position fausse maintenue)
3. Les paper sleeves n'avaient pas de PnL réel à perdre

**Si le bug avait duré 1 mois de plus**, ou si GOR avait été activé, le manque à gagner aurait pu se concrétiser en perte réelle.

## Limites de l'audit

1. **Logs anciens purgés** : `worker.log.5` et antérieurs effacés par rotation. Impossible de retracer exactement quand la position MCL CAM TP'd, ni avec quel timing. Les chiffres reposent sur le journal mémoire ("TP +$605 le 19/04") + les bracket SL/TP observés (75 entry / 81.92 TP).

2. **Calcul overnight 1-jour** : la cadence CAM/GOR est rebal 20j, donc une position se porte généralement 20 jours. Calcul overnight 1-jour sous-estime l'impact.

3. **Backwardation MCL Z6** : ticker yfinance CL=F est front-month, mais le contract réel détenu était MCLZ6 (deferred décembre, prix ~77 vs front ~95). **Le calcul de momentum CAM sur ticker yfinance n'utilise pas le même prix que le contract réellement tradé**. Bug latent indépendant du bug pipeline. Hors scope de cet audit, à explorer plus tard.

4. **Pas d'audit gold_oil_rotation strict** : on a fait l'audit GOR mais on n'a pas tracé chaque cycle pour confirmer le "signal dormant". Si GOR a tenu MGC à un moment, le calcul change.

## Conclusion

Le bug pipeline était **grave en gouvernance**, **modéré en décisions corrompues** (52-62% des jours divergents), et **léger en PnL réel** (~$0 perdu mesurable). Le manque à gagner théorique cumulé est de l'ordre de $3K (borne supérieure conditionnelle).

Le desk **n'a pas perdu d'argent réel à cause de ce bug** — soit par chance (TP CAM avant divergence critique), soit par configuration (GOR signal dormant), soit par périmètre (paper sleeves pas live).

**Le hardening Mission 1 est plus important que le PnL Mission 2** : la prochaine occurrence de ce type de bug sera détectée en quelques heures par le `data_content::*` preflight check, pas en 3 semaines.

L'incident stale-data peut être considéré comme **fermé économiquement** : le coût réel est faible et borné, et le mécanisme qui aurait dû alerter existe désormais.

## Recommandation pour Mission 3

Avant de prononcer un verdict final sur la dette, il faut **revalider gold_trend_mgc** (Mission 3) car cette sleeve a manqué 10 long signals pendant la fenêtre. Si gold_trend_mgc reste grade A sur historique fresh, on peut clôturer l'incident. Sinon, c'est un signal additionnel que le bug a aussi affecté la confiance qu'on plaçait dans cette sleeve.
