import { Link, useLocation } from 'react-router-dom'
import { BarChart3, Target, LineChart, Settings, Activity, Layers } from 'lucide-react'

const NAV = [
  { path: '/', label: 'Overview', icon: BarChart3 },
  { path: '/strategies', label: 'Strategies', icon: Target },
  { path: '/positions', label: 'Positions', icon: Activity },
  { path: '/analytics', label: 'Analytics', icon: LineChart },
  { path: '/allocation', label: 'Allocation', icon: Layers },
]

export default function Header({ regime, marketOpen }) {
  const location = useLocation()

  return (
    <header className="border-b border-[var(--color-border)] bg-[var(--color-bg-card)]">
      <div className="max-w-[1440px] mx-auto px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-6">
          <h1 className="text-lg font-semibold text-[var(--color-text-primary)] tracking-tight">
            Trading Platform
          </h1>
          <nav className="flex gap-1">
            {NAV.map(({ path, label, icon: Icon }) => (
              <Link
                key={path}
                to={path}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition-colors ${
                  location.pathname === path
                    ? 'bg-[var(--color-accent)]/15 text-[var(--color-accent)]'
                    : 'text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] hover:bg-[var(--color-bg-hover)]'
                }`}
              >
                <Icon size={15} />
                {label}
              </Link>
            ))}
          </nav>
        </div>
        <div className="flex items-center gap-4 text-sm">
          {regime && (
            <span className={`px-2 py-0.5 rounded text-xs font-mono ${
              regime.includes('BULL') ? 'bg-green-500/15 text-green-400' : 'bg-red-500/15 text-red-400'
            }`}>
              {regime}
            </span>
          )}
          <span className={`flex items-center gap-1.5 ${marketOpen ? 'text-[var(--color-profit)]' : 'text-[var(--color-text-secondary)]'}`}>
            <span className={`w-2 h-2 rounded-full ${marketOpen ? 'bg-green-500 animate-pulse' : 'bg-gray-500'}`} />
            {marketOpen ? 'Market Open' : 'Market Closed'}
          </span>
        </div>
      </div>
    </header>
  )
}
