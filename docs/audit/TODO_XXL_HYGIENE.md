# TODO XXL — Hygiene Globale (directive user 2026-04-19)

**As of** : 2026-04-19T~15:00Z
**Mandat** : desk perso live, live ASAP, ROC eleve. L'hygiene sert au live, pas a faire joli.
**Version canonique** de la TODO recue du user. Toute execution iterative reference ce document.

---

## Principe directeur

L'hygiene ne sert pas a "faire joli". Elle sert a :
- eviter les faux signaux
- reduire les bugs silencieux
- fiabiliser le live
- rendre le capital mieux utilisable
- accelerer les decisions de promotion/retrait

## Regle d'execution

Rien de purement cosmetique tant qu'un point d'hygiene a un impact sur :
- live trading
- promotion de strategie
- verite runtime
- capital occupancy
- PnL / ROC
- recovery / incident response

---

## H0. Gel et baseline
- Geler toute nouvelle strategie live tant que la TODO n'est pas fermee.
- Geler tout nouveau "grand refactor" non lie a la verite runtime.
- Produire un baseline unique : pytest + runtime_audit --strict + alpaca_go_25k_gate + git status --short.
- Creer un rapport canonique `docs/audit/hygiene_baseline.md`.

## H1. Hygiene du worktree
- Reduire drastiquement le worktree sale.
- Classer tout en 4 buckets : tracked legit / untracked research utile / generated artifacts / trash.
- Deplacer artefacts generes hors zones ambigues.
- Sortir brouillons de recherche non canoniques des dossiers critiques.
- Convention stricte : `docs/audit/`, `docs/research/`, `reports/research/`, `data/state/`, `data/incidents/`, `data/reconciliation/`, `temp/`.
- Rendre impossible qu'un fichier "temp" soit confondu avec une source de verite.

## H2. Hygiene des tests
- Cartographier : actifs / legacy / archives / skip toleres.
- Reduire skip lies a strats supprimees ou archivees.
- Doctrine : module mort => test archive, compat explicite tague.
- Produire `docs/audit/test_hygiene_map.md`.
- Tableau : business-critiques / live-critiques / recherche / archives.
- Re-mesurer couverture si encore citee dans docs.

## H3. Hygiene des registries et de la verite canonique
- Verifier coherence : live_whitelist.yaml / books_registry.yaml / quant_registry.yaml / health_registry.yaml.
- Supprimer commentaires obsoletes ou narratif vivant dans YAML.
- Deplacer snapshots operatoires hors configs.
- Interdire contradictions ("11 live crypto" si whitelist dit 0).
- Matrice canonique : AUTHORIZED / READY / ACTIVE / PROMOTABLE / DISABLED / ARCHIVED.
- Produire `docs/audit/canonical_truth_map.md`.

## H4. Hygiene des strategies
- Inventaire complet : live_core / live_probation / paper_only / research_only / disabled / archived.
- Exiger par strat : id canonique / book canonique / WF artefact canonique / statut / paper date / kill criteria structures.
- Supprimer double langage doc/whitelist/runtime.
- Produire `docs/audit/strategy_inventory_clean.md`.

## H5. Hygiene des donnees et etats
- Lister etats critiques : equity_state / positions_live / paper_journal / incidents / reconciliation / dd_state.
- Verifier par etat : chemin / producteur / consommateur / tolerance absence / criticite live.
- Produire `docs/audit/state_file_contracts.md`.
- Clarifier : attendu manquant en local vs anormal manquant sur VPS.
- Nettoyer chemins legacy ou dupliques.

## H6. Hygiene runtime / ops
- Separation stricte : verite repo local / VPS / cible.
- Formaliser : VPS prime pour decisions business.
- Verifier scripts verite : runtime_audit.py / live_pnl_tracker.py / alpaca_go_25k_gate.py.
- Tableau "source of truth" dans chaque doc pilotant une decision.
- Verifier cron reellement en place : futures refresh / crypto refresh / paper cycles / reconciliation / health checks.
- Produire `docs/audit/runtime_hygiene_matrix.md`.

## H7. Hygiene PnL / ROC / capital usage
- Definitions distinctes : total / deployable / allocated / used / at risk.
- Metriques distinctes : PnL net / ROC / occupancy / contribution marginale.
- Refuser tout doc qui melange occupation et performance.
- Reporting canonique hebdo : book / strat / live vs paper.
- Produire `docs/audit/roc_reporting_contract.md`.

## H8. Hygiene docs d'audit
- Reviser tous les docs avec scores : date / environnement / sources / formule.
- Interdire arrondis optimistes.
- Ajouter section historical context dans chaque doc vivant.
- Produire `docs/audit/scoring_policy.md`.

## H9. Hygiene securite / exploitation
- Verifier secrets / .env / chemins sensibles / logs verbeux.
- Verifier logs ne doublonnent pas.
- Verifier incidents critiques bien ecrits.
- Verifier scripts live n'ecrivent pas dans chemins ambigus.
- Produire `docs/audit/ops_hygiene_checklist.md`.

## H10. Hygiene de decision
- Une page unique repondant a : qu'est-ce qui trade / bloque / promouvable / n'apporte pas de ROC.
- Point d'entree operateur.
- Produire `docs/audit/desk_operating_truth.md`.

---

## Livrables minimum

- `docs/audit/hygiene_baseline.md` (H0)
- `docs/audit/test_hygiene_map.md` (H2)
- `docs/audit/canonical_truth_map.md` (H3)
- `docs/audit/strategy_inventory_clean.md` (H4)
- `docs/audit/state_file_contracts.md` (H5)
- `docs/audit/runtime_hygiene_matrix.md` (H6)
- `docs/audit/roc_reporting_contract.md` (H7)
- `docs/audit/scoring_policy.md` (H8)
- `docs/audit/ops_hygiene_checklist.md` (H9)
- `docs/audit/desk_operating_truth.md` (H10)

---

## Definition of Done

- Repo ne melange plus runtime / recherche / archives / bruit.
- Tests actifs testent des choses reellement vivantes.
- Docs ne contredisent plus le runtime.
- Statuts strat/books univoques.
- Scripts de verite racontent tous la meme histoire.
- Lecteur sait en 2 minutes : ce qui trade / ce qui ne trade pas / pourquoi / quoi corriger en premier pour ROC.

---

## Priorite d'execution (user-mandated)

1. H1 worktree
2. H2 tests
3. H3 registries
4. H5 data/state
5. H6 runtime/ops
6. H7 PnL/ROC/capital
7. H8 docs/scoring
8. H4 inventory final (apres 1-3-5-6)
9. H9 securite/ops
10. H10 operating truth (synthese finale)
