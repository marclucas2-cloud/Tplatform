import { useApi } from '../hooks/useApi'
import { ArrowUpRight, ArrowDownRight } from 'lucide-react'

export default function Positions() {
  const { data, loading } = useApi('/positions', 10000)

  if (loading || !data) return <div className="text-center py-12 text-[var(--color-text-secondary)]">Loading...</div>

  const positions = data.positions || []

  return (
    <div className="space-y-4">
      {/* Exposure Summary */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
          <div className="text-xs text-[var(--color-text-secondary)] uppercase">Positions</div>
          <div className="font-mono text-2xl font-semibold">{data.count}</div>
        </div>
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
          <div className="text-xs text-[var(--color-text-secondary)] uppercase">Exposure Long</div>
          <div className={`font-mono text-xl ${data.exposure_long > 0 ? 'text-[var(--color-profit)]' : 'text-[var(--color-text-secondary)]'}`}>${data.exposure_long?.toLocaleString()}</div>
          <div className="text-xs text-[var(--color-text-secondary)]">{data.exposure_long_pct}%</div>
        </div>
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
          <div className="text-xs text-[var(--color-text-secondary)] uppercase">Exposure Short</div>
          <div className={`font-mono text-xl ${data.exposure_short > 0 ? 'text-[var(--color-loss)]' : 'text-[var(--color-text-secondary)]'}`}>${data.exposure_short?.toLocaleString()}</div>
          <div className="text-xs text-[var(--color-text-secondary)]">{data.exposure_short_pct}%</div>
        </div>
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
          <div className="text-xs text-[var(--color-text-secondary)] uppercase">Exposure Nette</div>
          <div className="font-mono text-xl">${data.exposure_net?.toLocaleString()}</div>
        </div>
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
          <div className="text-xs text-[var(--color-text-secondary)] uppercase">Cash Libre</div>
          <div className="font-mono text-xl">${((data.total_capital || 0) - Math.abs(data.exposure_long || 0) - Math.abs(data.exposure_short || 0)).toLocaleString()}</div>
        </div>
      </div>

      {/* Positions Table */}
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl overflow-hidden">
        {positions.length === 0 ? (
          <div className="text-center py-16 text-[var(--color-text-secondary)]">
            <div className="text-4xl mb-2">---</div>
            <div>Aucune position ouverte</div>
            <div className="text-xs mt-1">Les strategies intraday tradent entre 15:35 et 22:00 Paris</div>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-[var(--color-text-secondary)] uppercase tracking-wider bg-[var(--color-bg-primary)]">
                <th className="text-left py-3 px-4">Ticker</th>
                <th className="text-left py-3">Direction</th>
                <th className="text-right py-3">Shares</th>
                <th className="text-right py-3">Entry</th>
                <th className="text-right py-3">Current</th>
                <th className="text-right py-3">P&L</th>
                <th className="text-right py-3">P&L %</th>
                <th className="text-right py-3">Valeur</th>
                <th className="text-left py-3">Strategy</th>
                <th className="text-right py-3">SL</th>
                <th className="text-right py-3 px-4">TP</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p, i) => (
                <tr key={i} className="border-t border-[var(--color-border)]/50 hover:bg-[var(--color-bg-hover)]">
                  <td className="py-3 px-4 font-mono font-bold text-[var(--color-text-primary)]">{p.ticker}</td>
                  <td className="py-3">
                    <span className={`flex items-center gap-1 text-xs font-bold ${p.direction === 'LONG' ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}`}>
                      {p.direction === 'LONG' ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}
                      {p.direction}
                    </span>
                  </td>
                  <td className="py-3 text-right font-mono">{p.shares}</td>
                  <td className="py-3 text-right font-mono">${p.entry_price?.toFixed(2)}</td>
                  <td className="py-3 text-right font-mono">${p.current_price?.toFixed(2)}</td>
                  <td className={`py-3 text-right font-mono font-bold ${p.pnl >= 0 ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}`}>
                    ${p.pnl >= 0 ? '+' : ''}{p.pnl?.toFixed(2)}
                  </td>
                  <td className={`py-3 text-right font-mono ${p.pnl_pct >= 0 ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}`}>
                    {p.pnl_pct >= 0 ? '+' : ''}{p.pnl_pct?.toFixed(2)}%
                  </td>
                  <td className="py-3 text-right font-mono text-[var(--color-text-secondary)]">${Math.abs(p.market_value)?.toLocaleString()}</td>
                  <td className="py-3 text-xs text-[var(--color-text-secondary)]">{p.strategy}</td>
                  <td className="py-3 text-right font-mono text-xs text-[var(--color-loss)]">{p.stop_loss ? `$${p.stop_loss.toFixed(2)}` : '—'}</td>
                  <td className="py-3 text-right font-mono text-xs text-[var(--color-profit)] px-4">{p.take_profit ? `$${p.take_profit.toFixed(2)}` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
