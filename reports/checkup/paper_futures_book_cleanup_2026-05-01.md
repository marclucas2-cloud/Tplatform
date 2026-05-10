# Paper Futures Book Cleanup — 2026-05-01

## Why

Le checkup du 2026-05-01 a trouvé un book `IBKR paper` non canonique sur `DUP573894`:

- state file local paper: `MNQ +1` seulement
- broker paper: `MES +2`, `MNQ +5`
- open orders paper:
  - multiples brackets `MESM6` / `MNQM6`
  - reliquats `MCLZ6` sans position ouverte

Le book n'était plus lisible ni fiable comme environnement paper de validation.

## Action taken

Nettoyage manuel VPS execute le `2026-05-01 06:01 UTC`:

1. inventaire broker-side du compte `DUP573894`
2. annulation globale des ordres paper ouverts
3. flatten market des positions nettes restantes:
   - `SELL 2 MESM6`
   - `SELL 5 MNQM6`
4. reset du state file paper a `{}`:
   - `/opt/trading-platform/data/state/futures_positions_paper.json`

## Result

Etat final verifie juste apres cleanup:

- broker paper positions: `[]`
- broker paper open orders: `[]`
- state file paper: `{}`

Le live `U25023333` n'a pas ete touche.

## Audit artifacts on VPS

- `/opt/trading-platform/reports/checkup/paper_futures_book_cleanup_2026-05-01.json`
- `/opt/trading-platform/reports/checkup/paper_futures_book_cleanup_2026-05-01_pass2.json`

## Notes

- Le premier passage a confirme que les ordres provenaient de plusieurs `clientId`, ce qui expliquait que le nettoyage partiel precedent n'avait pas abouti.
- Le flatten initial a aussi revele un detail IBKR: les contrats issus de `positions()` devaient etre requalifies avec un `exchange` explicite avant soumission d'ordres marche.
- Le deuxieme passage a utilise des contrats qualifies (`MES`/`MNQ` sur `CME`) et a permis un reset complet du book paper.
