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

const PHASE_CONFIG = {
  LIVE:       { bg: 'bg-emerald-500/20', text: 'text-emerald-400', border: 'border-emerald-500/30', icon: '●', label: 'Live' },
  PROBATION:  { bg: 'bg-yellow-500/20',  text: 'text-yellow-400',  border: 'border-yellow-500/30',  icon: '◐', label: 'Probation' },
  PAPER:      { bg: 'bg-blue-500/20',    text: 'text-blue-400',    border: 'border-blue-500/30',    icon: '○', label: 'Paper' },
  WF_PENDING: { bg: 'bg-purple-500/20',  text: 'text-purple-400',  border: 'border-purple-500/30',  icon: '⏳', label: 'WF Pending' },
  CODE:       { bg: 'bg-gray-500/20',    text: 'text-gray-400',    border: 'border-gray-500/30',    icon: '⬜', label: 'Code' },
  REJECTED:   { bg: 'bg-red-500/20',     text: 'text-red-400',     border: 'border-red-500/30',     icon: '✕', label: 'Rejete' },
}

export function PhaseBadge({ phase }) {
  const c = PHASE_CONFIG[phase] || PHASE_CONFIG.CODE
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold ${c.bg} ${c.text} border ${c.border}`}>
      <span>{c.icon}</span>
      <span>{c.label}</span>
    </span>
  )
}

export const PHASE_ORDER = ['LIVE', 'PROBATION', 'PAPER', 'WF_PENDING', 'CODE', 'REJECTED']
export { PHASE_CONFIG }
