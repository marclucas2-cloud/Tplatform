/**
 * Tooltips institutionnels en francais pour chaque metrique du dashboard.
 */
export const TOOLTIPS = {
  // NAV & Performance
  nav_live: "Net Asset Value = Valeur totale du portefeuille live (IBKR + Binance). Exclut le paper trading (Alpaca).",
  pnl_trading: "Profit & Loss de trading pur, hors depots et retraits. Si vous deposez $5K, le P&L ne change pas.",
  twr: "Time-Weighted Return = Performance eliminant l'effet des flux de capital. C'est la metrique standard pour comparer un fonds a un benchmark.",
  pnl_day: "Variation de l'equity depuis l'ouverture du marche aujourd'hui.",
  pnl_unrealized: "Plus/moins-values latentes sur les positions encore ouvertes.",

  // Risk
  drawdown: "Baisse depuis le dernier sommet d'equity. Ex: -3.1% = le portefeuille a perdu 3.1% depuis son pic.",
  var_95: "Value at Risk 95% sur 1 jour. Perte maximale estimee avec 95% de confiance sur les prochaines 24h.",
  sharpe: "Ratio de Sharpe = Rendement excedentaire / volatilite. > 1.0 = bon, > 2.0 = excellent.",
  kill_switch: "Mecanisme d'arret d'urgence. Se declenche automatiquement si le drawdown depasse un seuil.",
  risk_if_stopped: "Perte totale si TOUS les stop-loss sont touches en meme temps. Represente le worst case immediat.",
  exposure_net: "Exposition nette = Long - Short. Positive = biais haussier, negative = biais baissier.",

  // Strategies
  phase: "Phase du cycle de vie : CODE -> WF (validation) -> PAPER -> PROBATION -> LIVE. REJECTED = rejete par walk-forward.",
  allocation: "Pourcentage du capital total alloue a cette strategie.",
  kill_margin: "Distance au seuil de desactivation automatique. < 50% = zone de danger.",

  // Trades
  commissions: "Frais de courtage payes aux brokers (Alpaca, IBKR, Binance).",
  interest: "Interets d'emprunt payes sur les positions short en margin (Binance surtout).",
  slippage: "Ecart entre le prix demande et le prix obtenu, en points de base (1 bps = 0.01%).",
  cost_pct: "Pourcentage du P&L brut consomme par les couts. < 15% = sain, > 25% = problematique.",

  // Tax
  pfu: "Prelevement Forfaitaire Unique = 30% flat tax (12.8% IR + 17.2% PS) sur les plus-values en France.",
  crypto_crypto: "En France, les echanges crypto-crypto (BTC -> ETH) ne sont PAS imposables. Seule la conversion en EUR/fiat declenche l'impot.",

  // Positions
  sl_distance: "Distance en % entre le prix actuel et le stop-loss. < 2% = proche du declenchement.",
}
