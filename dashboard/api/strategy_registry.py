"""
Registre complet des strategies avec descriptions, edges et parametres.
Source unique de verite pour le dashboard.
"""

STRATEGY_REGISTRY = {
    "opex_gamma": {
        "name": "OpEx Gamma Pin",
        "tier": "S",
        "type": "intraday",
        "edge_type": "Event-driven (options expiration)",
        "description": (
            "Les jours d'expiration d'options (vendredis), les market makers doivent "
            "hedger leur exposition gamma. Cela cree un effet d'aimant vers le 'round number' "
            "le plus proche du VWAP — le prix est mecaniquement attire vers ces niveaux. "
            "On entre en mean reversion quand le prix s'ecarte de >0.3% du round number."
        ),
        "why_it_works": (
            "C'est un flux MECANIQUE, pas technique. Les market makers DOIVENT hedger — "
            "ils n'ont pas le choix. L'edge est structurel et difficile a arbitrer car il "
            "necessite de comprendre le positionnement options."
        ),
        "parameters": {
            "deviation_threshold": {"value": "0.30%", "description": "Ecart min du prix vs round number pour entrer"},
            "stop_loss": {"value": "0.50%", "description": "Stop loss depuis le prix d'entree"},
            "take_profit": {"value": "Round number", "description": "Le prix cible est le round number (magnet price)"},
            "round_step_gt500": {"value": "$10", "description": "Step pour les actions > $500 (ex: SPY)"},
            "round_step_gt100": {"value": "$5", "description": "Step pour les actions $100-$500"},
            "round_step_gt50": {"value": "$2.50", "description": "Step pour les actions $50-$100"},
            "round_step_default": {"value": "$1", "description": "Step pour les actions < $50"},
            "timing": {"value": "13:00 - 15:30 ET", "description": "Fenetre de trading (apres-midi uniquement)"},
            "jours_actifs": {"value": "Vendredis", "description": "Actif les vendredis + 3eme vendredi du mois (OpEx mensuel)"},
            "max_trades_jour": {"value": "2", "description": "Maximum de trades par jour"},
        },
        "tickers": ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA"],
        "backtest": {"sharpe": 10.41, "win_rate": 72.9, "profit_factor": 4.51, "max_dd": 0.02, "trades": 48},
    },

    "gap_continuation": {
        "name": "Overnight Gap Continuation",
        "tier": "A",
        "type": "intraday",
        "edge_type": "Momentum (gap continuation)",
        "description": (
            "Les gaps d'ouverture > 1.1% avec un volume eleve (> 1.8x la moyenne) "
            "tendent a continuer dans la direction du gap pendant les 2-3 premieres heures. "
            "Contrairement au 'gap fade' (qui echoue), on SUIT le gap."
        ),
        "why_it_works": (
            "Les gaps sont causes par des flux overnight (earnings, news, macro). "
            "Le volume confirme que le flux est reel. Les institutionnels qui n'ont pas "
            "pu entrer overnight achettent/vendent a l'ouverture, amplifiant le mouvement."
        ),
        "parameters": {
            "min_gap": {"value": "1.1%", "description": "Gap minimum pour declencher le signal"},
            "volume_mult": {"value": "1.8x", "description": "Volume d'ouverture vs moyenne 20j"},
            "stop_loss": {"value": "0.8%", "description": "Stop loss depuis l'entree"},
            "take_profit": {"value": "1.5%", "description": "Take profit"},
            "timing": {"value": "09:35 - 11:00 ET", "description": "Fenetre d'entree (matin uniquement)"},
            "max_trades_jour": {"value": "3", "description": "Maximum de trades par jour"},
        },
        "tickers": ["Univers complet (88 tickers les plus liquides)"],
        "backtest": {"sharpe": 5.22, "win_rate": 53.1, "profit_factor": 1.61, "max_dd": 0.38, "trades": 32},
    },

    "gold_fear": {
        "name": "Gold Fear Gauge",
        "tier": "B",
        "type": "intraday",
        "edge_type": "Cross-asset (risk-off signal)",
        "description": (
            "Quand l'or (GLD) monte ET les actions (SPY) baissent simultanement, "
            "c'est un signal de risk-off institutionnel. On shorte les actions high-beta "
            "(TSLA, NVDA, AMD, COIN, MARA) qui seront les plus touchees par le "
            "mouvement de de-risking."
        ),
        "why_it_works": (
            "L'or est le barometre de la peur institutionnelle. Quand les gros fonds "
            "achettent de l'or ET vendent des actions, c'est un flux massif qui persiste "
            "2-4 heures. Les actions high-beta amplifient le mouvement."
        ),
        "parameters": {
            "gld_threshold": {"value": "+0.5%", "description": "GLD doit etre en hausse de >0.5% depuis l'ouverture"},
            "spy_threshold": {"value": "-0.3%", "description": "SPY doit etre en baisse de >0.3% depuis l'ouverture"},
            "stop_loss": {"value": "1.0%", "description": "Stop loss (si le risk-off se retourne)"},
            "take_profit": {"value": "2.0%", "description": "Take profit ou sortie a 14:00 ET"},
            "timing": {"value": "10:30 - 14:00 ET", "description": "Detection a 10:30, trade jusqu'a 14:00"},
            "direction": {"value": "SHORT uniquement", "description": "Cette strategie est short-only"},
            "max_trades_jour": {"value": "1", "description": "Maximum 1 position"},
        },
        "tickers": ["GLD", "SPY (signal)", "TSLA", "NVDA", "AMD", "COIN", "MARA (trade)"],
        "backtest": {"sharpe": 5.01, "win_rate": 56.2, "profit_factor": 2.20, "max_dd": 0.12, "trades": 16},
    },

    "crypto_proxy_v2": {
        "name": "Crypto-Proxy Regime V2",
        "tier": "A",
        "type": "intraday",
        "edge_type": "Pairs (decorrelation reversion)",
        "description": (
            "COIN (Coinbase) et MARA/MSTR (mineurs Bitcoin) sont normalement tres correles. "
            "Quand COIN monte fort (>0.7%) mais que MARA/MSTR ne suivent pas, le 'follower' "
            "finit par rattraper. On achete le retardataire."
        ),
        "why_it_works": (
            "Les crypto-proxies sont lies au meme sous-jacent (Bitcoin). La decorrelation "
            "temporaire est due a des flux specifiques (ex: earnings COIN, rebalancing ETF). "
            "La convergence est mecanique car les arbitrageurs forcent le retour a l'equilibre."
        ),
        "parameters": {
            "leader_perf": {"value": "+0.7%", "description": "COIN doit monter de >0.7%"},
            "zscore_entry": {"value": "-1.2", "description": "Z-score de decorrelation pour entrer"},
            "stop_loss": {"value": "ATR x 2.0", "description": "Stop loss base sur l'ATR"},
            "take_profit": {"value": "Risk x 1.5", "description": "Take profit = 1.5x le risque"},
            "zscore_lookback": {"value": "15 barres", "description": "Fenetre pour calculer le z-score"},
            "timing": {"value": "10:00 - 15:30 ET", "description": "Fenetre de trading"},
            "max_trades_jour": {"value": "2", "description": "Maximum de trades par jour"},
        },
        "tickers": ["COIN (leader)", "MARA", "MSTR", "RIOT (followers)"],
        "backtest": {"sharpe": 3.49, "win_rate": 63.6, "profit_factor": 1.77, "max_dd": 0.10, "trades": 20},
    },

    "dow_seasonal": {
        "name": "Day-of-Week Seasonal",
        "tier": "A",
        "type": "intraday",
        "edge_type": "Seasonal (calendar anomaly)",
        "description": (
            "Le 'Monday Effect' (biais negatif le lundi) et le 'Friday bullish' "
            "(cloture positive le vendredi) sont des anomalies calendaires documentees "
            "academiquement. Le debut de mois (jours 1-3) est egalement haussier "
            "a cause des flux de pension funds."
        ),
        "why_it_works": (
            "Les flux institutionnels sont cycliques : ventes le lundi (derisking weekend), "
            "achats le vendredi (couverture weekend), flux pension funds en debut de mois. "
            "Ces patterns persistent car ils sont lies a des contraintes operationnelles."
        ),
        "parameters": {
            "stop_loss": {"value": "0.5%", "description": "Stop loss"},
            "take_profit": {"value": "0.3%", "description": "Take profit (conservateur)"},
            "rsi_long": {"value": "> 55", "description": "RSI minimum pour entrer LONG"},
            "rsi_short": {"value": "< 45", "description": "RSI maximum pour entrer SHORT"},
            "spy_atr_filter": {"value": "ATR 20j > 2%", "description": "Skip si SPY trop volatile"},
            "timing": {"value": "10:00 - 15:30 ET", "description": "Fenetre de trading"},
        },
        "tickers": ["SPY", "QQQ", "IWM", "DIA"],
        "backtest": {"sharpe": 3.42, "win_rate": 68.2, "profit_factor": 1.55, "max_dd": 0.09, "trades": 44},
    },

    "vwap_micro": {
        "name": "VWAP Micro-Deviation",
        "tier": "A",
        "type": "intraday",
        "edge_type": "Mean reversion (VWAP rolling)",
        "description": (
            "Au lieu du VWAP journalier classique, on utilise un VWAP rolling sur 20 barres "
            "(~1h40). Quand le prix s'ecarte de >1.2 ecarts-types de ce VWAP court, il "
            "revient rapidement. Les algos TWAP/VWAP institutionnels utilisent ces niveaux."
        ),
        "why_it_works": (
            "Le VWAP rolling est plus reactif que le VWAP daily — il s'adapte a la tendance "
            "intraday. Les deviations extremes sont corrigees par les algorithmes institutionnels "
            "qui executent au VWAP. C'est un mean reversion structurel."
        ),
        "parameters": {
            "vwap_lookback": {"value": "20 barres", "description": "Fenetre du VWAP rolling (~1h40 en 5M)"},
            "entry_sd": {"value": "1.2 SD", "description": "Deviation standard pour entrer"},
            "stop_loss": {"value": "2.0 SD", "description": "Stop loss a 2.0 SD du VWAP"},
            "take_profit": {"value": "0.3 SD", "description": "Target = retour proche du VWAP"},
            "rsi_confirm": {"value": "< 40 (long) / > 60 (short)", "description": "Confirmation RSI"},
            "timing": {"value": "10:30 - 15:30 ET", "description": "Fenetre de trading"},
            "max_trades_jour": {"value": "3", "description": "Maximum de trades par jour"},
        },
        "tickers": ["Top 31 tickers liquides (AAPL, MSFT, NVDA, etc.)"],
        "backtest": {"sharpe": 3.08, "win_rate": 48.2, "profit_factor": 1.48, "max_dd": 0.06, "trades": 363},
    },

    "orb_v2": {
        "name": "ORB 5-Min V2",
        "tier": "B",
        "type": "intraday",
        "edge_type": "Breakout (opening range)",
        "description": (
            "Le range des 5 premieres minutes (9:30-9:35) capture le positionnement overnight. "
            "Version V2 avec filtres stricts : gap > 3%, volume > 3x, prix > $10. "
            "Reduit les trades de 615 a ~220 pour survivre aux commissions."
        ),
        "why_it_works": (
            "L'opening range breakout est un des patterns les plus documentes. Le flux "
            "directionnel de l'ouverture indique ou les gros acteurs se positionnent. "
            "Les filtres stricts (gap + volume) selectionnent les 'stocks in play'."
        ),
        "parameters": {
            "gap_threshold": {"value": "3.0%", "description": "Gap minimum (stock in play)"},
            "volume_mult": {"value": "3.0x", "description": "Volume 1ere barre vs moyenne"},
            "stop_loss": {"value": "Extremite opposee du range", "description": "Stop = low (long) ou high (short) du range 5M"},
            "take_profit": {"value": "2x le risque", "description": "R:R ratio = 2:1"},
            "timing": {"value": "09:35 - 15:00 ET", "description": "Entree apres 9:35"},
            "max_trades_jour": {"value": "3", "description": "Maximum de trades par jour"},
        },
        "tickers": ["Univers complet filtre par gap + volume"],
        "backtest": {"sharpe": 2.28, "win_rate": 48.0, "profit_factor": 1.30, "max_dd": 0.88, "trades": 220},
    },

    "meanrev_v2": {
        "name": "Mean Reversion V2",
        "tier": "B",
        "type": "intraday",
        "edge_type": "Mean reversion (BB + RSI extreme)",
        "description": (
            "Bollinger Bands a 3.0 ecarts-types + RSI(7) aux extremes (12/88). "
            "Version V2 tres selective : necessite volume > 2x et maximum 2 trades/jour. "
            "La selectivite reduit les trades de 615 a ~57, eliminant le bruit."
        ),
        "why_it_works": (
            "A 3 ecarts-types, le prix est statistiquement en zone extreme (99.7%). "
            "Le RSI a 12/88 confirme l'epuisement. La combinaison des deux filtres "
            "est tres selective et le taux de reversion est eleve."
        ),
        "parameters": {
            "bb_period": {"value": "20", "description": "Periode des Bollinger Bands"},
            "bb_std": {"value": "3.0", "description": "Ecarts-types (tres large)"},
            "rsi_period": {"value": "7", "description": "Periode RSI (court terme)"},
            "rsi_long": {"value": "< 12", "description": "RSI oversold extreme pour LONG"},
            "rsi_short": {"value": "> 88", "description": "RSI overbought extreme pour SHORT"},
            "stop_loss": {"value": "1.0%", "description": "Stop loss au-dela de la bande"},
            "take_profit": {"value": "Middle band (SMA20)", "description": "Target = retour a la moyenne"},
            "volume_mult": {"value": "2.0x", "description": "Volume minimum"},
            "max_trades_jour": {"value": "2", "description": "Maximum 2 trades par jour"},
        },
        "tickers": ["Univers complet (88 tickers)"],
        "backtest": {"sharpe": 1.44, "win_rate": 57.0, "profit_factor": 1.35, "max_dd": 0.50, "trades": 57},
    },

    "corr_hedge": {
        "name": "Correlation Regime Hedge",
        "tier": "B",
        "type": "intraday",
        "edge_type": "Cross-asset (correlation anomaly)",
        "description": (
            "SPY et TLT (obligations) sont normalement inversement correles. "
            "Quand ils bougent dans le meme sens pendant 30+ minutes, c'est une anomalie. "
            "On shorte celui qui a le plus devie de son VWAP — il reviendra en premier."
        ),
        "why_it_works": (
            "La correlation inverse SPY/TLT est un equilibre fondamental (risk-on vs risk-off). "
            "Quand elle se casse temporairement, c'est souvent du au flux de rebalancing qui "
            "se corrige en quelques heures. Meme logique pour GLD/USO."
        ),
        "parameters": {
            "corr_threshold": {"value": "> 0.5", "description": "Correlation rolling 20 barres (normalement < 0)"},
            "stop_loss": {"value": "0.5%", "description": "Stop loss"},
            "take_profit": {"value": "0.8%", "description": "Take profit"},
            "timing": {"value": "11:00 - 15:00 ET", "description": "Fenetre de trading"},
            "pairs": {"value": "SPY/TLT, GLD/USO", "description": "Paires tradees"},
            "max_trades_jour": {"value": "2", "description": "Maximum de trades par jour"},
        },
        "tickers": ["SPY", "TLT", "GLD", "USO"],
        "backtest": {"sharpe": 1.09, "win_rate": 54.5, "profit_factor": 1.25, "max_dd": 0.10, "trades": 88},
    },

    "triple_ema": {
        "name": "Triple EMA Pullback",
        "tier": "B",
        "type": "intraday",
        "edge_type": "Trend following (EMA alignment)",
        "description": (
            "Quand les EMA 8/13/21 sont alignees (toutes ascendantes ou descendantes), "
            "le trend est fort. Un pullback vers l'EMA 8 offre une re-entree a moindre "
            "risque dans la direction du trend. Desactivee automatiquement en regime bear."
        ),
        "why_it_works": (
            "L'alignement triple EMA confirme un trend etabli. Le pullback vers l'EMA courte "
            "est un point d'entree classique du trend following, avec un stop naturel (EMA 21)."
        ),
        "parameters": {
            "ema_fast": {"value": "8", "description": "EMA courte"},
            "ema_mid": {"value": "13", "description": "EMA moyenne"},
            "ema_slow": {"value": "21", "description": "EMA longue"},
            "stop_loss": {"value": "Sous EMA 21 + 0.2%", "description": "Stop loss sous l'EMA longue"},
            "take_profit": {"value": "1.5%", "description": "Take profit"},
            "timing": {"value": "10:00 - 15:15 ET", "description": "Fenetre de trading"},
            "regime_filter": {"value": "DESACTIVEE en bear", "description": "Automatiquement off si SPY < SMA200"},
        },
        "tickers": ["Top 31 tickers liquides"],
        "backtest": {"sharpe": 1.06, "win_rate": 44.7, "profit_factor": 1.12, "max_dd": 0.30, "trades": 360},
    },

    "lateday_meanrev": {
        "name": "Late Day Mean Reversion",
        "tier": "B",
        "type": "intraday",
        "edge_type": "Mean reversion (power hour exhaustion)",
        "description": (
            "Apres 14:00 ET, les stocks qui ont bouge de >3% depuis l'ouverture avec un "
            "RSI extreme et un volume en baisse montrent de l'epuisement. On entre en "
            "counter-trend pour capturer le retracement de fin de journee."
        ),
        "why_it_works": (
            "En fin de journee, les traders intraday ferment leurs positions (profit-taking), "
            "les algos TWAP terminent leurs ordres, et le flux directionnel s'epuise. "
            "Le retracement est statistiquement probable sur les gros mouvements."
        ),
        "parameters": {
            "min_day_move": {"value": "3.0%", "description": "Mouvement minimum depuis l'ouverture"},
            "rsi_extreme": {"value": "< 25 (long) / > 75 (short)", "description": "RSI extreme requis"},
            "stop_loss": {"value": "0.8%", "description": "Stop loss"},
            "take_profit": {"value": "1.2%", "description": "Take profit"},
            "timing": {"value": "14:00 - 15:55 ET", "description": "Fenetre (derniere heure)"},
        },
        "tickers": ["Univers complet (hors ETFs leverages)"],
        "backtest": {"sharpe": 0.60, "win_rate": 52.3, "profit_factor": 1.34, "max_dd": 0.71, "trades": 44},
    },

    "momentum_25etf": {
        "name": "Momentum 25 ETFs",
        "tier": "C",
        "type": "monthly",
        "edge_type": "Momentum (rotation mensuelle)",
        "description": (
            "Rotation mensuelle sur les 25 ETFs les plus liquides. On achete les 2 ETFs "
            "avec le meilleur momentum sur 3 mois (ROC). Crash filter : si SPY < SMA200, "
            "tout vendre (100% cash)."
        ),
        "why_it_works": (
            "Le momentum factor est le plus documente en finance academique (Jegadeesh & Titman). "
            "Les actifs qui ont bien performe continuent en general sur 1-12 mois. "
            "Le crash filter evite les periodes de regime baissier."
        ),
        "parameters": {
            "lookback": {"value": "3 mois", "description": "Periode de momentum (ROC)"},
            "top_n": {"value": "2", "description": "Nombre d'ETFs selectionnes"},
            "crash_filter": {"value": "SPY > SMA200", "description": "Si SPY < SMA200, tout vendre"},
            "stop_loss": {"value": "5% (trailing)", "description": "Trailing stop broker-side"},
            "rebalance": {"value": "1er du mois", "description": "Rebalancement mensuel"},
        },
        "tickers": ["SPY", "QQQ", "IWM", "DIA", "EFA", "EEM", "TLT", "GLD", "USO", "XLE", "XLF", "XLK", "..."],
        "backtest": {"sharpe": 0.88, "win_rate": 55.0, "profit_factor": 1.20, "max_dd": 3.0, "trades": 24},
    },

    "pairs_mu_amat": {
        "name": "Pairs MU/AMAT",
        "tier": "C",
        "type": "daily",
        "edge_type": "Pairs (cointegration)",
        "description": (
            "MU (Micron) et AMAT (Applied Materials) sont dans le meme secteur semi-conducteurs "
            "et historiquement cointegres. Quand le z-score du ratio atteint ±2, on entre "
            "en mean reversion (long le retardataire, short le leader)."
        ),
        "why_it_works": (
            "La cointegration entre MU et AMAT est stable sur 5 ans (Sharpe 1.15 sur 5Y). "
            "Les deux entreprises sont exposees aux memes cycles semi-conducteurs. "
            "Les divergences temporaires sont corrigees par les flux sectoriels."
        ),
        "parameters": {
            "zscore_entry": {"value": "2.0", "description": "Z-score pour entrer"},
            "zscore_exit": {"value": "0.5", "description": "Z-score pour sortir"},
            "stop_loss": {"value": "3% par jambe (5% trailing broker-side)", "description": "Stop loss"},
            "lookback": {"value": "60 jours", "description": "Fenetre pour calculer mean/std"},
        },
        "tickers": ["MU", "AMAT"],
        "backtest": {"sharpe": 0.94, "win_rate": 58.0, "profit_factor": 1.30, "max_dd": 2.5, "trades": 18},
    },

    "vrp_rotation": {
        "name": "VRP SVXY/SPY/TLT",
        "tier": "C",
        "type": "monthly",
        "edge_type": "Regime (volatility risk premium)",
        "description": (
            "Rotation entre 3 actifs selon le regime de volatilite : "
            "SVXY (short VIX) quand la vol est basse et en baisse, "
            "SPY quand la vol est normale, TLT quand la vol est haute (flight to safety)."
        ),
        "why_it_works": (
            "La prime de risque de volatilite (VRP) est le fait que la volatilite implicite "
            "est generalement superieure a la vol realisee. SVXY capture cette prime. "
            "Le switch vers TLT en haute vol protege pendant les crises."
        ),
        "parameters": {
            "vol_regime": {"value": "ATR 20j vs SMA60 de l'ATR", "description": "Detection du regime"},
            "svxy_signal": {"value": "Vol basse + en baisse", "description": "Quand acheter SVXY"},
            "spy_signal": {"value": "Vol normale", "description": "Quand acheter SPY"},
            "tlt_signal": {"value": "Vol haute", "description": "Quand acheter TLT (refuge)"},
            "stop_loss": {"value": "8% (trailing broker-side)", "description": "Stop large pour le mensuel"},
            "rebalance": {"value": "Mensuel", "description": "Check du regime chaque mois"},
        },
        "tickers": ["SVXY", "SPY", "TLT"],
        "backtest": {"sharpe": 0.75, "win_rate": 52.0, "profit_factor": 1.15, "max_dd": 4.0, "trades": 12},
    },
}
