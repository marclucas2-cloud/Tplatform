# POL-EVENT-FLAG — Policy "event binaire actif" pour stratégies live

**Créé** : 2026-04-19 (session MCL gap géopolitique Iran)
**Owner** : Marc
**Priorité** : P2 (policy, pas hotfix)
**Status** : OPEN — design avant code

## Contexte déclencheur

Dimanche 19 avril 2026 : position CAM long MCL à +$295, exposée à un event
binaire prévu mercredi 22 avril (expiration ceasefire Israël-Iran, reprise
possible des frappes si pas d'accord à Islamabad).

La stratégie CAM est un momentum cross-asset. Elle **n'intègre pas** le risque
d'event géopolitique binaire dans son signal. Tenir la position au-delà du TP
sur cet event = sortir du edge CAM (WF grade A) pour faire un pari non-modélisé.

Question : **une stratégie live doit-elle se flatter automatiquement avant un
event binaire identifié, ou reste-t-elle agnostique et laisse son TP/SL gérer ?**

## Question à trancher

Pour chaque stratégie live_core, définir la **posture event-driven** :

### Option A — Agnostique (status quo)
- La stratégie ignore les events macro
- Le TP/SL gère le risque
- Avantage : simplicité, pas de discrétion
- Inconvénient : sur un gap event, manque à gagner ou exposition à un move adverse violent

### Option B — Flat before event
- Liste curée d'events binaires (FOMC, expiration ceasefire, élection, OPEC)
- Stratégie flat automatiquement T-1 de l'event, re-entry possible T+1 si signal
- Avantage : immunise contre les gaps adverses
- Inconvénient : rate les gaps favorables, over-fit possible sur la liste

### Option C — Event-aware sizing
- Stratégie réduit size (×0.5) pendant fenêtre event
- Ne flatte pas complètement, garde exposition partielle
- Avantage : compromis risque/opportunité
- Inconvénient : complexité, paramètre de plus à tuner

### Option D — Override opérateur avec trace
- La stratégie reste agnostique en temps normal
- Opérateur peut déclarer un event flag dans `config/event_calendar.yaml`
- Pendant event actif, `pre_order_guard` bloque les nouvelles entrées ET/OU
  force un flat sur positions existantes
- Audit trail via JSONL incidents ([core/monitoring/incident_report.py](../../core/monitoring/incident_report.py))
- Avantage : discipline + flexibilité tracée
- Inconvénient : zone grise entre discrétion et systématique

## Inventaire des events binaires à traiter

Sans hiérarchiser, pour alimenter la discussion :

- **Macro scheduled** : FOMC, ECB, BoJ, OPEC meetings, NFP, CPI
- **Géopolitique** : expiration ceasefire, élections majeures (US, UE), referendums
- **Corporate** : earnings (moins critique sur futures)
- **Crypto** : halvings, hard forks, changements réglementaires (MiCA, SEC rulings)

Décision : quelles catégories forcent un event flag ? **User decision pending**.

## Critères de validation (DoD)

- [ ] Décision documentée : A / B / C / D pour chaque catégorie de stratégie
  (futures / FX / crypto)
- [ ] Si Option B/C/D retenue :
  - [ ] Schéma `config/event_calendar.yaml` (fields : date, type, impact, strats affectées)
  - [ ] Intégration dans `core/pre_order_guard` (6ème check existant + 1 nouveau ?)
  - [ ] Policy flat/reduce implémentée dans `core/worker/cycle_runner.py`
  - [ ] Test de bout-en-bout : event flag activé → order bloqué + position flat
  - [ ] Audit trail JSONL complet (qui a déclaré l'event, quand, quelles strats touchées)
- [ ] Documenté dans `docs/audit/ops_hygiene_checklist.md` (opérateur ajoute event T-2)
- [ ] Backtest : policy appliquée sur 10Y historique, calcul du coût (manque à gagner
  net des gaps adverses évités)

## Non-objectifs

- **Pas** un système d'override permanent (c'est de la discrétion cachée)
- **Pas** un news sentiment LLM (trop de bruit, pas reproductible)
- **Pas** un calendrier event auto-fetched depuis une API externe (trop fragile,
  dépendance critique)

## Recommandation à challenger

**Option D (override opérateur avec trace)** semble le bon compromis :
- Reste systématique 99% du temps (stratégie ignore events)
- Permet à l'opérateur de dire "cet event-ci a un risque asymétrique non-modélisé,
  je flag T-1" sans toucher le code
- Audit trail complet : la décision est traçable, pas de "théâtre discrétionnaire"
- Compatible avec la doctrine user : [feedback_decision_authority.md](../../C:/Users/barqu/.claude/projects/c--Users-barqu-trading-platform/memory/feedback_decision_authority.md)
  (AI propose, user décide)

**Mais** : risque de dérive si l'opérateur flag trop souvent. Garde-fou nécessaire :
max N flags par mois, review trimestrielle du coût opportunity des flags.

## Fichiers touchés (prévisionnel)

- `config/event_calendar.yaml` (nouveau)
- `core/pre_order_guard.py` (ajout check event_flag_active)
- `core/worker/cycle_runner.py` (hook flat on event activation)
- `core/monitoring/incident_report.py` (log event flag transitions)
- `docs/audit/ops_hygiene_checklist.md` (operator playbook)
- `tests/core/test_pre_order_guard.py` (cas event)

## Risques

- **Sur-utilisation** : opérateur flag à chaque inquiétude, tue l'alpha
- **Sous-utilisation** : opérateur oublie le flag, protection inutile
- **Ambiguïté** : "event binaire" n'est pas défini formellement, zone grise
- **Précédent** : une fois le flag en place, tentation d'aller vers override
  ad-hoc pas traçable

## Références

- Trigger : weekend Iran 18-19 avril 2026, ceasefire expire mercredi 22 avril
- PO verdict NO-GO override manuel : [docs/todos/TICKET_FEAT_CAM_TRAIL.md](TICKET_FEAT_CAM_TRAIL.md)
- Doctrine : [feedback_step_up.md](../../C:/Users/barqu/.claude/projects/c--Users-barqu-trading-platform/memory/feedback_step_up.md)
  ("pas de théâtre") et [feedback_prove_profitability_first.md](../../C:/Users/barqu/.claude/projects/c--Users-barqu-trading-platform/memory/feedback_prove_profitability_first.md)
  ("pas d'over-engineering sur $20K")
- Live hardening existant : [project_live_hardening_p0_p1.md](../../C:/Users/barqu/.claude/projects/c--Users-barqu-trading-platform/memory/project_live_hardening_p0_p1.md)
