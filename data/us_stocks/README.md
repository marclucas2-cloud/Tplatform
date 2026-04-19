# data/us_stocks/

**Producer** : `scripts/download_us_data_alpaca.py` + `scripts/fetch_midcap_data.py`.
**Consumer** : backtests US (`us_sector_ls_40_5`, `us_stocks_daily`,
`us_pead`), WF scripts, discovery engine.
**Criticity** : HIGH pour research US. **Re-producible** via scripts.
**Tolerance absence** : OK en dev (download a la demande). Sur VPS : attendu si us_*
strategies actives.
**Gitignore** : contenu `*.parquet` auto-genere ignore (volume ~30MB, 506 tickers).
Seul ce README versionne.

Structure typique : `{TICKER}.parquet` (ex AAPL, ABBV, ...). Data 5Y daily
via Alpaca API ou yfinance fallback.

Pour re-produire : `python scripts/download_us_data_alpaca.py --universe sp500`
ou `python scripts/fetch_midcap_data.py`.

**NE PAS commiter parquets** (deja couvert par `*.parquet` global + dir-specific).
