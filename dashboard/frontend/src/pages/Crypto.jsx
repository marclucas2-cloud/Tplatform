import { useApi } from '../hooks/useApi'
import MetricCard from '../components/MetricCard'
import { Bitcoin, Wallet, ShieldCheck, TrendingUp } from 'lucide-react'

const WALLET_COLORS = {
  Spot: { bg: 'bg-blue-500/20', text: 'text-blue-400', border: 'border-blue-500/30', bar: 'bg-blue-500' },
  Margin: { bg: 'bg-orange-500/20', text: 'text-orange-400', border: 'border-orange-500/30', bar: 'bg-orange-500' },
  Earn: { bg: 'bg-purple-500/20', text: 'text-purple-400', border: 'border-purple-500/30', bar: 'bg-purple-500' },
  Cash: { bg: 'bg-gray-500/20', text: 'text-gray-400', border: 'border-gray-500/30', bar: 'bg-gray-500' },
}

function WalletTag({ wallet }) {
  const c = WALLET_COLORS[wallet] || WALLET_COLORS.Cash
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold ${c.bg} ${c.text} border ${c.border}`}>
      {wallet}
    </span>
  )
}

function StatusBadge({ status }) {
  const isLive = status === 'LIVE'
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-bold ${
      isLive
        ? 'bg-green-500/20 text-green-400 border border-green-500/30'
        : 'bg-gray-500/20 text-gray-400 border border-gray-500/30'
    }`}>
      {status || 'OFF'}
    </span>
  )
}

function PhaseBadge({ phase }) {
  return (
    <span className="inline-flex items-center px-2.5 py-1 rounded-lg text-xs font-bold bg-yellow-500/20 text-yellow-400 border border-yellow-500/30">
      {phase || 'SOFT_LAUNCH'}
    </span>
  )
}

export default function Crypto() {
  const { data, loading, error } = useApi('/crypto/strategies', 30000)

  if (loading || !data) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-[var(--color-text-secondary)] animate-pulse">Chargement crypto...</div>
      </div>
    )
  }

  const strategies = data.strategies || []
  const wallets = data.wallets || {}
  const balance = data.balance || {}
  const phase = data.phase || 'SOFT_LAUNCH'
  const totalCapital = data.total_capital || 20000
  const earnPositions = data.earn_positions || strategies.filter(s => s.wallet === 'Earn').length
  const kellyFraction = data.kelly_fraction || '1/8'
  const marginLevel = data.binance_balance?.margin_level ?? data.margin_level ?? null

  // Wallet distribution
  const walletEntries = Object.entries(wallets).length > 0
    ? Object.entries(wallets)
    : [['Spot', 6000], ['Margin', 4000], ['Earn', 3000], ['Cash', 2000]]
  const walletTotal = walletEntries.reduce((sum, [, v]) => sum + (typeof v === 'number' ? v : v?.amount || 0), 0)

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-orange-500/20 flex items-center justify-center">
            <Bitcoin size={20} className="text-orange-400" />
          </div>
          <div>
            <h1 className="text-lg font-semibold text-[var(--color-text-primary)]">Crypto — Binance Live</h1>
            <p className="text-xs text-[var(--color-text-secondary)]">{strategies.length} strategies, margin + spot + earn</p>
          </div>
        </div>
        <PhaseBadge phase={phase} />
      </div>

      {/* KPI Row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <MetricCard
          label="Capital Total"
          value={totalCapital}
          prefix="$"
        />
        <MetricCard
          label="Positions Earn"
          value={earnPositions}
          suffix=" actives"
        />
        <MetricCard
          label="Kelly Fraction"
          value={kellyFraction}
        />
        <MetricCard
          label="Margin Level"
          value={marginLevel != null ? `${marginLevel}%` : 'N/A'}
          color={marginLevel != null && marginLevel < 150 ? 'text-[var(--color-loss)]' : undefined}
        />
      </div>

      {/* Strategy Cards Grid */}
      <div>
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-3">
          Strategies ({strategies.length})
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {strategies.map((s, i) => (
            <div
              key={s.id || i}
              className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4 hover:border-[var(--color-accent)]/40 transition-colors"
            >
              <div className="flex items-center justify-between mb-3">
                <span className="text-sm font-semibold text-[var(--color-text-primary)]">
                  {s.name}
                </span>
                <StatusBadge status={s.status} />
              </div>
              <div className="flex items-center gap-2 mb-3">
                <WalletTag wallet={s.wallet} />
                {s.max_leverage && (
                  <span className="text-xs font-mono text-[var(--color-text-secondary)]">
                    Levier max: {s.max_leverage}x
                  </span>
                )}
              </div>
              <div className="flex items-center justify-between text-xs">
                <div className="text-[var(--color-text-secondary)]">
                  Allocation: <span className="font-mono text-[var(--color-text-primary)]">{s.allocation_pct || 0}%</span>
                  {s.capital != null && (
                    <span className="ml-2 font-mono text-[var(--color-text-secondary)]">
                      (${s.capital?.toLocaleString()})
                    </span>
                  )}
                </div>
              </div>
              {s.symbols && s.symbols.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {s.symbols.map((sym) => (
                    <span
                      key={sym}
                      className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono bg-[var(--color-bg-primary)] text-[var(--color-text-secondary)] border border-[var(--color-border)]"
                    >
                      {sym}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Wallet Distribution */}
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-4">
          Distribution Wallets
        </h2>
        <div className="space-y-3">
          {walletEntries.map(([name, value]) => {
            const amount = typeof value === 'number' ? value : value?.amount || 0
            const pct = walletTotal > 0 ? (amount / walletTotal) * 100 : 0
            const c = WALLET_COLORS[name] || WALLET_COLORS.Cash
            return (
              <div key={name}>
                <div className="flex items-center justify-between mb-1">
                  <div className="flex items-center gap-2">
                    <span className={`w-3 h-3 rounded-sm ${c.bar}`} />
                    <span className="text-sm text-[var(--color-text-primary)]">{name}</span>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-xs font-mono text-[var(--color-text-secondary)]">
                      {pct.toFixed(0)}%
                    </span>
                    <span className="text-xs font-mono text-[var(--color-text-primary)]">
                      ${amount.toLocaleString()}
                    </span>
                  </div>
                </div>
                <div className="h-2 bg-[var(--color-bg-primary)] rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full ${c.bar} transition-all duration-500`}
                    style={{ width: `${pct}%` }}
                  />
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Binance Balance */}
      {balance && Object.keys(balance).length > 0 && (
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-3">
            Soldes Binance
          </h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {Object.entries(balance).map(([asset, amount]) => (
              <div key={asset} className="flex items-center justify-between py-2 px-3 rounded-lg bg-[var(--color-bg-primary)]">
                <span className="text-xs font-semibold text-[var(--color-text-secondary)] uppercase">{asset}</span>
                <span className="text-sm font-mono text-[var(--color-text-primary)]">
                  {typeof amount === 'number' ? amount.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 8 }) : amount}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Error display */}
      {error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-3">
          <span className="text-xs text-red-400">Erreur API : {error}</span>
        </div>
      )}
    </div>
  )
}
