"""
Dashboard Pro — Revue & analyse des stratégies de trading.

Lancement :
    streamlit run dashboard.py

4 onglets :
  1. Leaderboard   — classement de toutes les stratégies
  2. Détail        — equity curve, drawdown, distribution des trades
  3. IS/OOS        — comparaison in-sample vs out-of-sample (stratégies optimisées)
  4. Portfolio     — corrélation + allocation
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from core.backtest.engine import BacktestEngine
from core.data.loader import OHLCVLoader
from core.data.universe import get_asset
from core.portfolio.correlation import PortfolioCorrelation
from core.ranking.ranker import StrategyRanker
from core.strategy_schema.validator import StrategyValidator

STRATEGIES_DIR = ROOT / "strategies"

# ─── Palettes & config ────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Trading Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

PERIOD_MAP = {"1M": "7d", "5M": "60d", "15M": "60d", "1H": "2y", "4H": "2y", "1D": "5y"}
TF_FALLBACK = {"1M": "1H", "5M": "1H"}

# ─── Helpers mis en cache ─────────────────────────────────────────────────────


@st.cache_data(ttl=3600, show_spinner=False)
def load_strategies() -> list[dict]:
    validator = StrategyValidator()
    strats = []
    for p in sorted(STRATEGIES_DIR.glob("*.json")):
        try:
            s = validator.load_and_validate(p)
            s["_file"] = p.name
            strats.append(s)
        except Exception:
            pass
    return strats


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_data(asset_symbol: str, timeframe: str):
    actual_tf = TF_FALLBACK.get(timeframe, timeframe)
    period = PERIOD_MAP.get(actual_tf, "1y")
    asset = get_asset(asset_symbol)
    ticker = asset.ticker if asset else None
    try:
        return OHLCVLoader.from_yfinance(asset_symbol, actual_tf, period=period, ticker=ticker)
    except Exception as e:
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def run_backtest(strategy_id: str, asset_symbol: str, capital: float) -> dict | None:
    strats = load_strategies()
    strategy = next((s for s in strats if s["strategy_id"] == strategy_id), None)
    if strategy is None:
        return None
    data = fetch_data(asset_symbol, strategy["timeframe"])
    if data is None:
        return None
    engine = BacktestEngine(initial_capital=capital)
    try:
        result = engine.run(data, strategy)
        d = result.to_dict()
        # Convertir en liste pour la sérialisation du cache
        eq = result.equity_curve
        d["equity_curve"] = eq.tolist() if hasattr(eq, "tolist") else list(eq)
        return d
    except Exception:
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def run_wf_backtest(strategy_id: str, asset_symbol: str, capital: float) -> dict | None:
    strats = load_strategies()
    strategy = next((s for s in strats if s["strategy_id"] == strategy_id), None)
    if strategy is None:
        return None
    data = fetch_data(asset_symbol, strategy["timeframe"])
    if data is None:
        return None
    engine = BacktestEngine(initial_capital=capital)
    try:
        # IS (70%)
        train, test = data.split(0.70)
        r_is = engine.run(train, strategy)
        r_oos = engine.run(test, strategy)
        def _to_list(eq):
            return eq.tolist() if hasattr(eq, "tolist") else list(eq)
        return {
            "is": r_is.to_dict(),
            "oos": r_oos.to_dict(),
            "is_equity": _to_list(r_is.equity_curve),
            "oos_equity": _to_list(r_oos.equity_curve),
        }
    except Exception:
        return None


# ─── Sidebar ──────────────────────────────────────────────────────────────────

st.sidebar.title("📈 Trading Dashboard")
capital = st.sidebar.number_input("Capital initial ($)", value=10_000, step=1_000)

strategies = load_strategies()
all_assets = sorted({s["asset"] for s in strategies})
all_tfs = sorted({s["timeframe"] for s in strategies})

st.sidebar.markdown("---")
st.sidebar.markdown(f"**{len(strategies)}** stratégies chargées")
st.sidebar.markdown(f"**{len(all_assets)}** actifs")

# ─── Tabs ─────────────────────────────────────────────────────────────────────

tab1, tab2, tab3, tab4 = st.tabs(
    ["🏆 Leaderboard", "🔍 Détail stratégie", "📊 IS/OOS", "💼 Portfolio"]
)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — LEADERBOARD
# ══════════════════════════════════════════════════════════════════════════════

with tab1:
    st.header("🏆 Leaderboard — toutes les stratégies")

    col_f1, col_f2, col_f3 = st.columns(3)
    filter_tf = col_f1.selectbox("Timeframe", ["Tous"] + all_tfs)
    filter_asset = col_f2.selectbox("Actif", ["Tous"] + all_assets)
    filter_valid = col_f3.checkbox("Validées uniquement", value=False)

    with st.spinner("Backtests en cours..."):
        rows = []
        for s in strategies:
            sid = s["strategy_id"]
            asset = s["asset"]
            tf = s["timeframe"]

            if filter_tf != "Tous" and tf != filter_tf:
                continue
            if filter_asset != "Tous" and asset != filter_asset:
                continue

            r = run_backtest(sid, asset, capital)
            if r is None:
                continue
            if filter_valid and not r.get("passes_validation"):
                continue

            rows.append({
                "strategy_id": sid,
                "asset": asset,
                "timeframe": tf,
                "sharpe": r.get("sharpe_ratio", 0),
                "DD%": r.get("max_drawdown_pct", 0),
                "WR%": r.get("win_rate_pct", 0),
                "PF": r.get("profit_factor", 0),
                "trades": r.get("total_trades", 0),
                "return%": r.get("total_return_pct", 0),
                "expectancy": r.get("expectancy", 0),
                "valid": "✅" if r.get("passes_validation") else "—",
            })

    if not rows:
        st.warning("Aucun résultat. Vérifiez les filtres ou la connexion yfinance.")
    else:
        df = pd.DataFrame(rows).sort_values("sharpe", ascending=False)
        ranker = StrategyRanker()
        ranked = ranker.rank(rows)
        score_map = {r.strategy_id: r.score for r in ranked}
        df["score"] = df["strategy_id"].map(score_map).round(1)
        df = df.sort_values("score", ascending=False)

        # Formatage conditionnel
        def color_sharpe(val):
            if val > 1.5:
                return "background-color: #1a7a3a; color: white"
            elif val > 0:
                return "background-color: #2d6a2d; color: white"
            else:
                return "background-color: #8b1a1a; color: white"

        styled = df.style.format({
            "sharpe": "{:+.3f}", "DD%": "{:.1f}%", "WR%": "{:.1f}%",
            "PF": "{:.2f}", "return%": "{:+.2f}%", "expectancy": "{:.4f}",
            "score": "{:.1f}",
        }).map(color_sharpe, subset=["sharpe"])

        st.dataframe(styled, use_container_width=True, height=500)

        col_m1, col_m2, col_m3, col_m4 = st.columns(4)
        valid_count = sum(1 for r in rows if r["valid"] == "✅")
        pos_sharpe = sum(1 for r in rows if r["sharpe"] > 0)
        col_m1.metric("Stratégies testées", len(rows))
        col_m2.metric("Validées", f"{valid_count}/{len(rows)}")
        col_m3.metric("Sharpe positif", f"{pos_sharpe}/{len(rows)}")
        avg_sh = np.mean([r["sharpe"] for r in rows if r["sharpe"] > 0]) if pos_sharpe > 0 else 0
        col_m4.metric("Sharpe moyen (>0)", f"{avg_sh:.3f}")

        # Scatter Sharpe vs Drawdown
        fig = px.scatter(
            df, x="DD%", y="sharpe", color="asset", size="trades",
            hover_name="strategy_id", title="Sharpe vs Drawdown",
            labels={"DD%": "Max Drawdown %", "sharpe": "Sharpe Ratio"},
        )
        fig.add_hline(y=0, line_dash="dash", line_color="gray")
        fig.add_hline(y=1.0, line_dash="dot", line_color="green", annotation_text="Sharpe=1")
        st.plotly_chart(fig, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — DÉTAIL STRATÉGIE
# ══════════════════════════════════════════════════════════════════════════════

with tab2:
    st.header("🔍 Détail d'une stratégie")

    sid_options = [s["strategy_id"] for s in strategies]
    selected_sid = st.selectbox("Choisir une stratégie", sid_options)
    selected_strategy = next((s for s in strategies if s["strategy_id"] == selected_sid), None)

    if selected_strategy:
        asset_sym = selected_strategy["asset"]
        col_info1, col_info2, col_info3 = st.columns(3)
        col_info1.info(f"**Actif** : {asset_sym}")
        col_info2.info(f"**Timeframe** : {selected_strategy['timeframe']}")
        col_info3.info(f"**Fichier** : {selected_strategy.get('_file', '')}")

        with st.spinner(f"Backtest {selected_sid} sur {asset_sym}..."):
            r = run_backtest(selected_sid, asset_sym, capital)

        if r is None:
            st.error("Backtest impossible — données non disponibles.")
        else:
            # Métriques principales
            cols = st.columns(6)
            metrics = [
                ("Sharpe", f"{r['sharpe_ratio']:+.3f}"),
                ("Max DD", f"{r['max_drawdown_pct']:.1f}%"),
                ("Win Rate", f"{r['win_rate_pct']:.1f}%"),
                ("PF", f"{r['profit_factor']:.2f}"),
                ("Trades", str(r["total_trades"])),
                ("Return", f"{r['total_return_pct']:+.2f}%"),
            ]
            for col, (label, val) in zip(cols, metrics):
                col.metric(label, val)

            status = "✅ VALIDÉE" if r.get("passes_validation") else "❌ NON VALIDÉE"
            st.markdown(f"**Statut** : {status}")

            # Equity Curve
            equity = r.get("equity_curve", [])
            if equity:
                eq_df = pd.DataFrame({"equity": equity})
                fig_eq = go.Figure()
                fig_eq.add_trace(go.Scatter(
                    y=eq_df["equity"], mode="lines",
                    line=dict(color="#00cc88", width=1.5),
                    fill="tozeroy", fillcolor="rgba(0,204,136,0.1)",
                    name="Equity"
                ))
                fig_eq.update_layout(
                    title=f"Equity Curve — {selected_sid}",
                    xaxis_title="Trades", yaxis_title="Capital ($)",
                    template="plotly_dark", height=350,
                )
                st.plotly_chart(fig_eq, use_container_width=True)

                # Drawdown
                eq_series = pd.Series(equity)
                rolling_max = eq_series.cummax()
                drawdown = (eq_series - rolling_max) / rolling_max * 100
                fig_dd = go.Figure()
                fig_dd.add_trace(go.Scatter(
                    y=drawdown, mode="lines", fill="tozeroy",
                    line=dict(color="#ff4444", width=1),
                    fillcolor="rgba(255,68,68,0.15)", name="Drawdown %"
                ))
                fig_dd.update_layout(
                    title="Drawdown (%)", xaxis_title="Trades",
                    yaxis_title="Drawdown %", template="plotly_dark", height=200,
                )
                st.plotly_chart(fig_dd, use_container_width=True)

            # Métriques avancées
            with st.expander("📋 Métriques complètes"):
                adv = {k: v for k, v in r.items()
                       if k not in ("equity_curve", "trade_returns") and not isinstance(v, list)}
                st.json(adv)

            # Paramètres de la stratégie
            with st.expander("⚙️ Paramètres JSON"):
                display = {k: v for k, v in selected_strategy.items() if k != "_file"}
                st.json(display)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — IS/OOS COMPARISON
# ══════════════════════════════════════════════════════════════════════════════

with tab3:
    st.header("📊 Comparaison IS vs OOS")
    st.caption("70% des données pour l'in-sample (IS), 30% pour l'out-of-sample (OOS).")

    opt_strategies = [s for s in strategies if "_opt_" in s["strategy_id"]]
    if not opt_strategies:
        st.info("Aucune stratégie optimisée trouvée (cherche les IDs contenant '_opt_').")
    else:
        opt_sid = st.selectbox(
            "Stratégie optimisée", [s["strategy_id"] for s in opt_strategies]
        )
        opt_strat = next(s for s in opt_strategies if s["strategy_id"] == opt_sid)

        with st.spinner("IS/OOS backtest..."):
            wf = run_wf_backtest(opt_sid, opt_strat["asset"], capital)

        if wf is None:
            st.error("Données non disponibles.")
        else:
            r_is, r_oos = wf["is"], wf["oos"]
            ratio = r_oos["sharpe_ratio"] / r_is["sharpe_ratio"] if r_is["sharpe_ratio"] != 0 else 0

            # Statut robustesse
            if ratio >= 0.7:
                rob_color, rob_label = "green", f"✅ ROBUSTE (ratio={ratio:.2f})"
            elif ratio >= 0.5:
                rob_color, rob_label = "orange", f"⚠️ ACCEPTABLE (ratio={ratio:.2f})"
            else:
                rob_color, rob_label = "red", f"🚨 ALERTE sur-apprentissage (ratio={ratio:.2f})"

            st.markdown(f"<h3 style='color:{rob_color}'>{rob_label}</h3>", unsafe_allow_html=True)

            # Comparaison métriques
            metrics_keys = ["sharpe_ratio", "max_drawdown_pct", "profit_factor", "win_rate_pct", "total_trades"]
            labels = ["Sharpe", "Max DD%", "PF", "Win Rate%", "Trades"]
            is_vals = [r_is.get(k, 0) for k in metrics_keys]
            oos_vals = [r_oos.get(k, 0) for k in metrics_keys]

            fig_bar = go.Figure(data=[
                go.Bar(name="IS (70%)", x=labels, y=is_vals, marker_color="#4477ff"),
                go.Bar(name="OOS (30%)", x=labels, y=oos_vals, marker_color="#ff8844"),
            ])
            fig_bar.update_layout(
                barmode="group", title="IS vs OOS — Métriques clés",
                template="plotly_dark", height=350,
            )
            st.plotly_chart(fig_bar, use_container_width=True)

            # Equity curves IS + OOS côte à côte
            col_is, col_oos = st.columns(2)

            def equity_chart(equity, title, color, fill_color):
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    y=equity, mode="lines",
                    line=dict(color=color, width=1.5),
                    fill="tozeroy", fillcolor=fill_color,
                ))
                fig.update_layout(title=title, template="plotly_dark", height=250, showlegend=False)
                return fig

            col_is.plotly_chart(
                equity_chart(wf["is_equity"], f"IS — Sharpe={r_is['sharpe_ratio']:+.3f}",
                             "#4477ff", "rgba(68,119,255,0.15)"),
                use_container_width=True
            )
            col_oos.plotly_chart(
                equity_chart(wf["oos_equity"], f"OOS — Sharpe={r_oos['sharpe_ratio']:+.3f}",
                             "#ff8844", "rgba(255,136,68,0.15)"),
                use_container_width=True
            )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — PORTFOLIO
# ══════════════════════════════════════════════════════════════════════════════

with tab4:
    st.header("💼 Analyse de portefeuille")

    selected_sids = st.multiselect(
        "Sélectionner les stratégies à inclure",
        [s["strategy_id"] for s in strategies],
        default=[s["strategy_id"] for s in strategies[:6]],
    )

    if not selected_sids:
        st.info("Sélectionnez au moins 2 stratégies.")
    else:
        with st.spinner("Backtests portefeuille..."):
            portfolio_results = []
            for sid in selected_sids:
                strat = next((s for s in strategies if s["strategy_id"] == sid), None)
                if strat:
                    r = run_backtest(sid, strat["asset"], capital)
                    if r:
                        portfolio_results.append(r)

        if len(portfolio_results) < 2:
            st.warning("Pas assez de données pour construire le portefeuille.")
        else:
            # Reconvertir equity_curve en pd.Series pour PortfolioCorrelation
            for pr in portfolio_results:
                eq = pr.get("equity_curve")
                if isinstance(eq, list):
                    pr["equity_curve"] = pd.Series(eq)
            pc = PortfolioCorrelation(max_weight=0.40, min_sharpe=0.0)
            alloc = pc.allocate(portfolio_results)

            # Allocation pie chart
            alloc_data = [
                {"strategy": sid, "weight": w}
                for sid, w in alloc.weights.items()
                if w > 0.001
            ]
            if alloc_data:
                alloc_df = pd.DataFrame(alloc_data)
                fig_pie = px.pie(
                    alloc_df, names="strategy", values="weight",
                    title="Allocation du portefeuille",
                    color_discrete_sequence=px.colors.qualitative.Bold,
                )
                st.plotly_chart(fig_pie, use_container_width=True)

            # Corrélation heatmap
            equities = {}
            for r in portfolio_results:
                eq = r.get("equity_curve", [])
                if len(eq) > 10:
                    equities[r["strategy_id"]] = eq

            if len(equities) >= 2:
                min_len = min(len(v) for v in equities.values())
                eq_df = pd.DataFrame({k: v[:min_len] for k, v in equities.items()})
                returns_df = eq_df.pct_change().dropna()
                corr = returns_df.corr()

                fig_corr = px.imshow(
                    corr, text_auto=".2f", color_continuous_scale="RdBu_r",
                    title="Matrice de corrélation (rendements)", zmin=-1, zmax=1,
                )
                fig_corr.update_layout(template="plotly_dark", height=400)
                st.plotly_chart(fig_corr, use_container_width=True)

            # Stats portefeuille
            st.subheader("Métriques du portefeuille")
            col_p1, col_p2, col_p3 = st.columns(3)
            col_p1.metric("Sharpe portefeuille", f"{alloc.expected_sharpe:.3f}")
            col_p2.metric("Stratégies incluses", len([w for w in alloc.weights.values() if w > 0.001]))
            col_p3.metric("Ratio diversification", f"{alloc.diversification_ratio:.2f}")
