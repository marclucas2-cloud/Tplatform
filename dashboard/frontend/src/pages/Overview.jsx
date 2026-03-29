import { useState } from 'react'
import { useApi } from '../hooks/useApi'
import MetricCard from '../components/MetricCard'
import { TierBadge, StatusDot } from '../components/StrategyBadge'
import EquityCurve from '../components/charts/EquityCurve'
import PeriodSelector from '../components/common/PeriodSelector'
import { ArrowUpRight, ArrowDownRight, Clock, AlertTriangle, AlertCircle, Info, CheckCircle, Bitcoin } from 'lucide-react'

const PERIOD_MAP = { '7j': '7d', '30j': '30d', '90j': '90d', 'YTD': 'ytd' }

export default function Overview() {
  const [period, setPeriod] = useState('30j')
  const { data: portfolio, loading: pLoad } = useApi('/portfolio', 30000)
  const { data: posData } = useApi('/positions', 15000)
  const { data: stratData } = useApi('/strategies', 60000)
  const { data: equityData } = useApi(`/equity-curve?period=${PERIOD_MAP[period] || '30d'}`, 60000)
  const { data: alertsData } = useApi('/alerts', 30000)
  const { data: cryptoData } = useApi('/crypto/strategies', 60000)

  if (pLoad || !portfolio) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-[var(--color-text-secondary)] animate-pulse">Loading portfolio...</div>
      </div>
    )
  }

  const positions = posData?.positions || []
  const strategies = stratData?.strategies || []

  return (
    <div className="space-y-6">
      {/* KPI Row */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
        <MetricCard
          label="Equity"
          value={portfolio.equity || (portfolio.alpaca_equity || 0) + (portfolio.binance_equity || 0) + (portfolio.ibkr_equity || 0)}
          change={portfolio.total_return_pct}
          prefix="$"
        />
        <MetricCard
          label="P&L Jour"
          value={portfolio.pnl_day || 0}
          change={portfolio.pnl_day_pct || 0}
          prefix="$"
          color={(portfolio.pnl_day || 0) >= 0 ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}
        />
        <MetricCard
          label="Positions"
          value={portfolio.positions_count}
          suffix=" ouvertes"
        />
        <MetricCard
          label="P&L Non-realise"
          value={portfolio.pnl_unrealized}
          prefix="$"
          color={portfolio.pnl_unrealized >= 0 ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}
        />
        <MetricCard
          label="CRO Score"
          value="9.5/10"
        />
      </div>

      {/* Equity Curve */}
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">Courbe d'Equity</h2>
          <PeriodSelector
            value={period}
            onChange={setPeriod}
            options={['7j', '30j', '90j', 'YTD']}
          />
        </div>
        <EquityCurve data={equityData?.curve || []} period={period} showBrokers={true} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Strategies */}
        <div className="lg:col-span-1 bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-3">
            Strategies ({strategies.length})
          </h2>
          <div className="space-y-2">
            {strategies.map((s) => (
              <div key={s.id} className="flex items-center justify-between py-1.5 px-2 rounded-lg hover:bg-[var(--color-bg-hover)] transition-colors">
                <div className="flex items-center gap-2">
                  <StatusDot status={s.status} />
                  <TierBadge tier={s.tier} />
                  <span className="text-sm text-[var(--color-text-primary)]">{s.name}</span>
                </div>
                <div className="flex items-center gap-3">
                  <span className="font-mono text-xs text-[var(--color-text-secondary)]">
                    {s.allocation_pct}%
                  </span>
                  <span className={`font-mono text-xs ${s.pnl_5d >= 0 ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}`}>
                    ${s.pnl_5d >= 0 ? '+' : ''}{s.pnl_5d.toFixed(0)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Positions */}
        <div className="lg:col-span-2 bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">
              Positions Ouvertes ({positions.length})
            </h2>
            {posData && (
              <div className="flex gap-3 text-xs font-mono">
                <span className="text-[var(--color-profit)]">Long: ${posData.exposure_long?.toLocaleString()}</span>
                <span className="text-[var(--color-loss)]">Short: ${posData.exposure_short?.toLocaleString()}</span>
                <span className="text-[var(--color-text-secondary)]">Net: ${posData.exposure_net?.toLocaleString()}</span>
              </div>
            )}
          </div>

          {positions.length === 0 ? (
            <div className="text-center py-8 text-[var(--color-text-secondary)] text-sm">
              Aucune position ouverte
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-[var(--color-text-secondary)] uppercase tracking-wider border-b border-[var(--color-border)]">
                  <th className="text-left py-2 px-2">Ticker</th>
                  <th className="text-left py-2">Dir</th>
                  <th className="text-right py-2">Shares</th>
                  <th className="text-right py-2">Entry</th>
                  <th className="text-right py-2">Current</th>
                  <th className="text-right py-2">P&L</th>
                  <th className="text-right py-2 px-2">Strategy</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p, i) => (
                  <tr key={i} className="border-b border-[var(--color-border)]/50 hover:bg-[var(--color-bg-hover)]">
                    <td className="py-2 px-2 font-mono font-semibold">{p.ticker}</td>
                    <td className="py-2">
                      <span className={`flex items-center gap-1 text-xs font-semibold ${p.direction === 'LONG' ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}`}>
                        {p.direction === 'LONG' ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}
                        {p.direction}
                      </span>
                    </td>
                    <td className="py-2 text-right font-mono">{p.shares}</td>
                    <td className="py-2 text-right font-mono">${p.entry_price?.toFixed(2)}</td>
                    <td className="py-2 text-right font-mono">${p.current_price?.toFixed(2)}</td>
                    <td className={`py-2 text-right font-mono font-semibold ${p.pnl >= 0 ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}`}>
                      ${p.pnl >= 0 ? '+' : ''}{p.pnl?.toFixed(2)}
                    </td>
                    <td className="py-2 px-2 text-right text-xs text-[var(--color-text-secondary)]">{p.strategy}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* Crypto Binance */}
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Bitcoin size={16} className="text-orange-400" />
            <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">Crypto Binance</h2>
          </div>
          {cryptoData?.phase && (
            <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold bg-yellow-500/20 text-yellow-400 border border-yellow-500/30">
              {cryptoData.phase}
            </span>
          )}
        </div>
        {!cryptoData ? (
          <div className="text-center py-4 text-[var(--color-text-secondary)] text-sm animate-pulse">
            Chargement crypto...
          </div>
        ) : (
          <>
            <div className="flex gap-4 mb-3 text-xs">
              <span className="text-[var(--color-text-secondary)]">
                Capital: <span className="font-mono text-[var(--color-text-primary)]">
                  ${(cryptoData.total_capital || 0).toLocaleString()}
                </span>
              </span>
              <span className="text-[var(--color-text-secondary)]">
                Strategies actives: <span className="font-mono text-[var(--color-text-primary)]">
                  {(cryptoData.strategies || []).filter(s => s.status === 'LIVE').length}/{(cryptoData.strategies || []).length}
                </span>
              </span>
            </div>
            <div className="space-y-1.5">
              {(cryptoData.strategies || []).map((s, i) => (
                <div key={s.id || i} className="flex items-center justify-between py-1 px-2 rounded-lg hover:bg-[var(--color-bg-hover)] transition-colors">
                  <div className="flex items-center gap-2">
                    <span className={`inline-block w-2 h-2 rounded-full ${
                      s.status === 'LIVE' ? 'bg-[var(--color-profit)]' : 'bg-gray-600'
                    }`} />
                    <span className="text-sm text-[var(--color-text-primary)]">{s.name}</span>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                      s.wallet === 'Spot' ? 'bg-blue-500/20 text-blue-400' :
                      s.wallet === 'Margin' ? 'bg-orange-500/20 text-orange-400' :
                      s.wallet === 'Earn' ? 'bg-purple-500/20 text-purple-400' :
                      'bg-gray-500/20 text-gray-400'
                    }`}>
                      {s.wallet || '—'}
                    </span>
                    <span className="font-mono text-xs text-[var(--color-text-secondary)]">
                      {s.allocation_pct || 0}%
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      {/* System Status */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-3 flex items-center gap-2">
          <Clock size={14} className="text-[var(--color-text-secondary)]" />
          <span className="text-xs text-[var(--color-text-secondary)]">
            Updated: {portfolio.timestamp ? new Date(portfolio.timestamp).toLocaleTimeString('fr-FR') : 'N/A'}
          </span>
        </div>
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-3">
          <span className="text-xs text-[var(--color-text-secondary)]">
            Regime: <span className="font-mono text-[var(--color-text-primary)]">{portfolio.regime || 'N/A'}</span>
          </span>
        </div>
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-3">
          <span className="text-xs text-[var(--color-text-secondary)]">
            ATR SPY: <span className="font-mono text-[var(--color-text-primary)]">{portfolio.regime_detail?.atr_pct ?? 'N/A'}%</span>
          </span>
        </div>
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-3">
          <span className="text-xs text-[var(--color-text-secondary)]">
            Capital: <span className="font-mono text-[var(--color-text-primary)]">${(portfolio.equity || 0).toLocaleString()}</span>
          </span>
        </div>
      </div>

      {/* Dernieres Alertes */}
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-3">Alertes Recentes</h2>
        {(alertsData?.alerts || []).length === 0 ? (
          <div className="text-center py-6 text-[var(--color-text-secondary)] text-sm">
            Aucune alerte recente
          </div>
        ) : (
          <div className="space-y-2">
            {(alertsData?.alerts || []).slice(0, 5).map((alert, i) => {
              const levelConfig = {
                critical: { icon: AlertTriangle, color: 'text-[var(--color-loss)]', bg: 'bg-red-500/5' },
                warning: { icon: AlertCircle, color: 'text-[var(--color-warning)]', bg: 'bg-yellow-500/5' },
                info: { icon: Info, color: 'text-[var(--color-info)]', bg: 'bg-blue-500/5' },
                success: { icon: CheckCircle, color: 'text-[var(--color-profit)]', bg: 'bg-green-500/5' },
              }
              const cfg = levelConfig[alert.level] || levelConfig.info
              const Icon = cfg.icon
              return (
                <div
                  key={i}
                  className={`flex items-start gap-3 py-2 px-3 rounded-lg ${cfg.bg}`}
                >
                  <Icon size={14} className={`mt-0.5 shrink-0 ${cfg.color}`} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm text-[var(--color-text-primary)] truncate">
                        {alert.message}
                      </span>
                      <span className="text-xs font-mono text-[var(--color-text-secondary)] shrink-0">
                        {alert.timestamp
                          ? new Date(alert.timestamp).toLocaleTimeString('fr-FR', {
                              hour: '2-digit',
                              minute: '2-digit',
                            })
                          : ''}
                      </span>
                    </div>
                    {alert.source && (
                      <span className="text-xs text-[var(--color-text-secondary)]">{alert.source}</span>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
