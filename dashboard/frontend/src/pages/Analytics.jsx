import { useApi } from '../hooks/useApi'
import { TierBadge } from '../components/StrategyBadge'

export default function Analytics() {
  const { data: stratData } = useApi('/strategies', 60000)
  const { data: regime } = useApi('/regime', 60000)
  const { data: alloc } = useApi('/allocation', 60000)

  const strategies = stratData?.strategies || []

  return (
    <div className="space-y-6">
      {/* Regime */}
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-5">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-3">Regime de Marche</h2>
        {regime && !regime.error ? (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <div className="text-xs text-[var(--color-text-secondary)]">Regime</div>
              <div className={`font-mono text-lg font-bold ${regime.bull ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}`}>
                {regime.regime}
              </div>
            </div>
            <div>
              <div className="text-xs text-[var(--color-text-secondary)]">SPY vs SMA200</div>
              <div className={`font-mono text-lg ${regime.bull ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}`}>
                {regime.bull ? 'AU-DESSUS' : 'EN-DESSOUS'}
              </div>
              <div className="text-xs font-mono text-[var(--color-text-secondary)]">
                ${regime.spy_close?.toFixed(2)} vs ${regime.sma200?.toFixed(2)}
              </div>
            </div>
            <div>
              <div className="text-xs text-[var(--color-text-secondary)]">ATR 20j</div>
              <div className="font-mono text-lg">{regime.atr_pct}%</div>
            </div>
            <div>
              <div className="text-xs text-[var(--color-text-secondary)]">Ajustements</div>
              <div className="text-sm">
                {!regime.bull && <div className="text-[var(--color-loss)]">Bear: -30% allocation</div>}
                {regime.high_vol && <div className="text-[var(--color-warning)]">HiVol: OpEx +30%, DoW -50%</div>}
                {regime.low_vol && <div className="text-[var(--color-info)]">LoVol: -20% global</div>}
                {regime.bull && !regime.high_vol && !regime.low_vol && <div className="text-[var(--color-profit)]">Aucun (normal)</div>}
              </div>
            </div>
          </div>
        ) : (
          <div className="text-[var(--color-text-secondary)]">Chargement...</div>
        )}
      </div>

      {/* Strategy Ranking */}
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-5">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-3">Ranking par Sharpe</h2>
        <div className="space-y-2">
          {strategies.map((s) => {
            const maxSharpe = strategies[0]?.sharpe || 1
            const width = Math.min((s.sharpe / maxSharpe) * 100, 100)
            return (
              <div key={s.id} className="flex items-center gap-3">
                <TierBadge tier={s.tier} />
                <span className="text-sm w-44 truncate">{s.name}</span>
                <div className="flex-1 bg-[var(--color-bg-primary)] rounded-full h-3 overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${
                      s.sharpe >= 5 ? 'bg-[var(--color-profit)]' :
                      s.sharpe >= 2 ? 'bg-[var(--color-info)]' :
                      s.sharpe >= 1 ? 'bg-[var(--color-warning)]' : 'bg-[var(--color-loss)]'
                    }`}
                    style={{ width: `${Math.max(width, 2)}%` }}
                  />
                </div>
                <span className="font-mono text-sm w-16 text-right">{s.sharpe.toFixed(2)}</span>
              </div>
            )
          })}
        </div>
      </div>

      {/* Allocation Tiers */}
      {alloc && alloc.tiers && (
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-5">
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-3">Allocation par Tier</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {Object.entries(alloc.tiers).map(([tier, strats]) => {
              const totalPct = strats.reduce((sum, s) => sum + (s.pct || 0), 0) * 100
              const totalCap = strats.reduce((sum, s) => sum + (s.capital || 0), 0)
              return (
                <div key={tier} className="border border-[var(--color-border)] rounded-lg p-3">
                  <div className="flex items-center gap-2 mb-2">
                    <TierBadge tier={tier} />
                    <span className="font-mono text-sm font-semibold">{totalPct.toFixed(1)}%</span>
                    <span className="font-mono text-xs text-[var(--color-text-secondary)]">${totalCap.toLocaleString()}</span>
                  </div>
                  <div className="space-y-1">
                    {strats.map((s) => (
                      <div key={s.id} className="flex justify-between text-xs">
                        <span className="text-[var(--color-text-secondary)] truncate">{s.name}</span>
                        <span className="font-mono">{(s.pct * 100).toFixed(1)}%</span>
                      </div>
                    ))}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Kill Switch Status */}
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-5">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-3">Kill Switch Status</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {strategies.map((s) => {
            const margin = s.kill_margin_pct
            const danger = margin < 30
            const warning = margin < 60
            return (
              <div key={s.id} className={`border rounded-lg p-3 ${
                danger ? 'border-[var(--color-loss)]/50 bg-red-500/5' :
                warning ? 'border-[var(--color-warning)]/30' : 'border-[var(--color-border)]'
              }`}>
                <div className="flex justify-between items-center mb-1">
                  <span className="text-sm font-semibold">{s.name}</span>
                  <span className={`font-mono text-xs ${danger ? 'text-[var(--color-loss)]' : warning ? 'text-[var(--color-warning)]' : 'text-[var(--color-profit)]'}`}>
                    {margin}%
                  </span>
                </div>
                <div className="w-full bg-[var(--color-bg-primary)] rounded-full h-2 overflow-hidden">
                  <div
                    className={`h-full rounded-full ${
                      danger ? 'bg-[var(--color-loss)]' : warning ? 'bg-[var(--color-warning)]' : 'bg-[var(--color-profit)]'
                    }`}
                    style={{ width: `${Math.min(Math.max(margin, 0), 100)}%` }}
                  />
                </div>
                <div className="flex justify-between text-xs text-[var(--color-text-secondary)] mt-1">
                  <span>P&L 5j: ${s.pnl_5d >= 0 ? '+' : ''}{s.pnl_5d.toFixed(0)}</span>
                  <span>Seuil: ${s.kill_threshold.toFixed(0)}</span>
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
