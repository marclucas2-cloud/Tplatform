"""
Visualisations Plotly pour les résultats de backtest.
"""
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import os
import config


def plot_equity_curve(
    equity_curves: dict[str, pd.Series],
    title: str = "Equity Curves",
    filename: str = "equity_curves.html",
):
    """
    Plot des equity curves de plusieurs stratégies sur un même graphique.
    equity_curves: {"Strategy Name": pd.Series(date -> equity)}
    """
    fig = go.Figure()
    colors = ["#2196F3", "#FF5722", "#4CAF50", "#FFC107", "#9C27B0", "#00BCD4"]

    for i, (name, equity) in enumerate(equity_curves.items()):
        fig.add_trace(go.Scatter(
            x=equity.index,
            y=equity.values,
            name=name,
            line=dict(color=colors[i % len(colors)], width=2),
        ))

    fig.update_layout(
        title=title,
        xaxis_title="Date",
        yaxis_title="Portfolio Value ($)",
        template="plotly_dark",
        hovermode="x unified",
        legend=dict(x=0.01, y=0.99),
        height=500,
    )

    path = os.path.join(config.OUTPUT_DIR, filename)
    fig.write_html(path)
    print(f"  [PLOT] Saved: {path}")
    return fig


def plot_strategy_comparison(
    all_metrics: dict[str, dict],
    filename: str = "strategy_comparison.html",
):
    """Comparaison en barres des métriques clés par stratégie."""
    names = list(all_metrics.keys())
    metrics_to_plot = [
        ("total_return_pct", "Return (%)"),
        ("sharpe_ratio", "Sharpe Ratio"),
        ("win_rate", "Win Rate (%)"),
        ("profit_factor", "Profit Factor"),
        ("max_drawdown_pct", "Max Drawdown (%)"),
    ]

    fig = make_subplots(
        rows=1, cols=len(metrics_to_plot),
        subplot_titles=[m[1] for m in metrics_to_plot],
    )

    colors = ["#2196F3", "#FF5722", "#4CAF50", "#FFC107", "#9C27B0", "#00BCD4"]

    for col, (key, label) in enumerate(metrics_to_plot, 1):
        values = [all_metrics[n].get(key, 0) for n in names]
        fig.add_trace(
            go.Bar(
                x=names,
                y=values,
                marker_color=[colors[i % len(colors)] for i in range(len(names))],
                showlegend=False,
            ),
            row=1, col=col,
        )

    fig.update_layout(
        title="Strategy Comparison",
        template="plotly_dark",
        height=400,
    )

    path = os.path.join(config.OUTPUT_DIR, filename)
    fig.write_html(path)
    print(f"  [PLOT] Saved: {path}")
    return fig


def plot_weekday_performance(
    trades: pd.DataFrame,
    strategy_name: str,
    filename: str = None,
):
    """Performance par jour de la semaine."""
    trades = trades.copy()
    trades["weekday"] = pd.to_datetime(trades["date"]).dt.day_name()
    order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    by_day = trades.groupby("weekday")["pnl"].agg(["sum", "count", "mean"]).reindex(order)

    fig = make_subplots(rows=1, cols=2, subplot_titles=["Total P&L", "Avg P&L per Trade"])

    fig.add_trace(go.Bar(x=by_day.index, y=by_day["sum"], marker_color="#2196F3"), row=1, col=1)
    fig.add_trace(go.Bar(x=by_day.index, y=by_day["mean"], marker_color="#4CAF50"), row=1, col=2)

    fig.update_layout(
        title=f"{strategy_name} — Performance by Weekday",
        template="plotly_dark",
        showlegend=False,
        height=400,
    )

    safe = strategy_name.lower().replace(' ', '_').replace('/', '_').replace('+', '_')
    fname = filename or f"{safe}_weekday.html"
    path = os.path.join(config.OUTPUT_DIR, fname)
    fig.write_html(path)
    print(f"  [PLOT] Saved: {path}")
    return fig


def plot_trade_distribution(
    trades: pd.DataFrame,
    strategy_name: str,
    filename: str = None,
):
    """Distribution des P&L par trade."""
    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=trades["pnl"],
        nbinsx=50,
        marker_color="#2196F3",
        opacity=0.8,
    ))
    fig.add_vline(x=0, line_dash="dash", line_color="red")
    fig.add_vline(x=trades["pnl"].mean(), line_dash="dash", line_color="green",
                  annotation_text=f"Mean: ${trades['pnl'].mean():.2f}")

    fig.update_layout(
        title=f"{strategy_name} — Trade P&L Distribution",
        xaxis_title="P&L ($)",
        yaxis_title="Count",
        template="plotly_dark",
        height=400,
    )

    safe = strategy_name.lower().replace(' ', '_').replace('/', '_').replace('+', '_')
    fname = filename or f"{safe}_distribution.html"
    path = os.path.join(config.OUTPUT_DIR, fname)
    fig.write_html(path)
    print(f"  [PLOT] Saved: {path}")
    return fig
