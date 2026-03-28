const DEFAULT_OPTIONS = ['7j', '30j', '90j', 'YTD', 'All']

export default function PeriodSelector({ value, onChange, options = DEFAULT_OPTIONS }) {
  return (
    <div className="flex gap-1">
      {options.map((opt) => (
        <button
          key={opt}
          onClick={() => onChange?.(opt)}
          className={`px-2.5 py-1 rounded-full text-xs font-mono transition-colors ${
            value === opt
              ? 'bg-[var(--color-info)]/20 text-[var(--color-info)]'
              : 'text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] hover:bg-[var(--color-bg-hover)]'
          }`}
        >
          {opt}
        </button>
      ))}
    </div>
  )
}
