import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useApi } from '../hooks/useApi'
import { TierBadge, PhaseBadge, PHASE_ORDER, PHASE_CONFIG } from '../components/StrategyBadge'
import { ChevronDown, ChevronUp, Shield, AlertTriangle } from 'lucide-react'

const ASSET_CLASSES = ['ALL', 'CRYPTO', 'FX', 'EU', 'US', 'FUTURES']

export default function Strategies() {
  const { data, loading } = useApi('/strategies', 60000)
  const [sortKey, setSortKey] = useState('sharpe')
  const [sortDir, setSortDir] = useState('desc')
  const [filterPhase, setFilterPhase] = useState('ALL')
  const [filterAsset, setFilterAsset] = useState('ALL')
  const navigate = useNavigate()

  if (loading || !data) return <div className="text-center py-12 text-[var(--color-text-secondary)]">Loading...</div>

  const strategies = [...(data.strategies || [])]

  // Filter
  let filtered = strategies
  if (filterPhase !== 'ALL') filtered = filtered.filter(s => s.phase === filterPhase)
  if (filterAsset !== 'ALL') filtered = filtered.filter(s => s.asset_class === filterAsset)

  // Sort within groups
  filtered.sort((a, b) => {
    const va = a[sortKey] ?? 0
    const vb = b[sortKey] ?? 0
    return sortDir === 'desc' ? vb - va : va - vb
  })

  const toggleSort = (key) => {
    if (sortKey === key) setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    else { setSortKey(key); setSortDir('desc') }
  }

  const SortIcon = ({ col }) => {
    if (sortKey !== col) return null
    return sortDir === 'desc' ? <ChevronDown size={12} /> : <ChevronUp size={12} />
  }

  // Group by phase
  const grouped = {}
  for (const phase of PHASE_ORDER) {
    const items = filtered.filter(s => s.phase === phase)
    if (items.length > 0) grouped[phase] = items
  }

  // Phase counts for filter buttons
  const phaseCounts = {}
  for (const s of strategies) {
    phaseCounts[s.phase] = (phaseCounts[s.phase] || 0) + 1
  }

  return (
    <div className="space-y-4">
      {/* Phase Filters */}
      <div className="flex flex-wrap gap-2">
        <button onClick={() => setFilterPhase('ALL')}
          className={`px-3 py-1 rounded-lg text-xs font-semibold transition-colors border border-[var(--color-border)] ${
            filterPhase === 'ALL' ? 'bg-[var(--color-accent)]/20 text-[var(--color-accent)]' : 'bg-[var(--color-bg-card)] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]'
          }`}>
          Toutes ({strategies.length})
        </button>
        {PHASE_ORDER.map(phase => {
          const count = phaseCounts[phase] || 0
          if (count === 0) return null
          const cfg = PHASE_CONFIG[phase]
          return (
            <button key={phase} onClick={() => setFilterPhase(phase)}
              className={`px-3 py-1 rounded-lg text-xs font-semibold transition-colors border ${
                filterPhase === phase ? `${cfg.bg} ${cfg.text} ${cfg.border}` : 'border-[var(--color-border)] bg-[var(--color-bg-card)] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]'
              }`}>
              {cfg.icon} {cfg.label} ({count})
            </button>
          )
        })}
        <div className="w-px bg-[var(--color-border)] mx-1" />
        {ASSET_CLASSES.map(ac => (
          <button key={ac} onClick={() => setFilterAsset(ac)}
            className={`px-3 py-1 rounded-lg text-xs transition-colors border border-[var(--color-border)] ${
              filterAsset === ac ? 'bg-[var(--color-info)]/20 text-[var(--color-info)]' : 'bg-[var(--color-bg-card)] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]'
            }`}>
            {ac === 'ALL' ? 'Tous' : ac}
          </button>
        ))}
      </div>

      {/* Table grouped by phase */}
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-[var(--color-text-secondary)] uppercase tracking-wider bg-[var(--color-bg-primary)]">
              <th className="text-left py-3 px-4">Phase</th>
              <th className="text-left py-3">Strategie</th>
              <th className="text-left py-3">Classe</th>
              <th className="text-left py-3">Broker</th>
              <th className="text-right py-3 cursor-pointer hover:text-[var(--color-text-primary)]" onClick={() => toggleSort('sharpe')}>
                <span className="flex items-center justify-end gap-1">Sharpe <SortIcon col="sharpe" /></span>
              </th>
              <th className="text-right py-3 cursor-pointer hover:text-[var(--color-text-primary)]" onClick={() => toggleSort('allocation_pct')}>
                <span className="flex items-center justify-end gap-1">Alloc <SortIcon col="allocation_pct" /></span>
              </th>
              <th className="text-right py-3 cursor-pointer hover:text-[var(--color-text-primary)]" onClick={() => toggleSort('pnl_5d')}>
                <span className="flex items-center justify-end gap-1">P&L 5j <SortIcon col="pnl_5d" /></span>
              </th>
              <th className="text-right py-3 px-4">Kill Switch</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(grouped).map(([phase, items]) => {
              const cfg = PHASE_CONFIG[phase]
              return [
                <tr key={`header-${phase}`} className={`${cfg.bg}`}>
                  <td colSpan={8} className={`py-2 px-4 text-xs font-bold ${cfg.text} uppercase tracking-wider`}>
                    {cfg.icon} {cfg.label} ({items.length})
                  </td>
                </tr>,
                ...items.map((s) => {
                  const killMargin = s.kill_margin_pct
                  const killDanger = killMargin < 50
                  return (
                    <tr key={s.id} onClick={() => navigate(`/strategies/${s.id}`)} className="border-t border-[var(--color-border)]/30 hover:bg-[var(--color-bg-hover)] transition-colors cursor-pointer">
                      <td className="py-2.5 px-4"><PhaseBadge phase={s.phase} /></td>
                      <td className="py-2.5 font-semibold text-[var(--color-text-primary)]">{s.name}</td>
                      <td className="py-2.5 text-xs text-[var(--color-text-secondary)]">{s.asset_class}</td>
                      <td className="py-2.5 text-xs font-mono text-[var(--color-text-secondary)]">{s.broker}</td>
                      <td className="py-2.5 text-right font-mono font-semibold">
                        {s.sharpe ? s.sharpe.toFixed(2) : '—'}
                      </td>
                      <td className="py-2.5 text-right font-mono">
                        {s.allocation_pct > 0 ? `${s.allocation_pct}%` : '—'}
                      </td>
                      <td className={`py-2.5 text-right font-mono ${s.pnl_5d >= 0 ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}`}>
                        {s.pnl_5d !== 0 ? `$${s.pnl_5d >= 0 ? '+' : ''}${s.pnl_5d.toFixed(0)}` : '—'}
                      </td>
                      <td className="py-2.5 px-4 text-right">
                        {s.kill_threshold !== 0 ? (
                          <div className="flex items-center justify-end gap-1">
                            {killDanger ? (
                              <AlertTriangle size={12} className="text-[var(--color-warning)]" />
                            ) : (
                              <Shield size={12} className="text-[var(--color-profit)]" />
                            )}
                            <span className={`font-mono text-xs ${killDanger ? 'text-[var(--color-warning)]' : 'text-[var(--color-text-secondary)]'}`}>
                              {killMargin}%
                            </span>
                          </div>
                        ) : (
                          <span className="text-xs text-[var(--color-text-secondary)]">—</span>
                        )}
                      </td>
                    </tr>
                  )
                })
              ]
            })}
          </tbody>
        </table>
      </div>

      {/* Summary */}
      <div className="flex flex-wrap gap-3 text-xs text-[var(--color-text-secondary)]">
        <span>{filtered.length} strategies affichees</span>
        <span>|</span>
        {PHASE_ORDER.map(p => {
          const count = phaseCounts[p] || 0
          if (count === 0) return null
          return <span key={p}>{PHASE_CONFIG[p].icon} {count} {PHASE_CONFIG[p].label.toLowerCase()}</span>
        })}
      </div>
    </div>
  )
}
