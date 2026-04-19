import { useState, useMemo } from 'react'
import { useApi } from '../hooks/useApi'
import MetricCard from '../components/MetricCard'
import { Download, ChevronUp, ChevronDown, ChevronLeft, ChevronRight } from 'lucide-react'
import Tooltip from '../components/common/Tooltip'
import { TOOLTIPS } from '../utils/tooltips'

const PERIODS = [
  { key: '7d', label: '7j' },
  { key: '30d', label: '30j' },
  { key: '90d', label: '90j' },
  { key: 'ytd', label: 'YTD' },
]

const PAGE_SIZE = 20

const BROKER_FILTERS = [
  { key: 'all', label: 'Tous' },
  { key: 'ibkr', label: 'IBKR' },
  { key: 'binance', label: 'Binance' },
  { key: 'alpaca', label: 'Alpaca' },
]

const CRYPTO_PATTERN = /^(BTC|ETH|BNB|SOL|ADA|DOGE|XRP|DOT|AVAX|MATIC|LINK|UNI|AAVE|ATOM)/i
const FUTURES_PATTERN = /^(MES|MNQ|MCL|MGC|M2K|FIB|FESX|NQ|ES|CL|GC)/i

function detectBroker(trade) {
  if (trade.broker === 'IBKR' || trade.asset_class === 'futures') return 'ibkr'
  if (trade.trade_source === 'crypto') return 'binance'
  const sym = (trade.symbol || '').toUpperCase()
  if (sym.includes('USDT') || sym.includes('USDC') || CRYPTO_PATTERN.test(sym)) return 'binance'
  if (FUTURES_PATTERN.test(sym)) return 'ibkr'
  return 'alpaca'
}

const COLUMNS = [
  { key: 'date', label: 'Date', align: 'left' },
  { key: 'symbol', label: 'Symbole', align: 'left' },
  { key: 'broker', label: 'Broker', align: 'left' },
  { key: 'side', label: 'Sens', align: 'left' },
  { key: 'entry_price', label: 'Entree', align: 'right', format: 'price' },
  { key: 'exit_price', label: 'Sortie', align: 'right', format: 'price' },
  { key: 'pnl', label: 'P&L', align: 'right', format: 'pnl' },
  { key: 'duration', label: 'Duree', align: 'right' },
  { key: 'strategy', label: 'Strategie', align: 'right' },
]

function formatPrice(v) {
  if (v == null) return '-'
  return '$' + Number(v).toFixed(2)
}

function formatPnl(v) {
  if (v == null) return '-'
  const n = Number(v)
  const sign = n >= 0 ? '+' : ''
  return `${sign}$${n.toFixed(2)}`
}

function filterByPeriod(trades, period) {
  const now = new Date()
  let cutoff
  switch (period) {
    case '7d':
      cutoff = new Date(now.getTime() - 7 * 86400000)
      break
    case '30d':
      cutoff = new Date(now.getTime() - 30 * 86400000)
      break
    case '90d':
      cutoff = new Date(now.getTime() - 90 * 86400000)
      break
    case 'ytd':
      cutoff = new Date(now.getFullYear(), 0, 1)
      break
    default:
      cutoff = new Date(0)
  }
  return trades.filter((t) => new Date(t.date) >= cutoff)
}

const MODE_TABS = [
  { key: 'all', label: 'Tous', color: 'text-[var(--color-text-primary)]' },
  { key: 'live', label: 'Live', color: 'text-emerald-400' },
  { key: 'paper', label: 'Paper', color: 'text-blue-400' },
]

export default function Journal() {
  const [modeTab, setModeTab] = useState('all')
  const modeParam = modeTab === 'all' ? '' : `&mode=${modeTab}`
  const { data: tradesData, loading: tLoad } = useApi(`/trades?limit=200${modeParam}`, 60000)
  const { data: calendarData } = useApi('/trades/calendar', 120000)
  const { data: costsData } = useApi('/trades/costs', 120000)

  const [period, setPeriod] = useState('30d')
  const [brokerFilter, setBrokerFilter] = useState('all')
  const [sortCol, setSortCol] = useState('date')
  const [sortDir, setSortDir] = useState('desc')
  const [page, setPage] = useState(0)

  const allTrades = useMemo(() =>
    (tradesData?.trades || []).map((t) => ({ ...t, _broker: detectBroker(t) })),
    [tradesData]
  )

  const filtered = useMemo(() => {
    let trades = filterByPeriod(allTrades, period)
    if (brokerFilter !== 'all') {
      trades = trades.filter((t) => t._broker === brokerFilter)
    }
    return trades
  }, [allTrades, period, brokerFilter])

  const sorted = useMemo(() => {
    const copy = [...filtered]
    copy.sort((a, b) => {
      let va = a[sortCol]
      let vb = b[sortCol]
      if (typeof va === 'string') va = va.toLowerCase()
      if (typeof vb === 'string') vb = vb.toLowerCase()
      if (va == null) return 1
      if (vb == null) return -1
      if (va < vb) return sortDir === 'asc' ? -1 : 1
      if (va > vb) return sortDir === 'asc' ? 1 : -1
      return 0
    })
    return copy
  }, [filtered, sortCol, sortDir])

  const totalPages = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE))
  const paginated = sorted.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)

  const stats = useMemo(() => {
    if (filtered.length === 0) return null
    const wins = filtered.filter((t) => (t.pnl ?? 0) > 0)
    const losses = filtered.filter((t) => (t.pnl ?? 0) < 0)
    const totalPnl = filtered.reduce((s, t) => s + (t.pnl ?? 0), 0)
    const commissions = filtered.reduce((s, t) => s + (t.commission ?? 0), 0)
    const best = filtered.reduce((b, t) => Math.max(b, t.pnl ?? 0), -Infinity)
    const worst = filtered.reduce((w, t) => Math.min(w, t.pnl ?? 0), Infinity)
    return {
      total: filtered.length,
      wins: wins.length,
      winRate: ((wins.length / filtered.length) * 100).toFixed(1),
      losses: losses.length,
      totalPnl,
      best,
      worst,
      commissions,
    }
  }, [filtered])

  function handleSort(col) {
    if (sortCol === col) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortCol(col)
      setSortDir('desc')
    }
    setPage(0)
  }

  function exportCSV() {
    if (sorted.length === 0) return
    const headers = COLUMNS.map((c) => c.label).join(',')
    const rows = sorted.map((t) =>
      COLUMNS.map((c) => {
        const v = t[c.key]
        return v != null ? String(v) : ''
      }).join(',')
    )
    const csv = [headers, ...rows].join('\n')
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `trades_${period}_${new Date().toISOString().slice(0, 10)}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  if (tLoad && !tradesData) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-[var(--color-text-secondary)] animate-pulse">Chargement journal...</div>
      </div>
    )
  }

  const calDays = calendarData?.days || []

  return (
    <div className="space-y-6">
      {/* Mode Tabs: Live / Paper / All */}
      <div className="flex items-center gap-1">
        {MODE_TABS.map((tab) => (
          <button key={tab.key} onClick={() => { setModeTab(tab.key); setPage(0) }}
            className={`px-4 py-2 text-sm font-semibold rounded-lg transition-colors ${
              modeTab === tab.key
                ? `bg-[var(--color-bg-card)] ${tab.color} border border-[var(--color-border)]`
                : 'text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]'
            }`}>
            {tab.key === 'live' && '● '}{tab.key === 'paper' && '○ '}{tab.label}
            {tradesData && ` (${tradesData.count})`}
          </button>
        ))}
      </div>

      {/* Cost Summary */}
      {costsData && !costsData.error && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <MetricCard label={<Tooltip text={TOOLTIPS.commissions}>Commissions</Tooltip>} value={costsData.total_commissions} prefix="$" />
          <MetricCard label={<Tooltip text={TOOLTIPS.interest}>Interets</Tooltip>} value={costsData.total_interest} prefix="$" />
          <MetricCard label={<Tooltip text={TOOLTIPS.slippage}>Slippage moy</Tooltip>} value={costsData.total_slippage_bps_avg} suffix=" bps" />
          <MetricCard label="Cout/trade" value={costsData.cost_per_trade_avg} prefix="$" />
          <MetricCard
            label={<Tooltip text={TOOLTIPS.cost_pct}>Couts % P&L</Tooltip>}
            value={costsData.cost_as_pct_of_pnl}
            suffix="%"
            color={costsData.healthy ? 'text-[var(--color-profit)]' : 'text-[var(--color-warning)]'}
          />
        </div>
      )}

      {/* Period filter + Broker filter + Export */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-3">
          <div className="flex gap-1">
            {PERIODS.map((p) => (
              <button
                key={p.key}
                onClick={() => {
                  setPeriod(p.key)
                  setPage(0)
                }}
                className={`px-3 py-1.5 text-xs font-semibold rounded-lg transition-colors ${
                  period === p.key
                    ? 'bg-[var(--color-accent)] text-white'
                    : 'bg-[var(--color-bg-card)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] border border-[var(--color-border)]'
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>
          <div className="w-px h-5 bg-[var(--color-border)]" />
          <div className="flex gap-1">
            {BROKER_FILTERS.map((bf) => (
              <button
                key={bf.key}
                onClick={() => {
                  setBrokerFilter(bf.key)
                  setPage(0)
                }}
                className={`px-3 py-1.5 text-xs font-semibold rounded-lg transition-colors ${
                  brokerFilter === bf.key
                    ? 'bg-[var(--color-accent)] text-white'
                    : 'bg-[var(--color-bg-card)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] border border-[var(--color-border)]'
                }`}
              >
                {bf.label}
              </button>
            ))}
          </div>
        </div>
        <button
          onClick={exportCSV}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg bg-[var(--color-bg-card)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] border border-[var(--color-border)] transition-colors"
        >
          <Download size={13} />
          Exporter CSV
        </button>
      </div>

      {/* Summary KPIs */}
      {stats ? (
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
          <MetricCard label="Trades" value={stats.total} />
          <MetricCard
            label="Gagnants"
            value={`${stats.wins} (${stats.winRate}%)`}
            color="text-[var(--color-profit)]"
          />
          <MetricCard
            label="Perdants"
            value={stats.losses}
            color="text-[var(--color-loss)]"
          />
          <MetricCard
            label="P&L Total"
            value={stats.totalPnl}
            prefix="$"
            color={stats.totalPnl >= 0 ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}
          />
          <MetricCard
            label="Meilleur"
            value={stats.best}
            prefix="$"
            color="text-[var(--color-profit)]"
          />
          <MetricCard
            label="Pire"
            value={stats.worst}
            prefix="$"
            color="text-[var(--color-loss)]"
          />
          <MetricCard
            label="Commissions"
            value={stats.commissions}
            prefix="-$"
            color="text-[var(--color-text-secondary)]"
          />
        </div>
      ) : (
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-6 text-center text-sm text-[var(--color-text-secondary)]">
          Aucune donnee pour cette periode
        </div>
      )}

      {/* Trade Table */}
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4 overflow-x-auto">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">
            Historique ({sorted.length} trades)
          </h2>
          <div className="flex items-center gap-2 text-xs text-[var(--color-text-secondary)]">
            <span>
              Page {page + 1}/{totalPages}
            </span>
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="p-1 rounded hover:bg-[var(--color-bg-hover)] disabled:opacity-30 transition-colors"
            >
              <ChevronLeft size={14} />
            </button>
            <button
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={page >= totalPages - 1}
              className="p-1 rounded hover:bg-[var(--color-bg-hover)] disabled:opacity-30 transition-colors"
            >
              <ChevronRight size={14} />
            </button>
          </div>
        </div>

        {paginated.length === 0 ? (
          <div className="text-center py-8 text-[var(--color-text-secondary)] text-sm">
            Aucune donnee
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-[var(--color-text-secondary)] uppercase tracking-wider border-b border-[var(--color-border)]">
                {COLUMNS.map((col) => (
                  <th
                    key={col.key}
                    onClick={() => handleSort(col.key)}
                    className={`py-2 px-2 cursor-pointer select-none hover:text-[var(--color-text-primary)] transition-colors ${
                      col.align === 'right' ? 'text-right' : 'text-left'
                    }`}
                  >
                    <span className="inline-flex items-center gap-1">
                      {col.label}
                      {sortCol === col.key && (
                        sortDir === 'asc' ? <ChevronUp size={11} /> : <ChevronDown size={11} />
                      )}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {paginated.map((t, i) => (
                <tr
                  key={i}
                  className="border-b border-[var(--color-border)]/50 hover:bg-[var(--color-bg-hover)] transition-colors"
                >
                  <td className="py-2 px-2 font-mono text-xs">{t.date ?? '-'}</td>
                  <td className="py-2 px-2 font-mono font-semibold">{t.symbol ?? '-'}</td>
                  <td className="py-2 px-2">
                    {t._broker === 'binance' ? (
                      <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-yellow-500/15 text-yellow-500">
                        Binance
                      </span>
                    ) : t._broker === 'ibkr' ? (
                      <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400">
                        IBKR
                      </span>
                    ) : (
                      <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-400">
                        Alpaca
                      </span>
                    )}
                  </td>
                  <td className="py-2 px-2">
                    <span
                      className={`text-xs font-semibold ${
                        t.side === 'BUY' || t.side === 'LONG'
                          ? 'text-[var(--color-profit)]'
                          : 'text-[var(--color-loss)]'
                      }`}
                    >
                      {t.side ?? '-'}
                    </span>
                  </td>
                  <td className="py-2 px-2 text-right font-mono">{formatPrice(t.entry_price)}</td>
                  <td className="py-2 px-2 text-right font-mono">{formatPrice(t.exit_price)}</td>
                  <td
                    className={`py-2 px-2 text-right font-mono font-semibold ${
                      (t.pnl ?? 0) >= 0 ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'
                    }`}
                  >
                    {formatPnl(t.pnl)}
                  </td>
                  <td className="py-2 px-2 text-right font-mono text-xs text-[var(--color-text-secondary)]">
                    {t.duration ?? '-'}
                  </td>
                  <td className="py-2 px-2 text-right text-xs text-[var(--color-text-secondary)]">
                    {t.strategy ?? '-'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Calendar Heatmap */}
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-3">
          Calendrier P&L
        </h2>
        {calDays.length > 0 ? (
          <div className="grid grid-cols-7 gap-1">
            {['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim'].map((d) => (
              <div key={d} className="text-center text-[10px] text-[var(--color-text-secondary)] pb-1">
                {d}
              </div>
            ))}
            {calDays.map((day, i) => {
              const pnl = day.pnl ?? 0
              const bg =
                pnl > 0
                  ? `rgba(34,197,94,${Math.min(Math.abs(pnl) / 200, 1) * 0.7 + 0.1})`
                  : pnl < 0
                    ? `rgba(239,68,68,${Math.min(Math.abs(pnl) / 200, 1) * 0.7 + 0.1})`
                    : 'var(--color-bg-hover)'
              return (
                <div
                  key={i}
                  title={`${day.date}: ${pnl >= 0 ? '+' : ''}$${pnl.toFixed(0)}`}
                  className="aspect-square rounded-sm flex items-center justify-center text-[9px] font-mono text-[var(--color-text-primary)]"
                  style={{ backgroundColor: bg }}
                >
                  {day.day ?? ''}
                </div>
              )
            })}
          </div>
        ) : (
          <div className="flex items-center justify-center h-32 border border-dashed border-[var(--color-border)] rounded-lg">
            <span className="text-sm text-[var(--color-text-secondary)]">
              Calendrier Heatmap — Aucune donnee
            </span>
          </div>
        )}
      </div>
    </div>
  )
}
