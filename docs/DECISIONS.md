# DECISIONS — Trading Platform

Log des decisions techniques majeures + raison + date + impact.

## 2026-04-19 — Phase XXL plan (this session)

### D-001: DD baselines schema v1 + 4 BootState
**Decision**: Persist `_peak_equity` + period baselines to disk via atomic write.
Boot classifies state as FIRST_BOOT / STATE_RESTORED / STATE_STALE / STATE_CORRUPT.
Peak NEVER reset on STATE_RESTORED.

**Why**: Pre-fix `_baselines_synced` flag in-memory only -> reboot in DD lost peak,
kill switch silent on real cumulative DD (cf feedback_baselines_persistence_bug.md).

**Impact**: Phase 1 commit. 145 crypto-risk tests PASS, 0 regression.

### D-002: OrderTracker persistance + crash recovery
**Decision**: OrderTracker accepte `state_path` parametre. Tous les transitions
(create/validate/submit/fill/...) trigger atomic save. Boot reload + recovery_summary.

**Why**: Pre-fix in-memory only -> crash -> orphan orders broker sans record interne ->
recovery impossible.

**Impact**: Phase 3 commit. 11 nouveaux tests recovery e2e.

### D-003: 7 checks pre_order_guard (au lieu de "6")
**Decision**: pre_order_guard implemente 7 checks (book, mode_authorized, live_allowed,
whitelist, safety_mode, kill_switches_scoped, book_health), pas 6 comme indique
historiquement.

**Why**: Audit Phase 5 a revele 7 checks effectifs vs 6 documentes. Mise a jour CLAUDE.md.

### D-004: WF canonical schema v1 + verdict rule
**Decision**: VALIDATED iff windows_pass / windows_total >= 50% AND median Sharpe > 0.
INSUFFICIENT_TRADES si moins de 3 windows non-insufficient.

**Why**: 20+ scripts wf_*.py existaient avec verdict ad-hoc. wf_crypto_all.py etait
buggy (B&H-adjusted). Phase 9 -> module canonique avec env_capture pour reproducibility.

### D-005: Spot/earn transfer detection threshold
**Decision**: dd_equity drops >3% mais total_equity stable <2% -> rebaseline (peak inclus).
Sanity threshold 3.0x (vs ancien 1.30x trop aggressif).

**Why**: Phase 1 introduce sanity threshold 3.0x avec proper persistence + period anchors.
1.30x produisait faux positifs (cf P0.2 audit 18 avril).

### D-006: Archive 21 strats + 5 tests dead code
**Decision**: Move via git mv vers strategies/_archive/ et tests/_archive/. Pas delete.

**Why**: 21 strats sans aucun import production code (incluant 9 FX disabled per ESMA).
Phase 8 -> reduce cognitive load tout en preservant history pour restoration.

### D-007: Promotion gate formelle 5 checks
**Decision**: paper -> live_probation requires:
1. age_paper_days >= 30
2. paper_journal >= 10 trades
3. wf_source declared
4. kill_switch_clean_24h
5. manual_greenlight (signed JSON token)

**Why**: Phase 7 -> arbitraire de promotions historiques, besoin de gate explicite.

## Decisions historiques pre-XXL (resume)

- **2026-04-15**: Risk budget 5% per futures strat (vs contract count) - cf
  project_risk_budget_framework.md
- **2026-04-16**: Phase 3.1 demote 3 binance strats (live_probation -> paper_only)
- **2026-04-17**: Live hardening P0/P1 (NAV fail-closed, preflight hard-fail,
  live_whitelist canonique, book health, audit trail)
- **2026-04-18**: P0.2 re-WF event-driven, demote 7 binance strats REJECTED
  + governance fail-closed binance/alpaca
- **2026-04-18**: PO decision FX strictement hors scope IBKR (ESMA EU leverage limits)

## Format pour nouvelles decisions

```markdown
### D-NNN: <titre court>
**Decision**: <quoi>
**Why**: <pourquoi, contexte, contrainte>
**Impact**: <commits, tests, score change>
```
