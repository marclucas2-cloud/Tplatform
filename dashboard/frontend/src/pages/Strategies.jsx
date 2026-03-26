import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useApi } from '../hooks/useApi'
import { TierBadge, StatusDot } from '../components/StrategyBadge'
import { ChevronDown, ChevronUp, Shield, AlertTriangle } from 'lucide-react'

export default function Strategies() {
  const { data, loading } = useApi('/strategies', 60000)
  const [sortKey, setSortKey] = useState('sharpe')
  const [sortDir, setSortDir] = useState('desc')
  const [filterTier, setFilterTier] = useState('all')
  const [filterType, setFilterType] = useState('all')
  const navigate = useNavigate()

  if (loading || !data) return <div className="text-center py-12 text-[var(--color-text-secondary)]">Loading...</div>

  const strategies = [...(data.strategies || [])]

  // Filter
  let filtered = strategies
  if (filterTier !== 'all') filtered = filtered.filter(s => s.tier === filterTier)
  if (filterType !== 'all') filtered = filtered.filter(s => s.type === filterType)

  // Sort
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

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex flex-wrap gap-2">
        {['all', 'S', 'A', 'B', 'C'].map(t => (
          <button key={t} onClick={() => setFilterTier(t)}
            className={`px-3 py-1 rounded-lg text-xs font-semibold transition-colors ${
              filterTier === t ? 'bg-[var(--color-accent)]/20 text-[var(--color-accent)]' : 'bg-[var(--color-bg-card)] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]'
            } border border-[var(--color-border)]`}>
            {t === 'all' ? 'Tous' : `Tier ${t}`}
          </button>
        ))}
        <div className="w-px bg-[var(--color-border)] mx-1" />
        {['all', 'intraday', 'daily', 'monthly'].map(t => (
          <button key={t} onClick={() => setFilterType(t)}
            className={`px-3 py-1 rounded-lg text-xs transition-colors ${
              filterType === t ? 'bg-[var(--color-info)]/20 text-[var(--color-info)]' : 'bg-[var(--color-bg-card)] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]'
            } border border-[var(--color-border)]`}>
            {t === 'all' ? 'Tous types' : t}
          </button>
        ))}
      </div>

      {/* Table */}
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-[var(--color-text-secondary)] uppercase tracking-wider bg-[var(--color-bg-primary)]">
              <th className="text-left py-3 px-4">Status</th>
              <th className="text-left py-3">Tier</th>
              <th className="text-left py-3">Strategie</th>
              <th className="text-left py-3">Type</th>
              <th className="text-right py-3 cursor-pointer hover:text-[var(--color-text-primary)]" onClick={() => toggleSort('sharpe')}>
                <span className="flex items-center justify-end gap-1">Sharpe <SortIcon col="sharpe" /></span>
              </th>
              <th className="text-right py-3 cursor-pointer hover:text-[var(--color-text-primary)]" onClick={() => toggleSort('allocation_pct')}>
                <span className="flex items-center justify-end gap-1">Alloc <SortIcon col="allocation_pct" /></span>
              </th>
              <th className="text-right py-3">Capital</th>
              <th className="text-right py-3 cursor-pointer hover:text-[var(--color-text-primary)]" onClick={() => toggleSort('pnl_5d')}>
                <span className="flex items-center justify-end gap-1">P&L 5j <SortIcon col="pnl_5d" /></span>
              </th>
              <th className="text-right py-3 px-4">Kill Switch</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((s) => {
              const killMargin = s.kill_margin_pct
              const killDanger = killMargin < 50
              return (
                <tr key={s.id} onClick={() => navigate(`/strategies/${s.id}`)} className="border-t border-[var(--color-border)]/50 hover:bg-[var(--color-bg-hover)] transition-colors cursor-pointer">
                  <td className="py-3 px-4"><StatusDot status={s.status} /></td>
                  <td className="py-3"><TierBadge tier={s.tier} /></td>
                  <td className="py-3 font-semibold text-[var(--color-text-primary)]">{s.name}</td>
                  <td className="py-3 text-[var(--color-text-secondary)] text-xs">{s.type}</td>
                  <td className="py-3 text-right font-mono font-semibold">{s.sharpe.toFixed(2)}</td>
                  <td className="py-3 text-right font-mono">{s.allocation_pct}%</td>
                  <td className="py-3 text-right font-mono text-[var(--color-text-secondary)]">
                    ${s.capital?.toLocaleString()}
                  </td>
                  <td className={`py-3 text-right font-mono ${s.pnl_5d >= 0 ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}`}>
                    ${s.pnl_5d >= 0 ? '+' : ''}{s.pnl_5d.toFixed(0)}
                  </td>
                  <td className="py-3 px-4 text-right">
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
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Summary */}
      <div className="flex gap-4 text-xs text-[var(--color-text-secondary)]">
        <span>{filtered.length} strategies affichees</span>
        <span>|</span>
        <span>{strategies.filter(s => s.status === 'ACTIVE').length} actives</span>
        <span>{strategies.filter(s => s.status === 'PAUSED').length} pausees</span>
        <span>{strategies.filter(s => s.status === 'DISABLED_BEAR').length} desactivees (bear)</span>
      </div>
    </div>
  )
}
