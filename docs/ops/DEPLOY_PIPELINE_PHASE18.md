# Deploy pipeline + canary + rollback (Phase 18 XXL plan, 2026-04-19)

## Workflow standard

```bash
# Sur VPS Hetzner:
ssh root@178.104.125.74
cd /opt/trading-platform

# Deploy avec auto-rollback
./scripts/deploy.sh
```

## Etapes deploy.sh (post-Phase 18 hardening)

1. Tag `rollback-YYYYMMDD-HHMMSS` -> point de retour automatique
2. `git pull origin main`
3. `pytest tests/ -x -q --timeout=300` -> rollback si echec
4. `pre_deploy_check.py` -> warning seulement
5. Deploy vers shadow service si configure, sinon directement live
6. **Health check immediat** -> auto-rollback si echec (Phase 18)
7. **Canary monitoring 60s** (3 ticks 20s) -> auto-rollback si health echoue (Phase 18)
8. Done -> output rollback + promote commands

## Rollback manuel

```bash
# Liste les tags rollback dispo
git tag -l "rollback-*" --sort=-version:refname | head -5

# Rollback vers le dernier tag (ou explicite)
./scripts/deploy.sh --rollback
./scripts/deploy.sh --rollback rollback-20260419-100530
```

## Promote shadow -> live

Si shadow service configure et test OK :
```bash
./scripts/deploy.sh --promote
```

## Health endpoint requis

Worker doit exposer `http://localhost:8080/health` qui retourne 200 si:
- Worker process alive
- Heartbeat write recent (< 5 min)
- Pas de kill switch CRITICAL bloquant

## Score post-Phase 18

- Deploy script existence: **9/10** (rollback tags + workflow)
- Auto-rollback sur health fail: **9/10** (Phase 18 add)
- Canary monitoring window: **9/10** (3 ticks 60s, Phase 18)
- Rollback tested: **5/10** (jamais teste en conditions reelles, recommande drill)
- Shadow service config: **5/10** (mentionne mais pas de doc setup)
