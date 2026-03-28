import { useApi } from '../hooks/useApi'
import MetricCard from '../components/MetricCard'
import { Info, Shield, AlertTriangle } from 'lucide-react'

const DEFAULT_BROKER = { long_pct: 0, short_pct: 0, net_pct: 0, cash_pct: 0, capital: 0 }
const DEFAULT_COMBINED = { net_long_total: 0, gross_total: 0, total_capital: 0, correlation: 0 }

function ExposureBar({ label, value, max, color }) {
  const safeMax = max || 1
  const pct = Math.min(Math.abs(value / safeMax) * 100, 100)
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className="text-[var(--color-text-secondary)]">{label}</span>
        <span className="font-mono text-[var(--color-text-primary)]">
          ${Math.abs(value).toLocaleString()} / ${(max || 0).toLocaleString()} = {(Math.abs(value) / safeMax * 100).toFixed(1)}%
        </span>
      </div>
      <div className="w-full h-3 bg-[var(--color-bg-primary)] rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{
            width: `${pct}%`,
            backgroundColor: color,
          }}
        />
      </div>
    </div>
  )
}

function CorrelationBadge({ value }) {
  let color, label
  if (value < 0.5) {
    color = 'var(--color-profit)'
    label = 'Faible'
  } else if (value < 0.7) {
    color = 'var(--color-warning)'
    label = 'Moderee'
  } else {
    color = 'var(--color-loss)'
    label = 'Elevee'
  }

  return (
    <div className="flex items-center gap-3">
      <span className="font-mono text-3xl font-bold" style={{ color }}>
        {value.toFixed(2)}
      </span>
      <div>
        <span className="text-xs px-2 py-0.5 rounded-full font-semibold" style={{ backgroundColor: `${color}20`, color }}>
          {label}
        </span>
        <p className="text-xs text-[var(--color-text-secondary)] mt-1">Correlation glissante 30j</p>
      </div>
    </div>
  )
}

function PortfolioPanel({ title, capital, data, showEarn = false }) {
  const items = [
    { label: 'Long', value: data.long_pct, color: 'var(--color-profit)' },
    { label: 'Short', value: data.short_pct, color: 'var(--color-loss)' },
    { label: 'Net', value: data.net_pct, color: 'var(--color-info)' },
    { label: 'Cash', value: data.cash_pct, color: 'var(--color-text-secondary)' },
  ]
  if (showEarn) {
    items.push({ label: 'Earn', value: data.earn_pct, color: 'var(--color-accent)' })
  }

  return (
    <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4 flex-1">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">{title}</h2>
        <span className="font-mono text-sm text-[var(--color-text-secondary)]">${capital.toLocaleString()}</span>
      </div>
      <div className="space-y-3">
        {items.map((item) => (
          <div key={item.label} className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: item.color }} />
              <span className="text-sm text-[var(--color-text-secondary)]">{item.label}</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-24 h-2 bg-[var(--color-bg-primary)] rounded-full overflow-hidden">
                <div
                  className="h-full rounded-full"
                  style={{ width: `${Math.min(item.value, 100)}%`, backgroundColor: item.color }}
                />
              </div>
              <span className="font-mono text-sm text-[var(--color-text-primary)] w-12 text-right">{item.value}%</span>
            </div>
          </div>
        ))}
      </div>
      <div className="mt-4 pt-3 border-t border-[var(--color-border)]">
        <div className="flex justify-between text-xs">
          <span className="text-[var(--color-text-secondary)]">Exposition nette</span>
          <span className="font-mono text-[var(--color-text-primary)]">${data.net_usd?.toLocaleString()}</span>
        </div>
      </div>
    </div>
  )
}

export default function CrossPortfolio() {
  const { data: exposureData, loading: eLoad } = useApi('/cross/exposure', 60000)
  const { data: stressData, loading: sLoad } = useApi('/cross/stress', 60000)

  const loading = eLoad && sLoad

  if (loading && !exposureData && !stressData) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-[var(--color-text-secondary)] animate-pulse">Chargement cross-portfolio...</div>
      </div>
    )
  }

  const ibkr = { ...DEFAULT_BROKER, ...exposureData?.ibkr }
  const binance = { ...DEFAULT_BROKER, earn_pct: 0, ...exposureData?.binance }
  const rawCombined = exposureData?.combined || {}
  const combined = {
    ...DEFAULT_COMBINED,
    ...rawCombined,
    capital: rawCombined.total_capital ?? DEFAULT_COMBINED.total_capital,
    net_usd: rawCombined.net_long_total ?? DEFAULT_COMBINED.net_long_total,
    net_pct: rawCombined.total_capital > 0
      ? ((rawCombined.net_long_total ?? 0) / rawCombined.total_capital * 100).toFixed(1)
      : 0,
    gross_usd: rawCombined.gross_total ?? DEFAULT_COMBINED.gross_total,
    gross_pct: rawCombined.total_capital > 0
      ? ((rawCombined.gross_total ?? 0) / rawCombined.total_capital * 100).toFixed(1)
      : 0,
    correlation: rawCombined.correlation ?? 0,
  }

  // Compute net_usd for each broker from pct + capital
  ibkr.net_usd = ibkr.net_usd ?? Math.round(ibkr.capital * ibkr.net_pct / 100)
  binance.net_usd = binance.net_usd ?? Math.round(binance.capital * binance.net_pct / 100)

  const exposure = { ibkr, binance, combined }
  const stress = stressData?.scenarios || []

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-[var(--color-text-primary)]">
          Cross-Portfolio IBKR + Binance
        </h1>
        <span className="font-mono text-sm text-[var(--color-text-secondary)]">
          Capital total: ${combined.capital.toLocaleString()}
        </span>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <MetricCard label="Net Combine" value={combined.net_usd} prefix="$" change={combined.net_pct} />
        <MetricCard label="Gross Combine" value={combined.gross_usd} prefix="$" />
        <MetricCard
          label="Correlation"
          value={combined.correlation.toFixed(2)}
          color={combined.correlation < 0.5 ? 'text-[var(--color-profit)]' : combined.correlation < 0.7 ? 'text-[var(--color-warning)]' : 'text-[var(--color-loss)]'}
        />
        <MetricCard label="Capital" value={combined.capital} prefix="$" />
      </div>

      {/* Side-by-side Panels */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <PortfolioPanel
          title="IBKR — FX / EU / Futures"
          capital={exposure.ibkr.capital}
          data={exposure.ibkr}
        />
        <PortfolioPanel
          title="Binance — Crypto FR"
          capital={exposure.binance.capital}
          data={exposure.binance}
          showEarn
        />
      </div>

      {/* Exposure Bars */}
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4 space-y-4">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">Exposition combinee</h2>
        <ExposureBar
          label="Exposition nette (long - short)"
          value={combined.net_usd}
          max={combined.capital}
          color="var(--color-info)"
        />
        <ExposureBar
          label="Exposition brute (long + short)"
          value={combined.gross_usd}
          max={combined.capital}
          color={combined.gross_pct > 80 ? 'var(--color-loss)' : combined.gross_pct > 60 ? 'var(--color-warning)' : 'var(--color-profit)'}
        />
        <div className="flex gap-4 text-xs text-[var(--color-text-secondary)] pt-1">
          <span>Limite nette: 70%</span>
          <span>Limite brute: 100%</span>
          <span>Cash reserve min: 20%</span>
        </div>
      </div>

      {/* Correlation Section */}
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-4">
          Correlation IBKR / Binance
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div>
            <CorrelationBadge value={combined.correlation} />
            <div className="mt-4 space-y-2 text-xs text-[var(--color-text-secondary)]">
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 rounded-full bg-[var(--color-profit)]" />
                <span>&lt; 0.50 — Diversification efficace</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 rounded-full bg-[var(--color-warning)]" />
                <span>0.50 - 0.70 — Surveiller, reduire si hausse</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 rounded-full bg-[var(--color-loss)]" />
                <span>&gt; 0.70 — Alerte, deleveraging automatique</span>
              </div>
            </div>
          </div>
          <div className="bg-[var(--color-bg-primary)] border border-[var(--color-border)] rounded-lg flex items-center justify-center h-40">
            <span className="text-xs text-[var(--color-text-secondary)]">Graphique correlation glissante 30j (a venir)</span>
          </div>
        </div>
      </div>

      {/* Stress Scenarios */}
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
        <div className="flex items-center gap-2 mb-3">
          <Shield size={16} className="text-[var(--color-warning)]" />
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">
            Scenarios de stress
          </h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-[var(--color-text-secondary)] uppercase tracking-wider border-b border-[var(--color-border)]">
                <th className="text-left py-2 px-2">Scenario</th>
                <th className="text-right py-2 px-2">IBKR</th>
                <th className="text-right py-2 px-2">Binance</th>
                <th className="text-right py-2 px-2">Combine</th>
                <th className="text-right py-2 px-2">% Capital</th>
              </tr>
            </thead>
            <tbody>
              {stress.map((s, i) => {
                const combinedLoss = s.combined ?? 0
                const pctCapital = combined.capital > 0 ? (combinedLoss / combined.capital * 100).toFixed(1) : '0.0'
                const severity = Math.abs(combinedLoss) > 3000 ? 'text-[var(--color-loss)]' : Math.abs(combinedLoss) > 1500 ? 'text-[var(--color-warning)]' : 'text-[var(--color-text-primary)]'
                return (
                  <tr key={i} className="border-b border-[var(--color-border)]/50 hover:bg-[var(--color-bg-hover)]">
                    <td className="py-2.5 px-2 text-[var(--color-text-primary)]">{s.name ?? s.scenario ?? '-'}</td>
                    <td className="py-2.5 px-2 text-right font-mono text-[var(--color-loss)]">
                      -${Math.abs(s.ibkr ?? 0).toLocaleString()}
                    </td>
                    <td className="py-2.5 px-2 text-right font-mono text-[var(--color-loss)]">
                      -${Math.abs(s.binance ?? 0).toLocaleString()}
                    </td>
                    <td className={`py-2.5 px-2 text-right font-mono font-semibold ${severity}`}>
                      -${Math.abs(combinedLoss).toLocaleString()}
                    </td>
                    <td className={`py-2.5 px-2 text-right font-mono ${severity}`}>
                      {pctCapital}%
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Info Box */}
      <div className="bg-blue-500/10 border border-blue-500/30 rounded-xl p-4">
        <div className="flex items-start gap-3">
          <Info size={18} className="text-[var(--color-info)] mt-0.5 shrink-0" />
          <div>
            <h3 className="text-sm font-semibold text-[var(--color-info)] mb-1">Protection crash (mars 2020)</h3>
            <p className="text-xs text-[var(--color-text-secondary)] leading-relaxed">
              En cas de crash type mars 2020, perte max controlee de -$1,250 grace aux kill switches.
              Le deleveraging progressif (30% a -1%, 50% a -1.5%, 100% a -2%) et les brackets OCA
              broker-side limitent l'impact meme si la connexion est perdue.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
