import { useApi } from '../hooks/useApi'
import { TierBadge } from '../components/StrategyBadge'
import DistributionChart from '../components/charts/DistributionChart'
import RollingSharpeChart from '../components/charts/RollingSharpeChart'
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Cell,
} from 'recharts'
import { TrendingUp, TrendingDown, Flame, Snowflake } from 'lucide-react'

const DAY_COLORS = {
  positive: 'var(--color-profit, #22c55e)',
  negative: 'var(--color-loss, #ef4444)',
}

function DayTooltip({ active, payload }) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload
  return (
    <div
      style={{
        background: '#1a1a2e',
        border: '1px solid #333',
        borderRadius: 8,
        padding: '10px 14px',
      }}
    >
      <div style={{ color: '#6b7280', fontSize: 12, marginBottom: 4 }}>{d.day}</div>
      <div style={{ color: '#e1e2e8', fontFamily: 'monospace', fontSize: 13 }}>
        P&L moyen: ${d.avg_pnl >= 0 ? '+' : ''}{d.avg_pnl?.toFixed(2)}
      </div>
      <div style={{ color: '#6b7280', fontFamily: 'monospace', fontSize: 11 }}>
        {d.trades} trades | WR {d.win_rate?.toFixed(0)}%
      </div>
    </div>
  )
}

function AttributionBar({ items, total }) {
  if (!items?.length) return null
  return (
    <div className="space-y-2">
      {items.map((item) => {
        const pct = total ? Math.abs(item.pnl / total * 100) : 0
        return (
          <div key={item.source || item.direction || item.strategy} className="space-y-1">
            <div className="flex items-center justify-between text-xs">
              <span className="text-[var(--color-text-secondary)]">
                {item.source || item.direction || item.strategy}
              </span>
              <div className="flex items-center gap-3">
                <span className="font-mono text-[var(--color-text-secondary)]">
                  {item.trades} trades | WR {item.win_rate}%
                </span>
                <span className={`font-mono font-semibold ${item.pnl >= 0 ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}`}>
                  ${item.pnl >= 0 ? '+' : ''}{item.pnl.toFixed(0)}
                </span>
                <span className="font-mono text-[var(--color-text-secondary)] w-12 text-right">
                  {item.pct_of_total != null ? `${item.pct_of_total}%` : `${pct.toFixed(0)}%`}
                </span>
              </div>
            </div>
            <div className="h-1.5 rounded-full bg-[var(--color-bg-hover)] overflow-hidden">
              <div
                className="h-full rounded-full transition-all"
                style={{
                  width: `${Math.min(pct, 100)}%`,
                  backgroundColor: item.pnl >= 0 ? 'var(--color-profit)' : 'var(--color-loss)',
                }}
              />
            </div>
          </div>
        )
      })}
    </div>
  )
}

export default function Analytics() {
  const { data: stratData } = useApi('/strategies', 60000)
  const { data: regime } = useApi('/regime', 60000)
  const { data: alloc } = useApi('/allocation', 60000)
  const { data: distData } = useApi('/analytics/distribution', 120000)
  const { data: sharpeData } = useApi('/analytics/rolling-sharpe', 120000)
  const { data: dayData } = useApi('/analytics/by-day', 120000)
  const { data: streaksData } = useApi('/analytics/streaks', 120000)
  const { data: attrData } = useApi('/analytics/attribution', 120000)

  const strategies = stratData?.strategies || []

  return (
    <div className="space-y-6">
      {/* Performance Attribution */}
      {attrData && !attrData.error && (
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-5">
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-4">
            Attribution de Performance
            <span className="ml-2 font-mono text-xs text-[var(--color-text-secondary)]">
              Total: <span className={attrData.total_pnl >= 0 ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}>
                ${attrData.total_pnl >= 0 ? '+' : ''}{attrData.total_pnl?.toFixed(0)}
              </span>
              {' '}| {attrData.trading_days} jours
            </span>
          </h2>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* By Source */}
            <div>
              <h3 className="text-xs text-[var(--color-text-secondary)] uppercase tracking-wider mb-3">Par source d'alpha</h3>
              <AttributionBar items={attrData.by_source} total={attrData.total_pnl} />
            </div>
            {/* By Direction */}
            <div>
              <h3 className="text-xs text-[var(--color-text-secondary)] uppercase tracking-wider mb-3">Par direction</h3>
              <AttributionBar items={attrData.by_direction} total={attrData.total_pnl} />
            </div>
          </div>
          {/* Top strategies */}
          {attrData.by_strategy?.length > 0 && (
            <div className="mt-4 pt-4 border-t border-[var(--color-border)]">
              <h3 className="text-xs text-[var(--color-text-secondary)] uppercase tracking-wider mb-3">Top strategies</h3>
              <AttributionBar items={attrData.by_strategy.slice(0, 8)} total={attrData.total_pnl} />
            </div>
          )}
        </div>
      )}

      {/* Regime */}
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-5">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-3">Regime de Marche</h2>
        {regime && !regime.error ? (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <div className="text-xs text-[var(--color-text-secondary)]">Regime</div>
              <div className={`font-mono text-lg font-bold ${regime.bull ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}`}>
                {regime.regime}
              </div>
            </div>
            <div>
              <div className="text-xs text-[var(--color-text-secondary)]">SPY vs SMA200</div>
              <div className={`font-mono text-lg ${regime.bull ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}`}>
                {regime.bull ? 'AU-DESSUS' : 'EN-DESSOUS'}
              </div>
              <div className="text-xs font-mono text-[var(--color-text-secondary)]">
                ${regime.spy_close?.toFixed(2)} vs ${regime.sma200?.toFixed(2)}
              </div>
            </div>
            <div>
              <div className="text-xs text-[var(--color-text-secondary)]">ATR 20j</div>
              <div className="font-mono text-lg">{regime.atr_pct}%</div>
            </div>
            <div>
              <div className="text-xs text-[var(--color-text-secondary)]">Ajustements</div>
              <div className="text-sm">
                {!regime.bull && <div className="text-[var(--color-loss)]">Bear: -30% allocation</div>}
                {regime.high_vol && <div className="text-[var(--color-warning)]">HiVol: OpEx +30%, DoW -50%</div>}
                {regime.low_vol && <div className="text-[var(--color-info)]">LoVol: -20% global</div>}
                {regime.bull && !regime.high_vol && !regime.low_vol && <div className="text-[var(--color-profit)]">Aucun (normal)</div>}
              </div>
            </div>
          </div>
        ) : (
          <div className="text-[var(--color-text-secondary)]">Chargement...</div>
        )}
      </div>

      {/* Strategy Ranking */}
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-5">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-3">Ranking par Sharpe</h2>
        <div className="space-y-2">
          {strategies.map((s) => {
            const maxSharpe = strategies[0]?.sharpe || 1
            const width = Math.min((s.sharpe / maxSharpe) * 100, 100)
            return (
              <div key={s.id} className="flex items-center gap-3">
                <TierBadge tier={s.tier} />
                <span className="text-sm w-44 truncate">{s.name}</span>
                <div className="flex-1 bg-[var(--color-bg-primary)] rounded-full h-3 overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${
                      s.sharpe >= 5 ? 'bg-[var(--color-profit)]' :
                      s.sharpe >= 2 ? 'bg-[var(--color-info)]' :
                      s.sharpe >= 1 ? 'bg-[var(--color-warning)]' : 'bg-[var(--color-loss)]'
                    }`}
                    style={{ width: `${Math.max(width, 2)}%` }}
                  />
                </div>
                <span className="font-mono text-sm w-16 text-right">{s.sharpe.toFixed(2)}</span>
              </div>
            )
          })}
        </div>
      </div>

      {/* Allocation Tiers */}
      {alloc && alloc.tiers && (
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-5">
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-3">Allocation par Tier</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {Object.entries(alloc.tiers).map(([tier, strats]) => {
              const totalPct = strats.reduce((sum, s) => sum + (s.pct || 0), 0) * 100
              const totalCap = strats.reduce((sum, s) => sum + (s.capital || 0), 0)
              return (
                <div key={tier} className="border border-[var(--color-border)] rounded-lg p-3">
                  <div className="flex items-center gap-2 mb-2">
                    <TierBadge tier={tier} />
                    <span className="font-mono text-sm font-semibold">{totalPct.toFixed(1)}%</span>
                    <span className="font-mono text-xs text-[var(--color-text-secondary)]">${totalCap.toLocaleString()}</span>
                  </div>
                  <div className="space-y-1">
                    {strats.map((s) => (
                      <div key={s.id} className="flex justify-between text-xs">
                        <span className="text-[var(--color-text-secondary)] truncate">{s.name}</span>
                        <span className="font-mono">{(s.pct * 100).toFixed(1)}%</span>
                      </div>
                    ))}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Kill Switch Status */}
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-5">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-3">Kill Switch Status</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {strategies.map((s) => {
            const margin = s.kill_margin_pct
            const danger = margin < 30
            const warning = margin < 60
            return (
              <div key={s.id} className={`border rounded-lg p-3 ${
                danger ? 'border-[var(--color-loss)]/50 bg-red-500/5' :
                warning ? 'border-[var(--color-warning)]/30' : 'border-[var(--color-border)]'
              }`}>
                <div className="flex justify-between items-center mb-1">
                  <span className="text-sm font-semibold">{s.name}</span>
                  <span className={`font-mono text-xs ${danger ? 'text-[var(--color-loss)]' : warning ? 'text-[var(--color-warning)]' : 'text-[var(--color-profit)]'}`}>
                    {margin}%
                  </span>
                </div>
                <div className="w-full bg-[var(--color-bg-primary)] rounded-full h-2 overflow-hidden">
                  <div
                    className={`h-full rounded-full ${
                      danger ? 'bg-[var(--color-loss)]' : warning ? 'bg-[var(--color-warning)]' : 'bg-[var(--color-profit)]'
                    }`}
                    style={{ width: `${Math.min(Math.max(margin, 0), 100)}%` }}
                  />
                </div>
                <div className="flex justify-between text-xs text-[var(--color-text-secondary)] mt-1">
                  <span>P&L 5j: ${s.pnl_5d >= 0 ? '+' : ''}{s.pnl_5d.toFixed(0)}</span>
                  <span>Seuil: ${s.kill_threshold.toFixed(0)}</span>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* P&L par jour de la semaine */}
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-5">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-3">P&L par Jour de la Semaine</h2>
        {dayData?.days && dayData.days.length > 0 ? (
          <ResponsiveContainer width="100%" height={250}>
            <BarChart
              data={dayData.days}
              layout="vertical"
              margin={{ top: 5, right: 30, bottom: 5, left: 60 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="#2a2b3d" horizontal={false} />
              <XAxis
                type="number"
                tick={{ fill: '#6b7280', fontSize: 11 }}
                stroke="#2a2b3d"
                tickFormatter={(v) => `$${v >= 0 ? '+' : ''}${v.toFixed(0)}`}
              />
              <YAxis
                type="category"
                dataKey="day"
                tick={{ fill: '#6b7280', fontSize: 12 }}
                stroke="#2a2b3d"
                width={55}
              />
              <Tooltip content={<DayTooltip />} />
              <Bar dataKey="avg_pnl" radius={[0, 4, 4, 0]} maxBarSize={28}>
                {(dayData.days || []).map((entry, index) => (
                  <Cell
                    key={index}
                    fill={entry.avg_pnl >= 0 ? DAY_COLORS.positive : DAY_COLORS.negative}
                    fillOpacity={0.8}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <div className="flex items-center justify-center h-[250px] text-[var(--color-text-secondary)] text-sm">
            Aucune donnee
          </div>
        )}
      </div>

      {/* Distribution des trades + Rolling Sharpe */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Distribution */}
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-5">
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-3">Distribution des Trades</h2>
          <DistributionChart data={distData?.buckets || []} />
          {distData?.stats && (
            <div className="grid grid-cols-3 gap-3 mt-3 pt-3 border-t border-[var(--color-border)]">
              <div className="text-center">
                <div className="text-xs text-[var(--color-text-secondary)]">Moyenne</div>
                <div className={`font-mono text-sm font-semibold ${distData.stats.mean >= 0 ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}`}>
                  ${distData.stats.mean >= 0 ? '+' : ''}{distData.stats.mean?.toFixed(2)}
                </div>
              </div>
              <div className="text-center">
                <div className="text-xs text-[var(--color-text-secondary)]">Mediane</div>
                <div className={`font-mono text-sm font-semibold ${distData.stats.median >= 0 ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}`}>
                  ${distData.stats.median >= 0 ? '+' : ''}{distData.stats.median?.toFixed(2)}
                </div>
              </div>
              <div className="text-center">
                <div className="text-xs text-[var(--color-text-secondary)]">Ecart-type</div>
                <div className="font-mono text-sm text-[var(--color-text-primary)]">
                  ${distData.stats.std?.toFixed(2)}
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Rolling Sharpe */}
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-5">
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-3">Rolling Sharpe 30j</h2>
          <RollingSharpeChart data={sharpeData?.points || []} />
          {sharpeData?.current !== undefined && (
            <div className="flex items-center justify-between mt-3 pt-3 border-t border-[var(--color-border)]">
              <div className="text-xs text-[var(--color-text-secondary)]">Sharpe actuel (30j)</div>
              <div className={`font-mono text-lg font-bold ${
                sharpeData.current >= 2 ? 'text-[var(--color-profit)]' :
                sharpeData.current >= 1 ? 'text-[var(--color-info)]' :
                sharpeData.current >= 0 ? 'text-[var(--color-warning)]' : 'text-[var(--color-loss)]'
              }`}>
                {sharpeData.current?.toFixed(2)}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Streaks */}
      {streaksData && (
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-5">
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-3">Series (Streaks)</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="border border-[var(--color-border)] rounded-lg p-3 text-center">
              <div className="flex items-center justify-center gap-1.5 mb-1">
                <Flame size={14} className="text-[var(--color-profit)]" />
                <span className="text-xs text-[var(--color-text-secondary)]">Plus longue serie gagnante</span>
              </div>
              <div className="font-mono text-2xl font-bold text-[var(--color-profit)]">
                {streaksData.longest_win || 0}
              </div>
              <div className="text-xs text-[var(--color-text-secondary)]">trades</div>
            </div>
            <div className="border border-[var(--color-border)] rounded-lg p-3 text-center">
              <div className="flex items-center justify-center gap-1.5 mb-1">
                <Snowflake size={14} className="text-[var(--color-loss)]" />
                <span className="text-xs text-[var(--color-text-secondary)]">Plus longue serie perdante</span>
              </div>
              <div className="font-mono text-2xl font-bold text-[var(--color-loss)]">
                {streaksData.longest_loss || 0}
              </div>
              <div className="text-xs text-[var(--color-text-secondary)]">trades</div>
            </div>
            <div className="border border-[var(--color-border)] rounded-lg p-3 text-center">
              <div className="flex items-center justify-center gap-1.5 mb-1">
                {streaksData.current_type === 'win' ? (
                  <TrendingUp size={14} className="text-[var(--color-profit)]" />
                ) : (
                  <TrendingDown size={14} className="text-[var(--color-loss)]" />
                )}
                <span className="text-xs text-[var(--color-text-secondary)]">Serie en cours</span>
              </div>
              <div className={`font-mono text-2xl font-bold ${
                streaksData.current_type === 'win' ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'
              }`}>
                {streaksData.current_count || 0}
              </div>
              <div className="text-xs text-[var(--color-text-secondary)]">
                {streaksData.current_type === 'win' ? 'gagnants' : 'perdants'}
              </div>
            </div>
            <div className="border border-[var(--color-border)] rounded-lg p-3 text-center">
              <div className="flex items-center justify-center gap-1.5 mb-1">
                <span className="text-xs text-[var(--color-text-secondary)]">P&L serie en cours</span>
              </div>
              <div className={`font-mono text-2xl font-bold ${
                (streaksData.current_pnl || 0) >= 0 ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'
              }`}>
                ${(streaksData.current_pnl || 0) >= 0 ? '+' : ''}{(streaksData.current_pnl || 0).toFixed(0)}
              </div>
              <div className="text-xs text-[var(--color-text-secondary)]">cumule</div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
