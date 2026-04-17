import { useState, useEffect } from 'react'
import { useApi } from '../hooks/useApi'
import { Shield, BookOpen, Activity, AlertTriangle, CheckCircle, XCircle, Clock, RefreshCw } from 'lucide-react'

const STATE_COLORS = {
  RUNNING_LIVE: 'var(--color-profit)',
  RUNNING_PAPER: 'var(--color-info, #3b82f6)',
  BLOCKED: 'var(--color-loss)',
  DEGRADED: 'var(--color-warning)',
  STOPPED: 'var(--color-text-muted, #6b7280)',
  PREFLIGHT_FAILED: 'var(--color-loss)',
  STARTING: 'var(--color-warning)',
  GREEN: 'var(--color-profit)',
  UNKNOWN: 'var(--color-text-muted, #6b7280)',
}

function StateIndicator({ state }) {
  const color = STATE_COLORS[state] || 'var(--color-text-secondary)'
  return (
    <span
      className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-semibold"
      style={{ backgroundColor: `color-mix(in srgb, ${color} 15%, transparent)`, color }}
    >
      <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: color }} />
      {state}
    </span>
  )
}

function BookCard({ bookId, data }) {
  const health = data || {}
  const state = health.status || health.state || 'UNKNOWN'
  const mode = health.mode || health.mode_authorized || '?'

  return (
    <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <BookOpen size={16} className="text-[var(--color-text-secondary)]" />
          <span className="text-sm font-semibold text-[var(--color-text-primary)]">{bookId}</span>
        </div>
        <StateIndicator state={state} />
      </div>
      <div className="space-y-1.5 text-xs text-[var(--color-text-secondary)]">
        <div className="flex justify-between">
          <span>Mode</span>
          <span className="font-mono text-[var(--color-text-primary)]">{mode}</span>
        </div>
        {health.broker && (
          <div className="flex justify-between">
            <span>Broker</span>
            <span className="font-mono text-[var(--color-text-primary)]">{health.broker}</span>
          </div>
        )}
        {health.capital_budget_usd != null && (
          <div className="flex justify-between">
            <span>Capital</span>
            <span className="font-mono text-[var(--color-text-primary)]">
              ${Number(health.capital_budget_usd).toLocaleString()}
            </span>
          </div>
        )}
        {health.live_strats_count != null && (
          <div className="flex justify-between">
            <span>Live strats</span>
            <span className="font-mono text-[var(--color-text-primary)]">{health.live_strats_count}</span>
          </div>
        )}
        {health.kill_book_active && (
          <div className="mt-2 p-2 rounded bg-[color-mix(in_srgb,var(--color-loss)_10%,transparent)] text-[var(--color-loss)] text-xs">
            Kill switch: {health.kill_book_reason}
          </div>
        )}
        {health.safety_mode_active && (
          <div className="mt-2 p-2 rounded bg-[color-mix(in_srgb,var(--color-warning)_10%,transparent)] text-[var(--color-warning)] text-xs">
            Safety mode: {health.safety_mode_reason}
          </div>
        )}
      </div>
    </div>
  )
}

function WhitelistTable({ whitelist }) {
  if (!whitelist || !Array.isArray(whitelist)) return null
  const byStatus = {}
  whitelist.forEach(s => {
    const st = s.status || 'unknown'
    if (!byStatus[st]) byStatus[st] = []
    byStatus[st].push(s)
  })
  const order = ['live_core', 'live_probation', 'paper_only', 'disabled']
  return (
    <div className="space-y-3">
      {order.map(status => {
        const items = byStatus[status]
        if (!items || items.length === 0) return null
        return (
          <div key={status}>
            <div className="flex items-center gap-2 mb-1.5">
              <StateIndicator state={status === 'live_core' ? 'RUNNING_LIVE' : status === 'live_probation' ? 'DEGRADED' : status === 'disabled' ? 'BLOCKED' : 'STOPPED'} />
              <span className="text-xs text-[var(--color-text-secondary)]">{items.length} strategies</span>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-1">
              {items.map(s => (
                <div key={s.strategy_id || s.name} className="text-xs font-mono text-[var(--color-text-primary)] bg-[var(--color-bg-hover)] rounded px-2 py-1 truncate">
                  {s.strategy_id || s.name}
                </div>
              ))}
            </div>
          </div>
        )
      })}
    </div>
  )
}

export default function Governance() {
  const { data: booksStatus, loading: booksLoading, refetch: refetchBooks } = useApi('/api/governance/desk-status')
  const { data: whitelist, loading: wlLoading } = useApi('/api/governance/live-whitelist')
  const { data: booksHealth } = useApi('/api/books/status')

  const books = booksStatus?.books || booksHealth?.books || {}
  const ts = booksStatus?.ts || new Date().toISOString()

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Shield size={24} className="text-[var(--color-text-primary)]" />
          <div>
            <h1 className="text-lg font-bold text-[var(--color-text-primary)]">Governance</h1>
            <p className="text-xs text-[var(--color-text-secondary)]">
              Desk status, books, whitelist, kill switches
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-[var(--color-text-muted)]">
            {new Date(ts).toLocaleTimeString()}
          </span>
          <button
            onClick={refetchBooks}
            className="p-1.5 rounded-lg hover:bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)]"
          >
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      {/* Global health */}
      {booksHealth?.global && (
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
          <div className="flex items-center gap-3">
            <Activity size={16} className="text-[var(--color-text-secondary)]" />
            <span className="text-sm text-[var(--color-text-secondary)]">Global Status</span>
            <StateIndicator state={booksHealth.global} />
          </div>
        </div>
      )}

      {/* Books grid */}
      <div>
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-3">Books</h2>
        {booksLoading ? (
          <div className="text-sm text-[var(--color-text-secondary)] animate-pulse">Loading...</div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
            {Object.entries(books).map(([id, data]) => (
              <BookCard key={id} bookId={id} data={data} />
            ))}
          </div>
        )}
      </div>

      {/* Live whitelist */}
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-3 flex items-center gap-2">
          <CheckCircle size={16} className="text-[var(--color-text-secondary)]" />
          Live Whitelist
        </h2>
        {wlLoading ? (
          <div className="text-sm text-[var(--color-text-secondary)] animate-pulse">Loading...</div>
        ) : whitelist?.error ? (
          <div className="text-sm text-[var(--color-loss)]">{whitelist.error}</div>
        ) : (
          <WhitelistTable whitelist={
            Array.isArray(whitelist) ? whitelist :
            whitelist?.strategies ? Object.entries(whitelist.strategies).map(([k, v]) => ({ strategy_id: k, ...v })) :
            []
          } />
        )}
      </div>
    </div>
  )
}
