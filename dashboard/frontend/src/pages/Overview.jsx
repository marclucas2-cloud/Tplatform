import { useApi } from '../hooks/useApi'
import MetricCard from '../components/MetricCard'
import { TierBadge, StatusDot } from '../components/StrategyBadge'
import { ArrowUpRight, ArrowDownRight, Clock } from 'lucide-react'

export default function Overview() {
  const { data: portfolio, loading: pLoad } = useApi('/portfolio', 30000)
  const { data: posData } = useApi('/positions', 15000)
  const { data: stratData } = useApi('/strategies', 60000)

  if (pLoad || !portfolio) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-[var(--color-text-secondary)] animate-pulse">Loading portfolio...</div>
      </div>
    )
  }

  const positions = posData?.positions || []
  const strategies = stratData?.strategies || []

  return (
    <div className="space-y-6">
      {/* KPI Row */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
        <MetricCard
          label="Equity"
          value={portfolio.equity}
          change={portfolio.total_return_pct}
          prefix="$"
        />
        <MetricCard
          label="P&L Jour"
          value={portfolio.pnl_day}
          change={portfolio.pnl_day_pct}
          prefix="$"
          color={portfolio.pnl_day >= 0 ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}
        />
        <MetricCard
          label="Positions"
          value={portfolio.positions_count}
          suffix=" ouvertes"
        />
        <MetricCard
          label="P&L Non-realise"
          value={portfolio.pnl_unrealized}
          prefix="$"
          color={portfolio.pnl_unrealized >= 0 ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}
        />
        <MetricCard
          label="CRO Score"
          value="9.5/10"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Strategies */}
        <div className="lg:col-span-1 bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-3">
            Strategies ({strategies.length})
          </h2>
          <div className="space-y-2">
            {strategies.map((s) => (
              <div key={s.id} className="flex items-center justify-between py-1.5 px-2 rounded-lg hover:bg-[var(--color-bg-hover)] transition-colors">
                <div className="flex items-center gap-2">
                  <StatusDot status={s.status} />
                  <TierBadge tier={s.tier} />
                  <span className="text-sm text-[var(--color-text-primary)]">{s.name}</span>
                </div>
                <div className="flex items-center gap-3">
                  <span className="font-mono text-xs text-[var(--color-text-secondary)]">
                    {s.allocation_pct}%
                  </span>
                  <span className={`font-mono text-xs ${s.pnl_5d >= 0 ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}`}>
                    ${s.pnl_5d >= 0 ? '+' : ''}{s.pnl_5d.toFixed(0)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Positions */}
        <div className="lg:col-span-2 bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">
              Positions Ouvertes ({positions.length})
            </h2>
            {posData && (
              <div className="flex gap-3 text-xs font-mono">
                <span className="text-[var(--color-profit)]">Long: ${posData.exposure_long?.toLocaleString()}</span>
                <span className="text-[var(--color-loss)]">Short: ${posData.exposure_short?.toLocaleString()}</span>
                <span className="text-[var(--color-text-secondary)]">Net: ${posData.exposure_net?.toLocaleString()}</span>
              </div>
            )}
          </div>

          {positions.length === 0 ? (
            <div className="text-center py-8 text-[var(--color-text-secondary)] text-sm">
              Aucune position ouverte
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-[var(--color-text-secondary)] uppercase tracking-wider border-b border-[var(--color-border)]">
                  <th className="text-left py-2 px-2">Ticker</th>
                  <th className="text-left py-2">Dir</th>
                  <th className="text-right py-2">Shares</th>
                  <th className="text-right py-2">Entry</th>
                  <th className="text-right py-2">Current</th>
                  <th className="text-right py-2">P&L</th>
                  <th className="text-right py-2 px-2">Strategy</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p, i) => (
                  <tr key={i} className="border-b border-[var(--color-border)]/50 hover:bg-[var(--color-bg-hover)]">
                    <td className="py-2 px-2 font-mono font-semibold">{p.ticker}</td>
                    <td className="py-2">
                      <span className={`flex items-center gap-1 text-xs font-semibold ${p.direction === 'LONG' ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}`}>
                        {p.direction === 'LONG' ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}
                        {p.direction}
                      </span>
                    </td>
                    <td className="py-2 text-right font-mono">{p.shares}</td>
                    <td className="py-2 text-right font-mono">${p.entry_price?.toFixed(2)}</td>
                    <td className="py-2 text-right font-mono">${p.current_price?.toFixed(2)}</td>
                    <td className={`py-2 text-right font-mono font-semibold ${p.pnl >= 0 ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}`}>
                      ${p.pnl >= 0 ? '+' : ''}{p.pnl?.toFixed(2)}
                    </td>
                    <td className="py-2 px-2 text-right text-xs text-[var(--color-text-secondary)]">{p.strategy}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* System Status */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-3 flex items-center gap-2">
          <Clock size={14} className="text-[var(--color-text-secondary)]" />
          <span className="text-xs text-[var(--color-text-secondary)]">
            Updated: {new Date(portfolio.timestamp).toLocaleTimeString()}
          </span>
        </div>
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-3">
          <span className="text-xs text-[var(--color-text-secondary)]">
            Regime: <span className="font-mono text-[var(--color-text-primary)]">{portfolio.regime}</span>
          </span>
        </div>
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-3">
          <span className="text-xs text-[var(--color-text-secondary)]">
            ATR SPY: <span className="font-mono text-[var(--color-text-primary)]">{portfolio.regime_detail?.atr_pct}%</span>
          </span>
        </div>
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-3">
          <span className="text-xs text-[var(--color-text-secondary)]">
            Capital Initial: <span className="font-mono text-[var(--color-text-primary)]">$100,000</span>
          </span>
        </div>
      </div>
    </div>
  )
}
