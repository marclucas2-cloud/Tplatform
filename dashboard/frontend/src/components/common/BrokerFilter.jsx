const BROKERS = [
  { value: 'all', label: 'Tous' },
  { value: 'ibkr', label: 'IBKR' },
  { value: 'binance', label: 'Binance' },
]

export default function BrokerFilter({ value = 'all', onChange }) {
  return (
    <div className="flex gap-1">
      {BROKERS.map((b) => (
        <button
          key={b.value}
          onClick={() => onChange?.(b.value)}
          className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors border ${
            value === b.value
              ? 'bg-[var(--color-accent)]/20 text-[var(--color-accent)] border-[var(--color-accent)]/30'
              : 'bg-[var(--color-bg-card)] text-[var(--color-text-secondary)] border-[var(--color-border)] hover:text-[var(--color-text-primary)] hover:bg-[var(--color-bg-hover)]'
          }`}
        >
          {b.label}
        </button>
      ))}
    </div>
  )
}
