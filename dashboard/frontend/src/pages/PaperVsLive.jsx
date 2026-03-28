import { useState } from 'react'
import { useApi } from '../hooks/useApi'
import MetricCard from '../components/MetricCard'
import { Info, AlertTriangle, Activity } from 'lucide-react'

const PERIODS = [
  { key: '30d', label: '30 jours' },
  { key: '90d', label: '90 jours' },
  { key: 'ytd', label: 'YTD' },
]

const SAMPLE_COMPARISON = {
  metrics: [
    { label: 'Sharpe', backtest: 2.10, paper: 1.65, live: 1.45, unit: '' },
    { label: 'Win Rate', backtest: 58, paper: 54, live: 52, unit: '%' },
    { label: 'Profit Factor', backtest: 2.30, paper: 1.90, live: 1.75, unit: '' },
    { label: 'Avg Trade P&L', backtest: 45, paper: 32, live: 28, unit: '$' },
    { label: 'Max Drawdown', backtest: -2.8, paper: -3.5, live: -3.1, unit: '%', invert: true },
    { label: 'Avg Slippage', backtest: 1.2, paper: 1.5, live: 2.1, unit: 'bps', invert: true },
  ],
  summary: {
    total_trades_paper: 142,
    total_trades_live: 87,
    signal_match_rate: 94.2,
    avg_execution_delay: 1.8,
  },
}

const SAMPLE_SIGNALS = [
  { date: '2026-03-27', symbol: 'EUR/USD', paper_signal: 'LONG', live_action: 'LONG', match: true },
  { date: '2026-03-26', symbol: 'EUR/GBP', paper_signal: 'SHORT', live_action: 'SHORT', match: true },
  { date: '2026-03-25', symbol: 'AUD/JPY', paper_signal: 'LONG', live_action: 'SKIP (margin)', match: false },
  { date: '2026-03-24', symbol: 'EU Gap', paper_signal: 'LONG', live_action: 'LONG (partial)', match: false },
  { date: '2026-03-22', symbol: 'MCL', paper_signal: 'SHORT', live_action: 'SHORT', match: true },
  { date: '2026-03-21', symbol: 'EUR/JPY', paper_signal: 'LONG', live_action: 'SKIP (kill switch)', match: false },
  { date: '2026-03-20', symbol: 'MES', paper_signal: 'LONG', live_action: 'LONG', match: true },
  { date: '2026-03-19', symbol: 'GBP/USD', paper_signal: 'SHORT', live_action: 'SHORT', match: true },
]

const SAMPLE_SLIPPAGE = [
  { strategy: 'EUR/USD Trend', model_bps: 1.2, real_bps: 1.8 },
  { strategy: 'EUR/GBP MR', model_bps: 1.0, real_bps: 1.3 },
  { strategy: 'EUR/JPY Carry', model_bps: 1.5, real_bps: 2.4 },
  { strategy: 'AUD/JPY Carry', model_bps: 1.8, real_bps: 2.9 },
  { strategy: 'GBP/USD Trend', model_bps: 1.3, real_bps: 1.9 },
  { strategy: 'EU Gap Open', model_bps: 2.0, real_bps: 3.1 },
  { strategy: 'MCL Brent Lag', model_bps: 2.5, real_bps: 3.8 },
  { strategy: 'MES Trend', model_bps: 1.8, real_bps: 2.2 },
]

function computeEcart(backtest, live, invert = false) {
  if (backtest === 0) return { pct: 0, severity: 'normal' }
  const raw = ((live - backtest) / Math.abs(backtest)) * 100
  const pct = invert ? -raw : raw
  const absPct = Math.abs(pct)

  let severity = 'normal'
  if (absPct > 50) severity = 'critical'
  else if (absPct >= 20) severity = 'warning'

  return { pct, severity }
}

function SeverityIcon({ severity }) {
  if (severity === 'critical') return <span className="text-[var(--color-loss)]" title="Ecart > 50%">&#x1F534;</span>
  if (severity === 'warning') return <span className="text-[var(--color-warning)]" title="Ecart 20-50%">&#x26A0;</span>
  return <span className="text-[var(--color-profit)]" title="Ecart < 20%">&#x25CF;</span>
}

function formatMetricValue(val, unit) {
  if (unit === '$') return `${val >= 0 ? '+' : ''}$${Math.abs(val)}`
  if (unit === '%') return `${val}%`
  if (unit === 'bps') return `${val}bps`
  return val.toFixed(2)
}

function SlippageBar({ label, model, real, maxVal }) {
  const modelWidth = (model / maxVal) * 100
  const realWidth = (real / maxVal) * 100
  const excess = real > model * 1.5

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className="text-[var(--color-text-secondary)] truncate w-32">{label}</span>
        <span className="font-mono text-[var(--color-text-primary)]">
          <span className="text-[var(--color-info)]">{model}</span>
          {' / '}
          <span className={excess ? 'text-[var(--color-loss)]' : 'text-[var(--color-warning)]'}>{real}</span>
          {' bps'}
        </span>
      </div>
      <div className="relative w-full h-4 bg-[var(--color-bg-primary)] rounded overflow-hidden">
        <div
          className="absolute top-0 left-0 h-full rounded opacity-40"
          style={{ width: `${realWidth}%`, backgroundColor: excess ? 'var(--color-loss)' : 'var(--color-warning)' }}
        />
        <div
          className="absolute top-0 left-0 h-full rounded"
          style={{ width: `${modelWidth}%`, backgroundColor: 'var(--color-info)' }}
        />
      </div>
    </div>
  )
}

export default function PaperVsLive() {
  const [period, setPeriod] = useState('30d')
  const { data: compData, loading: cLoad } = useApi(`/comparison?period=${period}`, 60000)
  const { data: signalData, loading: sigLoad } = useApi(`/comparison/signals?period=${period}`, 60000)

  const comparison = compData || SAMPLE_COMPARISON
  const signals = signalData?.divergences || SAMPLE_SIGNALS
  const slippage = compData?.slippage || SAMPLE_SLIPPAGE
  const summary = comparison.summary || SAMPLE_COMPARISON.summary
  const loading = cLoad && sigLoad

  if (loading && !compData && !signalData) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-[var(--color-text-secondary)] animate-pulse">Chargement comparaison...</div>
      </div>
    )
  }

  const metrics = comparison.metrics || SAMPLE_COMPARISON.metrics
  const maxSlippage = Math.max(...slippage.map((s) => Math.max(s.model_bps, s.real_bps))) * 1.2

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-[var(--color-text-primary)]">
          Paper vs Live — Comparaison
        </h1>
        <div className="flex gap-1 bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-lg p-0.5">
          {PERIODS.map((p) => (
            <button
              key={p.key}
              onClick={() => setPeriod(p.key)}
              className={`px-3 py-1.5 text-xs rounded-md transition-colors ${
                period === p.key
                  ? 'bg-[var(--color-accent)] text-white'
                  : 'text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]'
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Summary KPIs */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <MetricCard label="Trades Paper" value={summary.total_trades_paper} />
        <MetricCard label="Trades Live" value={summary.total_trades_live} />
        <MetricCard
          label="Signal Match"
          value={`${summary.signal_match_rate}%`}
          color={summary.signal_match_rate > 90 ? 'text-[var(--color-profit)]' : 'text-[var(--color-warning)]'}
        />
        <MetricCard
          label="Delai Execution"
          value={`${summary.avg_execution_delay}s`}
          color={summary.avg_execution_delay < 3 ? 'text-[var(--color-profit)]' : 'text-[var(--color-warning)]'}
        />
      </div>

      {/* Main Comparison Table */}
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
        <div className="flex items-center gap-2 mb-3">
          <Activity size={16} className="text-[var(--color-accent)]" />
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">
            Metriques — {PERIODS.find((p) => p.key === period)?.label}
          </h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-[var(--color-text-secondary)] uppercase tracking-wider border-b border-[var(--color-border)]">
                <th className="text-left py-2 px-2">Metrique</th>
                <th className="text-right py-2 px-2">Backtest</th>
                <th className="text-right py-2 px-2">Paper</th>
                <th className="text-right py-2 px-2">Live</th>
                <th className="text-right py-2 px-2">Ecart</th>
                <th className="text-center py-2 px-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {metrics.map((m, i) => {
                const ecart = computeEcart(m.backtest, m.live, m.invert)
                return (
                  <tr key={i} className="border-b border-[var(--color-border)]/50 hover:bg-[var(--color-bg-hover)]">
                    <td className="py-2.5 px-2 text-[var(--color-text-primary)] font-medium">{m.label}</td>
                    <td className="py-2.5 px-2 text-right font-mono text-[var(--color-text-secondary)]">
                      {formatMetricValue(m.backtest, m.unit)}
                    </td>
                    <td className="py-2.5 px-2 text-right font-mono text-[var(--color-info)]">
                      {formatMetricValue(m.paper, m.unit)}
                    </td>
                    <td className="py-2.5 px-2 text-right font-mono text-[var(--color-text-primary)] font-semibold">
                      {formatMetricValue(m.live, m.unit)}
                    </td>
                    <td className={`py-2.5 px-2 text-right font-mono font-semibold ${
                      ecart.severity === 'critical' ? 'text-[var(--color-loss)]' :
                      ecart.severity === 'warning' ? 'text-[var(--color-warning)]' :
                      'text-[var(--color-profit)]'
                    }`}>
                      {ecart.pct >= 0 ? '+' : ''}{ecart.pct.toFixed(0)}%
                    </td>
                    <td className="py-2.5 px-2 text-center">
                      <SeverityIcon severity={ecart.severity} />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        {/* Legend */}
        <div className="flex items-center gap-4 mt-3 pt-3 border-t border-[var(--color-border)] text-xs text-[var(--color-text-secondary)]">
          <span className="flex items-center gap-1">
            <span className="text-[var(--color-profit)]">&#x25CF;</span> &lt; 20% = normal
          </span>
          <span className="flex items-center gap-1">
            <span className="text-[var(--color-warning)]">&#x26A0;</span> 20-50% = surveillance
          </span>
          <span className="flex items-center gap-1">
            <span className="text-[var(--color-loss)]">&#x1F534;</span> &gt; 50% = probleme
          </span>
        </div>
      </div>

      {/* Equity Curves Placeholder */}
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-3">
          Courbes d'equity — Backtest / Paper / Live
        </h2>
        <div className="bg-[var(--color-bg-primary)] border border-[var(--color-border)] rounded-lg flex items-center justify-center h-56">
          <div className="text-center">
            <div className="flex items-center justify-center gap-6 mb-3">
              <span className="flex items-center gap-1.5 text-xs">
                <div className="w-4 h-0.5 bg-[var(--color-text-secondary)]" />
                Backtest
              </span>
              <span className="flex items-center gap-1.5 text-xs">
                <div className="w-4 h-0.5 bg-[var(--color-info)]" />
                Paper
              </span>
              <span className="flex items-center gap-1.5 text-xs">
                <div className="w-4 h-0.5 bg-[var(--color-profit)]" />
                Live
              </span>
            </div>
            <span className="text-xs text-[var(--color-text-secondary)]">Graphique equity superpose (a venir)</span>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Signal Divergences */}
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
          <div className="flex items-center gap-2 mb-3">
            <AlertTriangle size={16} className="text-[var(--color-warning)]" />
            <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">
              Divergences de signaux
            </h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-[var(--color-text-secondary)] uppercase tracking-wider border-b border-[var(--color-border)]">
                  <th className="text-left py-2 px-2">Date</th>
                  <th className="text-left py-2 px-2">Symbole</th>
                  <th className="text-left py-2 px-2">Signal Paper</th>
                  <th className="text-left py-2 px-2">Action Live</th>
                </tr>
              </thead>
              <tbody>
                {signals.map((s, i) => (
                  <tr key={i} className={`border-b border-[var(--color-border)]/50 hover:bg-[var(--color-bg-hover)] ${!s.match ? 'bg-yellow-500/5' : ''}`}>
                    <td className="py-2 px-2 font-mono text-xs text-[var(--color-text-secondary)]">{s.date}</td>
                    <td className="py-2 px-2 font-mono font-semibold text-[var(--color-text-primary)]">{s.symbol}</td>
                    <td className="py-2 px-2">
                      <span className={`text-xs font-semibold ${
                        s.paper_signal === 'LONG' ? 'text-[var(--color-profit)]' :
                        s.paper_signal === 'SHORT' ? 'text-[var(--color-loss)]' :
                        'text-[var(--color-text-secondary)]'
                      }`}>
                        {s.paper_signal}
                      </span>
                    </td>
                    <td className="py-2 px-2">
                      <span className={`text-xs font-semibold ${
                        s.match ? 'text-[var(--color-profit)]' : 'text-[var(--color-warning)]'
                      }`}>
                        {s.live_action}
                      </span>
                      {!s.match && (
                        <span className="ml-1 text-[var(--color-warning)]" title="Divergence">&#x26A0;</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Slippage Comparison */}
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-3">
            Slippage — Modele vs Reel (bps)
          </h2>
          <div className="space-y-3">
            {slippage.map((s, i) => (
              <SlippageBar
                key={i}
                label={s.strategy}
                model={s.model_bps}
                real={s.real_bps}
                maxVal={maxSlippage}
              />
            ))}
          </div>
          <div className="flex items-center gap-4 mt-4 pt-3 border-t border-[var(--color-border)] text-xs text-[var(--color-text-secondary)]">
            <span className="flex items-center gap-1.5">
              <div className="w-3 h-2 rounded-sm bg-[var(--color-info)]" />
              Modele
            </span>
            <span className="flex items-center gap-1.5">
              <div className="w-3 h-2 rounded-sm bg-[var(--color-warning)] opacity-40" />
              Reel
            </span>
            <span className="flex items-center gap-1.5">
              <div className="w-3 h-2 rounded-sm bg-[var(--color-loss)] opacity-40" />
              Reel &gt; 1.5x modele
            </span>
          </div>
        </div>
      </div>

      {/* Info Box */}
      <div className="bg-blue-500/10 border border-blue-500/30 rounded-xl p-4">
        <div className="flex items-start gap-3">
          <Info size={18} className="text-[var(--color-info)] mt-0.5 shrink-0" />
          <div>
            <h3 className="text-sm font-semibold text-[var(--color-info)] mb-1">Interpretation des ecarts</h3>
            <p className="text-xs text-[var(--color-text-secondary)] leading-relaxed">
              Un ecart backtest-live de 20-30% est normal et attendu (couts reels, slippage, latence).
              Au-dela de 50%, verifier : le modele de couts est-il realiste ? Y a-t-il du slippage excessif ?
              Les signaux divergent-ils (kill switch, margin insuffisante) ? Le signal sync fonctionne-t-il ?
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
