# Scoring Policy — H8 T8

**As of** : 2026-04-19T16:35Z
**Phase** : H8 TODO XXL hygiene. Reproductibilite + anti-inflation, pas charte redactionnelle.
**Livrable** : ce document. Politique canonique. **Tout score publie dans les docs d'audit doit obeir**.

---

## 0. Principe directeur T8

> Tout score publie dans le repo doit etre **reproductible** par un tiers et **anti-inflation** par construction.
>
> Si un score ne peut pas etre recalcule par un tiers lisant les sources citees, il n'a pas sa place dans un doc canonique.
>
> Si une formule donne 8.28, **on ecrit 8.3, pas 8.5**. Jamais d'arrondi vers le haut "pour faire joli".

**Anti-principe** :
- "Score comite" flou sans methode.
- "Jugement expert" non-separe du calcul reproductible.
- "Score composite 9.5/10" melant plateforme + live + ROC.

---

## 1. Scope — quels scores sont soumis a cette politique ?

Cette politique s'applique **obligatoirement** a tout score numerique publie dans :
- `docs/audit/*.md`
- `docs/todos/*.md` (si contient score)
- `reports/**/*.md` publies / pushed
- `SYNTHESE_COMPLETE.md` et `CLAUDE.md` (root docs canoniques)
- Dashboard API (endpoints retournant score)
- Commits messages (si score cite)

**Hors scope** : notes privees, brouillons (`temp/`, `.claude/`). Mais des qu'un score passe dans un doc canonique, il doit etre conforme.

---

## 2. 4 axes de score canoniques (distincts, non cumulables)

Herite T3-fix2 + T5-T7. **Refus explicite** de faire une moyenne ponderee unique.

| Axe | Periode | Dimensions | Source principale |
|---|---|---|---|
| **plateforme** | snapshot | code qualite + gouvernance + tests + coverage + architecture | `runtime_audit --strict` + `pytest` + `coverage report` |
| **live readiness** | snapshot | live engine existant + diversification 30j + trade freq + capital occupancy + paper quality | `runtime_audit` VPS + `live_pnl_tracker` + VPS positions |
| **ROC / capital usage** | rolling 30d/90d | capital deployed lisible + occupancy observee + ROC mesurable par strat + contribution marginale | `live_pnl_tracker` + `capital_occupancy_report.py` (NON LIVRE) |
| **qualite livrables docs** | snapshot | alignement doc-runtime + coverage sources citees + distinction historique/courant | meta-review manuel + consistency review |

**Regle absolue** : un score ne peut pas porter sur plus d'un axe. "Score 9.5 plateforme+live+ROC" = **invalide**.

---

## 3. Contrat de publication par score — 7 champs obligatoires

Chaque score **doit** etre accompagne de ces 7 champs :

```markdown
**Score [axe] : X.Y / 10**
- **as_of** : 2026-04-19T15:45Z (timestamp UTC ISO 8601)
- **environment** : local | VPS | snapshot_vps_{date} | mixed (si mixed, expliciter)
- **sources** :
  - `[commande exacte reproductible]` → [exit_code + key metric]
  - `[autre source file path]`
- **formule** : breakdown explicite ponderation si score compose
- **rounding** : rule applied (section 4)
- **confidence** : high | med | low (section 6)
- **auteur** : humain ou agent (si agent, nom modele)
```

### Exemple valide

```markdown
**Score plateforme : 8.3 / 10**
- as_of : 2026-04-19T14:33Z
- environment : mixed (pytest local, runtime_audit VPS)
- sources :
  - `python -m pytest -q -o cache_dir=.pytest_cache --basetemp .pytest_tmp` → exit 0, 3669 pass/1 skipped
  - `python scripts/runtime_audit.py --strict` VPS → exit 0, 0 incoherences
  - `pytest --cov=core` → 65.2% overall core
- formule : 8 dimensions x ponderation section 2 ci-dessous
  - Tests 9.0 x 15% = 1.35
  - Runtime audit coherence 9.0 x 15% = 1.35
  - Gouvernance 9.5 x 15% = 1.43
  - Persistance 9.5 x 10% = 0.95
  - Observabilite 7.0 x 15% = 1.05
  - Architecture 8.5 x 10% = 0.85
  - Coverage 6.5 x 10% = 0.65
  - SPOFs 6.5 x 10% = 0.65
  Total brut = 8.28 / 10
- rounding : **8.28 → 8.3** (section 4 regle 1 decimale)
- confidence : high (sources toutes executables + re-run possible)
- auteur : Claude Opus 4.7 via iter3-fix2 audit
```

### Exemple invalide (rejete)

```markdown
**Score : 9.5/10** — bon niveau global.
```
→ violation 6/7 champs. Si ce score apparait dans un doc canonique, il doit etre **corrige ou retire**.

---

## 4. Regles de rounding — anti-inflation

### 4.1 Regle principale : 1 decimale, toujours banker's rounding vers le bas en cas d'ambigute

Exemples :
- `8.28` → **8.3** (arrondi mathematique classique, 0.28 > 0.25)
- `8.25` → **8.2** (banker's rounding — arrondi vers l'even pour les 0.X5)
- `8.24` → **8.2** (arrondi vers le bas)
- `8.45` → **8.4** (banker's rounding vers 4 even)
- `8.55` → **8.6** (banker's rounding vers 6 even)
- `8.34` → **8.3**
- `8.36` → **8.4**

**Code Python reference** :
```python
def round_score(raw: float) -> float:
    """Banker's rounding (round half to even), 1 decimal."""
    return round(raw, 1)
```

### 4.2 Interdiction : arrondi "vers le haut pour faire joli"

Exemples interdits :
- `8.28 → 8.5` : **FRAUDE**. Ecart 0.22 > 0.05 tolerance rounding.
- `7.48 → 8.0` : **FRAUDE**. Saut arbitraire.
- `6.52 → 7.0` : **FRAUDE**.

Si un doc publie un score qui ne correspond pas au resultat de `round(raw, 1)`, il est **invalide**.

### 4.3 Arrondi des composantes internes

Les composantes (ex : Tests 9.0, Coverage 6.5) sont elles-memes arrondies a 0.5 selon une echelle discrete :
- 10.0 = perfect (0 fail, 100% coverage, etc.)
- 9.5 = excellent (1-2 gaps mineurs)
- 9.0 = tres bien (quelques gaps identifies)
- 8.5 = bien (gaps mesurables mais non bloquants)
- 8.0 = correct (plusieurs gaps)
- 7.5 / 7.0 = acceptable, ameliorations necessaires
- 6.5 / 6.0 = fragile, action rapide
- < 6.0 = risque, refonte

Le **score final** ensuite agrege par ponderation puis arrondi 1 decimale (section 4.1).

### 4.4 Format publication

Toujours avec 1 decimale : `8.3 / 10` (pas `8` ni `8.30`).

**Jamais de score sans decimale** (interdit "9/10" seul). Exception : `10.0/10` qui reste `10.0`.

---

## 5. Interdictions explicites

### 5.1 Interdit : score sans source executable

Un score doit pointer vers des **commandes ou fichiers reproductibles**. Interdit :
- "9.5/10 apres review exhaustive du codebase" (pas reproductible)
- "7/10 selon expert consensus" (sans sources)
- "Excellent" (pas un nombre, mais si compose dans un total = interdit)

### 5.2 Interdit : melange des 4 axes

Interdit :
- "Score global 9.5/10" sans preciser lequel des 4 axes
- Moyenne ponderee des 4 axes en un seul nombre
- "Moteur score plateforme 9.5 → on est bien live" (confusion plateforme et live readiness)

### 5.3 Interdit : score historique reutilise sans date

Interdit :
- "Score 9.5 (claim iter2)" **sans** date iter2 = 2026-04-19T20:00Z explicite
- Score d'une iteration publie dans un doc courant sans annotation `(historical context)`

### 5.4 Interdit : score sans environnement

- "Runtime audit 8/10" sans preciser si local (FAIL attendu) ou VPS (prod verite)

### 5.5 Interdit : gonflage narratif

Exemples detectes (iter3-fix2) :
- "Paper signal quality : 9.0" alors que 1 seul strat produit un journal → reel ≤ 5.5
- "Coverage 72% critical" non re-mesure depuis iter1 → doute raisonnable
- "Trade frequency : excellent" alors que 0.1-0.2/jour vs cible 1/jour → reel 3.5-4.0

Tout score dont la justification est plus optimiste que les sources citees = **invalide**.

---

## 6. Confidence levels — echelle qualitative obligatoire

Tout score doit porter un `confidence` :

| Level | Definition | Action |
|---|---|---|
| **high** | Sources executables + re-run possible + verifiable par tiers | publication OK |
| **med** | Sources citees mais pas toutes re-runnable OU mesure datee | publication OK avec `(confidence: med)` explicite |
| **low** | Jugement expert OR source incertaine OR metrique derivee non instrumentee | publication **obligatoirement** avec `(confidence: low)` + justification |

### Exemples application

| Score | Confidence | Justification |
|---|---|---|
| Plateforme 8.3 | high | pytest + runtime_audit + coverage tous executables |
| Live readiness 5.5 | **med** | live_pnl_tracker "insufficient history", paper signal quality derive empirique |
| ROC / capital 4.0 | **med** | occupancy_report non livre, observation directe positions_live ok mais partielle |
| Docs 7.5 | high | cross-check structure doc + T8 consistency |

---

## 7. Distinction courant / historique / cible

Obligatoire dans tout doc de pilotage.

### Convention

- **Score courant** : sans annotation, implicite "as_of now"
- **Score historique** : obligatoire annotation `(historical iter2 2026-04-19T20:00Z)` ou equivalent
- **Score cible** : annotation `(target M3 2026-06-30, scenario conservative)`

### Exemple

```markdown
## Plateforme scoring history

- **Historical iter0** : 8.8 (2026-04-19T~16:00Z, ChatGPT audit debut session)
- **Historical iter2** : 9.5 (2026-04-19T~20:00Z, plan 9.5 completion claim — **plateforme seule**)
- **Current (post iter3-fix2)** : **8.3** (2026-04-19T14:33Z, re-calcul honnete avec coverage doute + preflight local FAIL distingue)
- **Target M3 conservative** : 8.8 (2026-06-30, post coverage core >= 80% + weekly_truth_review.py livre)
```

### Regle anti-confusion

- Si un doc cite "9.5/10" dans un texte d'audit **courant**, il doit etre taggue `(historical)` sinon il ment sur l'etat actuel.
- Le principe iter3-fix2 (`live_readiness_scoreboard.md`, `deep_audit_current.md`) a systematiquement applique cette regle.

---

## 8. Score expert vs score calcule

### 8.1 Score calcule (majorite des cas)

- Formule mathematique explicite
- Composantes ponderees
- Result arrondi 1 decimale
- Confidence high si sources executables

### 8.2 Score expert (exceptionnel, a marquer)

- Jugement humain ou agent sans formule unique
- Utilise quand donnees insuffisantes pour metrique calculable
- Obligation : preciser `(expert judgement)` + justification narrative
- Confidence **toujours** `low` ou `med`

### Exemple expert

```markdown
**Score SPOFs / Ops : 6.5 / 10** (expert judgement)
- as_of : 2026-04-19T14:33Z
- sources : constat solo-dev + VPS Hetzner unique + IBKR Gateway unique
- formule : **pas de formule unique** — jugement base sur :
  - scalability tolerant jusqu'a 10x capital (feedback_prove_profitability_first)
  - aucun outage VPS 30j+ (heartbeat log)
  - decision user "pas de redondance avant $75K"
- rounding : n/a (jugement direct)
- confidence : **low** (pas de metric calculable)
- auteur : humain PO + agent review
```

### Regle

**Un doc de pilotage qui contient un score expert** doit aussi contenir au moins **un score calcule** pour donner un ancrage reproductible. Interdit d'avoir un doc "100% expert".

---

## 9. Template publication canonique (copier-coller)

### 9.1 Template score calcule

```markdown
### Score [AXE] — [TIMESTAMP]

**Score : X.Y / 10**

| Field | Value |
|---|---|
| as_of | `YYYY-MM-DDTHH:MMZ` |
| environment | local / VPS / mixed |
| sources | executable commands + files cites |
| formule | breakdown weighted sum |
| rounding | banker's 1 decimal |
| confidence | high / med / low |
| auteur | [nom/modele] |

#### Breakdown

| Dimension | Note discrete | Ponderation | Contribution |
|---|---|---|---|
| ... | X.X | Y% | Z.ZZ |
| **TOTAL brut** | — | 100% | **A.AB** |
| **Score arrondi** | — | — | **A.B** |

#### Gaps / justification

(narrative concise)
```

### 9.2 Template score expert

```markdown
### Score [AXE] (expert judgement) — [TIMESTAMP]

**Score : X.Y / 10** (expert judgement, confidence low/med)

| Field | Value |
|---|---|
| as_of | ... |
| environment | ... |
| sources | **narrative uniquement** (pas de formule) |
| rounding | n/a (jugement direct) |
| confidence | **low / med obligatoire** |
| auteur | ... |

#### Justification narrative

Pourquoi pas de formule : ...
Jugement base sur : ...
Scenarios considered : ...
```

---

## 10. Verification retrospective — scores des 6 livrables actuels

Audit des scores publies post iter3-fix2 :

| Doc | Score cite | Respect politique T8 ? | Correction requise |
|---|---|---|---|
| `deep_audit_current.md` section 6 | Plateforme 8.5 / Live 5.5 / ROC 4.0 / Docs 7.5 | ✅ 4 axes distincts, pas de moyenne, breakdown present | AJOUT : as_of + confidence levels + formule explicite |
| `live_readiness_scoreboard.md` section 5 | Plateforme 8.5 / Live readiness 5.5 | ✅ distinction respectee | AJOUT : breakdown detaille + confidence |
| `roc_capital_usage.md` section 9 | ROC 4.0 | ✅ axe unique | AJOUT : sources executables + as_of |
| `deliverables_consistency_review.md` section 7 | Docs 7.5 (post iter3-fix2 vs 5.0 pre) | ✅ | AJOUT : confidence high |
| `hygiene_baseline.md` section 5 | 4 axes cites | ✅ | AJOUT : env + confidence |
| `test_hygiene_map.md` | Pas de score numerique (par design) | ✅ n/a | n/a |

**Verdict** : les scores courants publies respectent **la structure** (4 axes, distinction, sources). Il manque systematiquement **as_of + confidence + formule detaillee inline** dans 5 docs.

### Action T8-d

Pas de re-publier les scores maintenant (ligne rouge : pas de rewrite massif). Mais **tout nouveau score** apres 2026-04-19T16:35Z doit respecter strictement le template section 9.

**Recommandation** : lors du prochain audit (semaine 2026-04-27), re-publier chaque score **dans son doc** avec le template complet.

---

## 11. DoD — 4 questions implicites du mandat

### Q1 : Aucun score publie sans date ?

**Oui post T8**. Tout score publie apres 2026-04-19T16:35Z dans un doc canonique doit avoir `as_of`.

### Q2 : Aucun score publie sans environnement ?

**Oui post T8**. Distinction local / VPS / mixed obligatoire.

### Q3 : Aucun score publie sans preuves ?

**Oui post T8**. Section `sources` obligatoire avec commandes executables OU files cites.

### Q4 : Aucun score publie sans logique de calcul ?

**Oui post T8**. Formule breakdown pour scores calcules. Narrative explicite pour scores expert (separe).

### Q5 (bonus) : Si expert, marque comme tel, separe du calcule ?

**Oui post T8**. Section 8.2 + annotation `(expert judgement)` + confidence low/med obligatoire.

---

## 12. Application immediate — ligne d'evaluation du mandat

**Verification immediate** sur les 4 scores canoniques actuels :

### Plateforme 8.3 / 10 (arrondi banker's 8.28)
- as_of : 2026-04-19T14:33Z
- environment : mixed (pytest local 3669 pass + runtime_audit VPS exit 0 + coverage local 65.2%)
- sources : 3 commandes executables listees section 3
- formule : 8 dimensions x ponderation deep_audit_current.md
- rounding : 8.28 brute → **8.3**
- confidence : **high**
- auteur : agent review iter3-fix2

**Note importante** : le precedent claim "**8.5**" deep_audit_current.md etait une **violation rounding policy** (8.28 → 8.3, pas 8.5). La politique T8 correige.

### Live readiness 5.5 / 10
- as_of : 2026-04-19T14:33Z
- environment : VPS (observation positions + paper journals + live_pnl_tracker)
- sources : runtime_audit + VPS state + live_pnl_tracker "Insufficient history"
- formule : 6 dimensions ponderees (live engine 7.5/20%, diversif 5.5/20%, fail-open 9.5/10%, occupancy 3.0/20%, trade freq 3.5/15%, paper quality 5.5/15%)
- rounding : 5.50 → 5.5 (banker's 5.0)
- confidence : **med** (live_pnl historique 1j insuffisant, paper quality derive empirique)
- auteur : agent review iter3-fix2

### ROC / capital usage 4.0 / 10
- as_of : 2026-04-19T14:33Z
- environment : VPS (positions + equity observation)
- sources : positions_live.json + equity_state.json + live_pnl_tracker
- formule : 5 dimensions (capital lisible 7.0/20%, occupancy 3.0/25%, ROC par strat 3.5/20%, marginal 2.0/15%, alignement 5.5/20%)
- rounding : 4.00 → 4.0
- confidence : **med** (capital_occupancy_report.py non livre, mesures par strat partielles)
- auteur : agent review iter3-fix2

### Docs 7.5 / 10
- as_of : 2026-04-19T15:45Z (post T3)
- environment : local (meta-review T8)
- sources : deliverables_consistency_review.md section 7 + T8 re-audit
- formule : 7 criteres x ponderation egale (distinction local/VPS 9/10, doc-script coherence 9/10, sources citees 9/10, scores recalculables 8/10, honnetete incertitudes 9/10, actionabilite 9/10, historique preserve 9/10 = moyenne 8.9, pondere conservateur 7.5)
- rounding : conservateur 7.5 (au lieu 8.9 arrondi)
- confidence : **med** (meta-review subjective partielle)
- auteur : agent

**Note** : le score 7.5 conservatif (vs 8.9 calculable) est un cas de **`jugement expert superpose au calcule`** → devrait etre marque plus clairement. **Action T8** : re-evaluer si le score calcule (8.9) doit etre prefere au judgement expert (7.5). Decision : garder 7.5 avec flag `(expert override, explique par manque meta-review peer)` dans le prochain refresh.

---

## 13. Ligne rouge T8 respectee

- ✅ Reproductibilite : template 7 champs obligatoires
- ✅ Anti-inflation : rounding banker's explicite + interdiction arrondi "joli"
- ✅ Distinction 4 axes : pas de moyenne unique
- ✅ Courant / historique / cible explicites
- ✅ Score expert vs calcule separe (section 8)
- ✅ Interdictions explicites (section 5)
- ✅ Template canonique publiable (section 9)
- ✅ Audit retrospectif (section 10)
- ✅ Correction des claims passes (section 12 : "8.5" → "8.3" par banker's rounding)
- ✅ DoD 4 questions user + bonus repondues (section 11)

**Prochain** : T9 H9 ops hygiene checklist. Securite + logs + incidents + chemins sensibles.
