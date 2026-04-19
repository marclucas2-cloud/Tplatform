# Coverage baseline — G3 iteration 1 (2026-04-19)

## Command

```
python -m pytest tests/ --ignore=tests/_archive --cov=core --cov=scripts --cov-report=term
```

## Results

| Scope | Coverage |
|---|---|
| **Full repo** (core + scripts) | **40%** |
| `core/` production code | **65%** |
| Critical path (governance + execution + kill_switch + preflight) | **72%** |
| `scripts/` (utility + one-shot WF) | low par design (many 0% one-shot) |

## Why "full 40%" is not alarming

`scripts/` contient beaucoup de utility one-shot (`scripts/wf_*.py`, `scripts/backtest_*.py`,
`scripts/run_*.py`) qui sont **lances manuellement**, jamais importes en production.
Couvrir ces scripts par des tests n'a pas de valeur defensive proportionnee a l'effort.

Le ratio pertinent pour qualite production est **core/ 65%**, et **critical path 72%**.

## Top 10 modules core/ les moins couverts (candidates futures)

| Module | Coverage | Priorite |
|---|---|---|
| core/governance/auto_demote.py | 0% | Medium (pas sur hot path) |
| core/governance/daily_summary.py | 0% | Low (reporting) |
| core/governance/registry_loader.py | 0% | **High** (chemin boot) |
| core/worker/cycles/futures_runner.py | 2% | **High** (1176 LOC extrait recent) |
| core/worker/cycles/paper_cycles.py | 7% | Medium (extrait recent) |
| core/worker/heartbeat.py | 10% | Medium |
| core/worker/alerts.py | 25% | **High** (alert path critical) |
| core/governance/safety_mode_flag.py | 38% | Medium |
| core/governance/kill_switches_scoped.py | 40% | **High** (kill switch) |
| core/worker/health.py | 27% | Medium |

## Seuil CI propose

pyproject.toml addopts : pas de seuil coverage-fail-under en CI pour l'instant
(trop agressif sur la codebase actuelle, risque de bloquer PRs legitimes).

Plan future (post-9.5) : seuil 60% sur `core/` quand futures_runner + heartbeat
modules auront tests dediés.

## G3 iteration 1 — DONE

- [x] coverage installe (coverage 7.13 + pytest-cov 7.1)
- [x] Run baseline complete: 3667 passed, coverage core=65%, critical=72%
- [x] Baseline documentee
- [ ] Seuil CI pas active (volontairement, voir ci-dessus)

Critère DoD 9.5 partiel rempli : coverage mesurable, gap modules identifies.
