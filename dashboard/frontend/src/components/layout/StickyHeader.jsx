import { useState, useEffect } from 'react'
import { useApi } from '../../hooks/useApi'
import { TrendingUp, TrendingDown, Minus, Clock } from 'lucide-react'

function BrokerDot({ name, connected, mode }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className={`w-1.5 h-1.5 rounded-full ${
        connected ? (mode === 'PAPER' ? 'bg-blue-400' : 'bg-emerald-400 animate-pulse') : 'bg-gray-600'
      }`} />
      <span className="text-[10px] text-[var(--color-text-secondary)] font-mono">{name}</span>
    </div>
  )
}

function formatCurrency(v) {
  if (v == null) return '$0'
  const abs = Math.abs(v)
  if (abs >= 1000) return `${v < 0 ? '-' : ''}$${(abs / 1000).toFixed(1)}K`
  return `${v < 0 ? '-' : ''}$${abs.toFixed(0)}`
}

export default function StickyHeader() {
  const { data: nav } = useApi('/nav', 30000)
  const { data: health } = useApi('/system/health', 30000)
  const [clock, setClock] = useState('')

  useEffect(() => {
    const tick = () => {
      setClock(new Date().toLocaleTimeString('fr-FR', {
        timeZone: 'Europe/Paris', hour: '2-digit', minute: '2-digit'
      }))
    }
    tick()
    const id = setInterval(tick, 30000)
    return () => clearInterval(id)
  }, [])

  const pnl = nav?.pnl_live ?? 0
  const pnlPct = nav?.pnl_live_pct ?? 0
  const navLive = nav?.nav_live ?? 0
  const twr = nav?.twr_pct ?? 0

  return (
    <div className="sticky top-0 z-30 flex items-center gap-4 px-4 py-2 bg-[var(--color-bg-card)]/95 backdrop-blur border-b border-[var(--color-border)] text-xs">
      {/* NAV */}
      <div className="flex items-center gap-2">
        <span className="text-[var(--color-text-secondary)]">NAV</span>
        <span className="font-mono font-semibold text-[var(--color-text-primary)]">
          {formatCurrency(navLive)}
        </span>
      </div>

      {/* P&L */}
      <div className="flex items-center gap-1">
        {pnl >= 0 ? (
          <TrendingUp size={12} className="text-[var(--color-profit)]" />
        ) : (
          <TrendingDown size={12} className="text-[var(--color-loss)]" />
        )}
        <span className={`font-mono font-semibold ${pnl >= 0 ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}`}>
          {pnl >= 0 ? '+' : ''}{formatCurrency(pnl)}
        </span>
        <span className={`font-mono ${pnl >= 0 ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}`}>
          ({pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(1)}%)
        </span>
      </div>

      {/* TWR */}
      <div className="hidden md:flex items-center gap-1">
        <span className="text-[var(--color-text-secondary)]">TWR</span>
        <span className={`font-mono ${twr >= 0 ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}`}>
          {twr >= 0 ? '+' : ''}{twr.toFixed(1)}%
        </span>
      </div>

      {/* Separator */}
      <div className="hidden md:block w-px h-4 bg-[var(--color-border)]" />

      {/* Brokers */}
      <div className="hidden md:flex items-center gap-3">
        <BrokerDot name="IBKR" connected={health?.ibkr_connected} mode="LIVE" />
        <BrokerDot name="BNB" connected={health?.binance_connected} mode="LIVE" />
        <BrokerDot name="ALP" connected={health?.alpaca_connected} mode="PAPER" />
      </div>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Clock */}
      <div className="flex items-center gap-1 text-[var(--color-text-secondary)]">
        <Clock size={11} />
        <span className="font-mono">{clock} CET</span>
      </div>
    </div>
  )
}
