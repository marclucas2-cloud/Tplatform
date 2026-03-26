const TIER_COLORS = {
  S: { bg: 'bg-yellow-500/20', text: 'text-yellow-400', border: 'border-yellow-500/30' },
  A: { bg: 'bg-blue-500/20', text: 'text-blue-400', border: 'border-blue-500/30' },
  B: { bg: 'bg-gray-500/20', text: 'text-gray-400', border: 'border-gray-500/30' },
  C: { bg: 'bg-gray-600/20', text: 'text-gray-500', border: 'border-gray-600/30' },
}

const STATUS_COLORS = {
  ACTIVE: 'text-[var(--color-profit)]',
  PAUSED: 'text-[var(--color-warning)]',
  RETIRED: 'text-[var(--color-loss)]',
  PROBATOIRE: 'text-[var(--color-accent)]',
  DISABLED_BEAR: 'text-[var(--color-warning)]',
}

export function TierBadge({ tier }) {
  const c = TIER_COLORS[tier] || TIER_COLORS.C
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-bold ${c.bg} ${c.text} border ${c.border}`}>
      {tier}
    </span>
  )
}

export function StatusDot({ status }) {
  const color = STATUS_COLORS[status] || 'text-gray-500'
  return <span className={`inline-block w-2 h-2 rounded-full ${color} bg-current`} title={status} />
}
