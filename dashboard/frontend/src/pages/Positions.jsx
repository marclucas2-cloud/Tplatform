import { useState, useMemo } from 'react'
import { useApi } from '../hooks/useApi'
import MetricCard from '../components/MetricCard'
import { ArrowUpRight, ArrowDownRight, ShieldAlert } from 'lucide-react'

const MODE_TABS = [
  { key: 'all', label: 'Toutes' },
  { key: 'live', label: '● Live' },
  { key: 'paper', label: '○ Paper' },
]

function PositionTable({ positions, showMode }) {
  if (positions.length === 0) {
    return (
      <div className="text-center py-12 text-[var(--color-text-secondary)]">
        <div className="text-3xl mb-2">—</div>
        <div>Aucune position ouverte</div>
      </div>
    )
  }

  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-xs text-[var(--color-text-secondary)] uppercase tracking-wider bg-[var(--color-bg-primary)]">
          <th className="text-left py-3 px-4">Ticker</th>
          <th className="text-left py-3">Dir</th>
          <th className="text-right py-3">Entry</th>
          <th className="text-right py-3">Current</th>
          <th className="text-right py-3">P&L</th>
          <th className="text-right py-3">SL</th>
          <th className="text-right py-3">Dist. SL</th>
          <th className="text-left py-3">Strategy</th>
          {showMode && <th className="text-right py-3 px-4">Mode</th>}
        </tr>
      </thead>
      <tbody>
        {positions.map((p, i) => {
          const slDist = p.stop_loss && p.current_price
            ? Math.abs((p.current_price - p.stop_loss) / p.current_price * 100)
            : null
          return (
            <tr key={i} className="border-t border-[var(--color-border)]/30 hover:bg-[var(--color-bg-hover)]">
              <td className="py-2.5 px-4 font-mono font-bold text-[var(--color-text-primary)]">{p.ticker}</td>
              <td className="py-2.5">
                <span className={`flex items-center gap-1 text-xs font-bold ${p.direction === 'LONG' ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}`}>
                  {p.direction === 'LONG' ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}
                  {p.direction}
                </span>
              </td>
              <td className="py-2.5 text-right font-mono">${p.entry_price?.toFixed(2)}</td>
              <td className="py-2.5 text-right font-mono">${p.current_price?.toFixed(2)}</td>
              <td className={`py-2.5 text-right font-mono font-bold ${p.pnl >= 0 ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}`}>
                {p.pnl >= 0 ? '+' : ''}${p.pnl?.toFixed(2)}
              </td>
              <td className="py-2.5 text-right font-mono text-xs text-[var(--color-loss)]">
                {p.stop_loss ? `$${p.stop_loss.toFixed(2)}` : '—'}
              </td>
              <td className={`py-2.5 text-right font-mono text-xs ${slDist && slDist < 2 ? 'text-[var(--color-warning)]' : 'text-[var(--color-text-secondary)]'}`}>
                {slDist != null ? `${slDist.toFixed(1)}%` : '—'}
              </td>
              <td className="py-2.5 text-xs text-[var(--color-text-secondary)]">{p.strategy}</td>
              {showMode && (
                <td className="py-2.5 px-4 text-right">
                  <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${
                    p._mode === 'live' ? 'bg-emerald-500/20 text-emerald-400' : 'bg-blue-500/20 text-blue-400'
                  }`}>
                    {p._mode === 'live' ? 'LIVE' : 'PAPER'}
                  </span>
                </td>
              )}
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

export default function Positions() {
  const { data, loading } = useApi('/positions', 10000)
  const { data: crossData } = useApi('/cross/exposure', 30000)
  const [mode, setMode] = useState('all')

  if (loading || !data) return <div className="text-center py-12 text-[var(--color-text-secondary)]">Loading...</div>

  // Merge positions from both sources and tag mode
  const paperPositions = (data.positions || []).map(p => ({ ...p, _mode: 'paper' }))
  const livePositions = (crossData?.positions || []).map(p => ({ ...p, _mode: 'live' }))
  const allPositions = [...livePositions, ...paperPositions]

  const filtered = mode === 'all' ? allPositions : allPositions.filter(p => p._mode === mode)

  // Risk if all stopped
  const riskIfStopped = filtered.reduce((sum, p) => {
    if (!p.stop_loss || !p.current_price || !p.shares) return sum
    const loss = Math.abs(p.shares) * Math.abs(p.current_price - p.stop_loss)
    return sum + loss
  }, 0)

  // P&L ouvert
  const openPnl = filtered.reduce((sum, p) => sum + (p.pnl || 0), 0)

  return (
    <div className="space-y-4">
      {/* Mode Tabs */}
      <div className="flex items-center gap-1">
        {MODE_TABS.map((tab) => (
          <button key={tab.key} onClick={() => setMode(tab.key)}
            className={`px-4 py-2 text-sm font-semibold rounded-lg transition-colors ${
              mode === tab.key
                ? 'bg-[var(--color-bg-card)] text-[var(--color-text-primary)] border border-[var(--color-border)]'
                : 'text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]'
            }`}>
            {tab.label}
          </button>
        ))}
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <MetricCard label="Positions" value={filtered.length} suffix=" ouvertes" />
        <MetricCard
          label="P&L Ouvert"
          value={openPnl}
          prefix="$"
          color={openPnl >= 0 ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}
        />
        <MetricCard
          label="Expo Long"
          value={data.exposure_long_pct || 0}
          suffix="%"
          color="text-[var(--color-profit)]"
        />
        <MetricCard
          label="Expo Short"
          value={data.exposure_short_pct || 0}
          suffix="%"
          color="text-[var(--color-loss)]"
        />
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
          <div className="flex items-center gap-1.5 mb-1">
            <ShieldAlert size={12} className="text-[var(--color-warning)]" />
            <span className="text-xs text-[var(--color-text-secondary)] uppercase">Risque si tout stoppe</span>
          </div>
          <div className="font-mono text-xl font-semibold text-[var(--color-loss)]">
            -${riskIfStopped.toFixed(0)}
          </div>
        </div>
      </div>

      {/* Positions Table */}
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl overflow-hidden">
        <PositionTable positions={filtered} showMode={mode === 'all'} />
      </div>
    </div>
  )
}
