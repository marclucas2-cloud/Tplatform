import { useState, useEffect } from 'react'
import { Link, useLocation } from 'react-router-dom'
import {
  BarChart3, Activity, Target, Shield, BookOpen, GitCompare,
  TrendingUp, Server, Receipt, Network, Menu, X, Bitcoin, LogOut, ShieldCheck
} from 'lucide-react'
import { useApi } from '../../hooks/useApi'
import { useAuth } from '../../context/AuthContext'

const NAV_ITEMS = [
  { path: '/', label: 'Vue d\'ensemble', icon: BarChart3 },
  { path: '/positions', label: 'Positions', icon: Activity },
  { path: '/strategies', label: 'Strategies', icon: Target },
  { path: '/crypto', label: 'Crypto', icon: Bitcoin },
  { path: '/risk', label: 'Risque', icon: Shield },
  { path: '/journal', label: 'Journal', icon: BookOpen },
  { path: '/paper-vs-live', label: 'Paper vs Live', icon: GitCompare },
  { path: '/analytics', label: 'Analytique', icon: TrendingUp },
  { path: '/system', label: 'Systeme', icon: Server },
  { path: '/tax', label: 'Fiscalite', icon: Receipt },
  { path: '/cross', label: 'Cross-Portfolio', icon: Network },
  { path: '/governance', label: 'Governance', icon: ShieldCheck },
]

export default function Sidebar() {
  const location = useLocation()
  const { logout } = useAuth()
  const [collapsed, setCollapsed] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)
  const { data: health } = useApi('/system/health', 30000)

  // Close mobile menu on route change
  useEffect(() => {
    setMobileOpen(false)
  }, [location.pathname])

  // Close mobile menu on resize to desktop
  useEffect(() => {
    const handleResize = () => {
      if (window.innerWidth >= 768) setMobileOpen(false)
    }
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  const isActive = (path) => {
    if (path === '/') return location.pathname === '/'
    return location.pathname.startsWith(path)
  }

  const sidebarContent = (
    <div className="flex flex-col h-full">
      {/* Logo / Title */}
      <div className="flex items-center justify-between px-4 py-4 border-b border-[var(--color-border)]">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-[var(--color-accent)]/20 flex items-center justify-center">
            <BarChart3 size={16} className="text-[var(--color-accent)]" />
          </div>
          <div>
            <div className="text-sm font-semibold text-[var(--color-text-primary)] leading-tight">
              Trading Platform
            </div>
            <div className="text-[10px] text-[var(--color-text-secondary)] font-mono">
              v2.0 LIVE
            </div>
          </div>
        </div>
        {/* Mobile close */}
        <button
          onClick={() => setMobileOpen(false)}
          className="md:hidden p-1 rounded hover:bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)]"
        >
          <X size={18} />
        </button>
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-3 px-2 space-y-0.5 overflow-y-auto">
        {NAV_ITEMS.map(({ path, label, icon: Icon }) => (
          <Link
            key={path}
            to={path}
            className={`flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors ${
              isActive(path)
                ? 'bg-[var(--color-accent)]/15 text-[var(--color-accent)] font-semibold'
                : 'text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] hover:bg-[var(--color-bg-hover)]'
            }`}
          >
            <Icon size={16} />
            <span>{label}</span>
          </Link>
        ))}
      </nav>

      {/* Broker Status */}
      <div className="px-4 py-3 border-t border-[var(--color-border)]">
        <div className="text-[10px] text-[var(--color-text-secondary)] uppercase tracking-wider mb-2">
          Brokers
        </div>
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className={`w-2 h-2 rounded-full ${
                health?.ibkr_connected ? 'bg-[var(--color-profit)] animate-pulse' : 'bg-gray-600'
              }`} />
              <span className="text-xs text-[var(--color-text-secondary)]">IBKR</span>
            </div>
            <span className={`text-[10px] font-mono ${
              health?.ibkr_connected ? 'text-[var(--color-profit)]' : 'text-gray-600'
            }`}>
              {health?.ibkr_connected ? 'LIVE' : 'OFF'}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className={`w-2 h-2 rounded-full ${
                health?.binance_connected ? 'bg-[var(--color-profit)] animate-pulse' : 'bg-gray-600'
              }`} />
              <span className="text-xs text-[var(--color-text-secondary)]">Binance</span>
            </div>
            <span className={`text-[10px] font-mono ${
              health?.binance_connected ? 'text-[var(--color-profit)]' : 'text-gray-600'
            }`}>
              {health?.binance_connected ? 'LIVE' : 'OFF'}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className={`w-2 h-2 rounded-full ${
                health?.alpaca_connected ? 'bg-[var(--color-warning)] animate-pulse' : 'bg-gray-600'
              }`} />
              <span className="text-xs text-[var(--color-text-secondary)]">Alpaca</span>
            </div>
            <span className={`text-[10px] font-mono ${
              health?.alpaca_connected ? 'text-[var(--color-warning)]' : 'text-gray-600'
            }`}>
              {health?.alpaca_connected ? 'PAPER' : 'OFF'}
            </span>
          </div>
        </div>
        {/* Worker status */}
        <div className="mt-2 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${
              health?.worker_running ? 'bg-[var(--color-profit)] animate-pulse' : 'bg-gray-600'
            }`} />
            <span className="text-xs text-[var(--color-text-secondary)]">Worker</span>
          </div>
          <span className={`text-[10px] font-mono ${
            health?.worker_running ? 'text-[var(--color-profit)]' : 'text-gray-600'
          }`}>
            {health?.worker_running ? 'ON' : 'OFF'}
          </span>
        </div>
      </div>

      {/* Logout */}
      <div className="px-4 py-3 border-t border-[var(--color-border)]">
        <button
          onClick={logout}
          className="flex items-center gap-2 w-full px-3 py-2 rounded-lg text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-loss)] hover:bg-[var(--color-bg-hover)] transition-colors"
        >
          <LogOut size={14} />
          <span>Deconnexion</span>
        </button>
      </div>
    </div>
  )

  return (
    <>
      {/* Mobile hamburger button */}
      <button
        onClick={() => setMobileOpen(true)}
        className="fixed top-3 left-3 z-50 md:hidden p-2 rounded-lg bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
      >
        <Menu size={20} />
      </button>

      {/* Mobile overlay */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/60 md:hidden"
          onClick={() => setMobileOpen(false)}
        />
      )}

      {/* Sidebar - mobile */}
      <aside
        className={`fixed top-0 left-0 z-50 h-screen w-[220px] bg-[var(--color-bg-card)] border-r border-[var(--color-border)] transform transition-transform duration-200 ease-in-out md:hidden ${
          mobileOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        {sidebarContent}
      </aside>

      {/* Sidebar - desktop */}
      <aside className="hidden md:flex md:flex-shrink-0 w-[220px] h-screen sticky top-0 bg-[var(--color-bg-card)] border-r border-[var(--color-border)]">
        {sidebarContent}
      </aside>
    </>
  )
}
