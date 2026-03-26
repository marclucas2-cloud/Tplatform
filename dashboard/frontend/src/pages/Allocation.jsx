import { useApi } from '../hooks/useApi'
import MetricCard from '../components/MetricCard'

export default function Allocation() {
  const { data: alloc } = useApi('/allocation', 60000)
  const { data: health } = useApi('/system/health', 30000)

  return (
    <div className="space-y-6">
      {/* System Health */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <MetricCard label="Alpaca" value={health?.alpaca_connected ? 'Connected' : 'Offline'} color={health?.alpaca_connected ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'} />
        <MetricCard label="Cache" value={`${health?.cache_files || 0} files`} suffix={` (${health?.cache_size_mb || 0} MB)`} />
        <MetricCard label="Tests" value={`${health?.tests_passing || 0} passing`} color="text-[var(--color-profit)]" />
        <MetricCard label="CRO Score" value={`${health?.cro_score || 0}/10`} />
      </div>

      {/* Allocation Detail */}
      {alloc && alloc.allocations && (
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-5">
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-4">Allocation Detail</h2>
          <div className="space-y-2">
            {Object.entries(alloc.allocations)
              .sort(([, a], [, b]) => b.pct - a.pct)
              .map(([sid, a]) => (
                <div key={sid} className="flex items-center gap-3">
                  <span className="text-sm w-48 truncate text-[var(--color-text-primary)]">{sid}</span>
                  <div className="flex-1 bg-[var(--color-bg-primary)] rounded-full h-4 overflow-hidden">
                    <div className="h-full bg-[var(--color-accent)] rounded-full" style={{ width: `${a.pct * 100}%` }} />
                  </div>
                  <span className="font-mono text-sm w-14 text-right">{(a.pct * 100).toFixed(1)}%</span>
                  <span className="font-mono text-sm w-20 text-right text-[var(--color-text-secondary)]">${a.capital?.toLocaleString()}</span>
                </div>
              ))}
          </div>
          <div className="mt-4 pt-3 border-t border-[var(--color-border)] text-xs text-[var(--color-text-secondary)]">
            Capital total: ${alloc.total_capital?.toLocaleString()} | Regime: {alloc.regime?.regime}
          </div>
        </div>
      )}
    </div>
  )
}
