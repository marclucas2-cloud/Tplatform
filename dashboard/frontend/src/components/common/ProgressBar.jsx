const STATUS_COLORS = {
  ok: {
    bar: 'bg-[var(--color-profit)]',
    text: 'text-[var(--color-profit)]',
  },
  warning: {
    bar: 'bg-[var(--color-warning)]',
    text: 'text-[var(--color-warning)]',
  },
  critical: {
    bar: 'bg-[var(--color-loss)]',
    text: 'text-[var(--color-loss)]',
  },
}

export default function ProgressBar({ label, current, max, unit = '', status = 'ok' }) {
  const pct = max > 0 ? Math.min((current / max) * 100, 100) : 0
  const colors = STATUS_COLORS[status] || STATUS_COLORS.ok

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <span className="text-xs text-[var(--color-text-secondary)]">{label}</span>
        <span className={`text-xs font-mono ${colors.text}`}>
          {typeof current === 'number' ? current.toLocaleString() : current}
          {' / '}
          {typeof max === 'number' ? max.toLocaleString() : max}
          {unit && ` ${unit}`}
        </span>
      </div>
      <div className="w-full bg-[var(--color-bg-primary)] rounded-full h-2 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${colors.bar}`}
          style={{ width: `${Math.max(pct, 1)}%` }}
        />
      </div>
    </div>
  )
}
