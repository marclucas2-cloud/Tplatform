import { useState, useRef } from 'react'
import { Info } from 'lucide-react'

export default function Tooltip({ text, children }) {
  const [visible, setVisible] = useState(false)
  const timeoutRef = useRef(null)

  const show = () => {
    clearTimeout(timeoutRef.current)
    setVisible(true)
  }

  const hide = () => {
    timeoutRef.current = setTimeout(() => setVisible(false), 150)
  }

  return (
    <span className="relative inline-flex items-center gap-1">
      {children}
      <span
        className="inline-flex cursor-help"
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
        tabIndex={0}
      >
        <Info size={13} className="text-[var(--color-text-secondary)] hover:text-[var(--color-info)] transition-colors" />
        {visible && (
          <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 z-50 pointer-events-none">
            <span className="block px-3 py-2 rounded-lg bg-[var(--color-bg-hover)] border border-[var(--color-border)] text-xs text-[var(--color-text-primary)] whitespace-nowrap shadow-lg max-w-[280px]">
              {text}
            </span>
            {/* Arrow */}
            <span className="block w-2 h-2 bg-[var(--color-bg-hover)] border-b border-r border-[var(--color-border)] rotate-45 absolute left-1/2 -translate-x-1/2 -bottom-1" />
          </span>
        )}
      </span>
    </span>
  )
}
