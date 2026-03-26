import { TrendingUp, TrendingDown, Minus } from 'lucide-react'

export default function MetricCard({ label, value, change, suffix = '', prefix = '', color }) {
  const isPositive = typeof change === 'number' ? change > 0 : null
  const isNegative = typeof change === 'number' ? change < 0 : null

  return (
    <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4 min-w-[160px]">
      <div className="text-xs text-[var(--color-text-secondary)] uppercase tracking-wider mb-1">{label}</div>
      <div className={`font-mono text-2xl font-semibold ${color || 'text-[var(--color-text-primary)]'}`}>
        {prefix}{typeof value === 'number' ? value.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 2 }) : value}{suffix}
      </div>
      {change !== undefined && change !== null && (
        <div className={`flex items-center gap-1 mt-1 text-sm font-mono ${isPositive ? 'text-[var(--color-profit)]' : isNegative ? 'text-[var(--color-loss)]' : 'text-[var(--color-text-secondary)]'}`}>
          {isPositive ? <TrendingUp size={14} /> : isNegative ? <TrendingDown size={14} /> : <Minus size={14} />}
          {typeof change === 'number' ? `${change >= 0 ? '+' : ''}${change.toFixed(2)}%` : change}
        </div>
      )}
    </div>
  )
}
