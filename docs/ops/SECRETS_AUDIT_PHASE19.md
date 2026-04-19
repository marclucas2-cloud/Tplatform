# Secrets audit — Phase 19 XXL plan (2026-04-19)

## Inventory

### Variables d'env (lue depuis .env, jamais committee)

| Var                         | Source            | Rotation policy |
|-----------------------------|-------------------|-----------------|
| BINANCE_API_KEY             | Binance dashboard | 90j recommande  |
| BINANCE_API_SECRET          | Binance dashboard | 90j recommande  |
| ALPACA_API_KEY              | Alpaca paper acct | low risk paper  |
| ALPACA_SECRET_KEY           | Alpaca paper acct | low risk paper  |
| TELEGRAM_BOT_TOKEN          | BotFather         | 180j ou si leak |
| TELEGRAM_CHAT_ID            | Telegram          | n/a (id only)   |
| IBKR_HOST/PORT              | Hetzner VPS IP    | n/a (config)    |
| IBKR_PAPER (bool)           | env flag          | n/a             |
| BINANCE_LIVE_CONFIRMED      | env flag          | n/a (toggle)    |
| PAPER_TRADING (bool)        | env flag          | n/a             |
| MACRO_ECB_LIVE_ENABLED      | env flag          | n/a             |
| DATA_FRESHNESS_GATE         | env flag          | n/a             |

### Secrets non-env

- SSH key Hetzner: `~/.ssh/id_hetzner` (private key) - rotation 1y
- IBKR password: en GUI Gateway (saved by user) - rotation 90j

## Protection actuelle (.gitignore)

```
.env
.env.*
*.env
!.env.example
**/secrets/
*_credentials*
*_token*
*.key
*.pem
```

## Audit findings (2026-04-19)

- `git log --all --full-history -- '**/*.env'` -> aucune entree (jamais committee)
- `grep BINANCE_API_KEY=...20chars` dans tous fichiers tracked -> aucune match
- `grep password=...8chars` dans tous fichiers tracked -> aucune match
- .env present localement seulement (cf `ls .env*`)

## Rotation policy

### Critical (revoke + replace immediatement si leak)
- BINANCE_API_KEY/SECRET (vraies fonds en jeu)
- TELEGRAM_BOT_TOKEN (bot peut spam)

### Important (90j recommande)
- BINANCE keys (rotation 90j)
- IBKR password (rotation 90j)

### Lower priority
- ALPACA paper (low value)
- SSH keys (1y avec audit logs SSH)

## Procedure rotation Binance

1. Binance > API Management > Create new API key (label avec date)
2. Restreindre IP (Hetzner only) + permissions (margin + spot only, pas withdraw)
3. Editer `.env` sur VPS:
   ```bash
   ssh root@VPS
   nano /opt/trading-platform/.env
   # Update BINANCE_API_KEY, BINANCE_API_SECRET
   systemctl restart trading-worker
   ```
4. Verifier nouvelle cle via Telegram heartbeat (BINANCE: $X,XXX)
5. Revoquer ancienne cle dans Binance dashboard

## Procedure rotation Telegram bot

1. BotFather > /token > generate new
2. Update `.env` sur VPS + local, restart worker
3. Old token automatically invalidated

## Score post-Phase 19

- .gitignore protection: **9/10** (toutes les patterns critiques)
- Secrets jamais committed: **9/10** (verifie via git log + grep)
- Rotation policy documentee: **8/10** (cette doc, mais pas encore drilled)
- Rotation reelle effectuee: **5/10** (depend du calendrier, pas trace)
- Recommandation: ajouter rappel calendar 90j pour rotation BINANCE
