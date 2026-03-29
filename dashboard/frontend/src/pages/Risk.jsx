import { useApi } from '../hooks/useApi'
import MetricCard from '../components/MetricCard'
import { ShieldAlert, ShieldCheck, ShieldOff, Activity } from 'lucide-react'

function ProgressBar({ label, current, max, unit = '', warn = 0.7, danger = 0.9, invertColor = false }) {
  const ratio = max > 0 ? Math.min(current / max, 1) : 0
  const pct = (ratio * 100).toFixed(1)
  const isInverted = invertColor || label?.toLowerCase().includes('cash') || label?.toLowerCase().includes('reserve') || label?.toLowerCase().includes('margin level')
  const barColor = isInverted
    ? (ratio >= danger ? 'var(--color-profit)' : ratio >= warn ? 'var(--color-profit)' : 'var(--color-warning)')
    : (ratio >= danger ? 'var(--color-loss)' : ratio >= warn ? 'var(--color-warning)' : 'var(--color-profit)')

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className="text-[var(--color-text-secondary)]">{label}</span>
        <span className="font-mono text-[var(--color-text-primary)]">
          {current}{unit} / {max}{unit}
        </span>
      </div>
      <div className="h-2 rounded-full bg-[var(--color-bg-hover)] overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${pct}%`, backgroundColor: barColor }}
        />
      </div>
    </div>
  )
}

function KillSwitchCard({ broker, status, lastTest }) {
  const isOff = status === 'OFF' || status === 'INACTIVE'
  return (
    <div className="flex items-center justify-between py-2 px-3 rounded-lg bg-[var(--color-bg-hover)]">
      <div className="flex items-center gap-2">
        {isOff ? (
          <ShieldCheck size={16} className="text-[var(--color-profit)]" />
        ) : (
          <ShieldAlert size={16} className="text-[var(--color-loss)]" />
        )}
        <span className="text-sm text-[var(--color-text-primary)] font-semibold">{broker}</span>
      </div>
      <div className="flex items-center gap-4">
        <span
          className={`font-mono text-xs font-bold px-2 py-0.5 rounded ${
            isOff
              ? 'bg-[var(--color-profit)]/15 text-[var(--color-profit)]'
              : 'bg-[var(--color-loss)]/15 text-[var(--color-loss)]'
          }`}
        >
          {status || 'N/A'}
        </span>
        <span className="text-xs text-[var(--color-text-secondary)]">
          Test : {lastTest || 'N/A'}
        </span>
      </div>
    </div>
  )
}

export default function Risk() {
  const { data: overview, loading: oLoad } = useApi('/risk/overview', 15000)
  const { data: limits, loading: lLoad } = useApi('/risk/limits', 15000)
  const { data: killSwitchData } = useApi('/risk/kill-switch', 30000)

  if (oLoad && !overview) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-[var(--color-text-secondary)] animate-pulse">Chargement risques...</div>
      </div>
    )
  }

  const ov = overview || {}
  const dd = ov.drawdown || {}
  const drawdown = dd.current_pct ?? ov.drawdown ?? 0
  const drawdownMax = dd.max_allowed_pct ?? ov.drawdown_max ?? -5
  const var95 = ov.var?.var_95_1d ?? ov.var_1d_95 ?? 0
  const expData = ov.exposure || {}
  const exposure = expData.total_capital ? Math.round((expData.ibkr_capital + expData.crypto_capital) / expData.total_capital * 100) : (ov.exposure_net_pct ?? 0)
  const ks = ov.kill_switch || {}
  const ibkrKs = ks.ibkr?.active ? 'ACTIVE' : 'OFF'
  const cryptoKs = ks.crypto?.active ? 'ACTIVE' : 'OFF'
  const killSwitch = (ibkrKs === 'ACTIVE' || cryptoKs === 'ACTIVE') ? 'ACTIVE' : 'OFF'

  // Map API limits: [{name, current, limit, pct_used, status}] -> ProgressBar props
  const rawLimits = limits?.limits || limits || []
  const riskLimits = Array.isArray(rawLimits) && rawLimits.length > 0
    ? rawLimits.map((lim) => ({
        label: lim.name ?? lim.label ?? '-',
        current: lim.current ?? 0,
        max: lim.limit ?? lim.max ?? 1,
        unit: lim.unit ?? '',
        inverted: lim.inverted ?? false,
      }))
    : []

  const rawKs = killSwitchData?.switches || killSwitchData || []
  const killSwitches = Array.isArray(rawKs) && rawKs.length > 0
    ? rawKs
    : [
        { broker: 'IBKR', status: ibkrKs, last_test: ks.ibkr?.activated_at || 'N/A' },
        { broker: 'Binance', status: cryptoKs, last_test: ks.crypto?.activated_at || 'N/A' },
      ]

  return (
    <div className="space-y-6">
      {/* KPI Row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <MetricCard
          label="Drawdown"
          value={drawdown}
          change={drawdownMax}
          suffix="%"
          color="text-[var(--color-loss)]"
        />
        <MetricCard
          label="VaR 1j 95%"
          value={var95}
          prefix="$"
          color="text-[var(--color-warning)]"
        />
        <MetricCard
          label="Exposition Nette"
          value={exposure}
          suffix="% long"
          color="text-[var(--color-text-primary)]"
        />
        <MetricCard
          label="Kill Switch"
          value={killSwitch}
          color={
            killSwitch === 'OFF'
              ? 'text-[var(--color-profit)]'
              : 'text-[var(--color-loss)]'
          }
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Risk Limits */}
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-4 flex items-center gap-2">
            <Activity size={14} />
            Limites de Risque
          </h2>
          <div className="space-y-3">
            {riskLimits.length > 0 ? riskLimits.map((lim, i) => (
              <ProgressBar
                key={i}
                label={lim.label}
                current={lim.current}
                max={lim.max}
                unit={lim.unit}
                invertColor={lim.inverted}
              />
            )) : (
              <div className="text-center py-4 text-sm text-[var(--color-text-secondary)]">
                Aucune donnee de limites disponible
              </div>
            )}
          </div>
        </div>

        {/* Drawdown Chart Placeholder */}
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-4">
            Historique Drawdown
          </h2>
          <div className="flex items-center justify-center h-64 border border-dashed border-[var(--color-border)] rounded-lg">
            <span className="text-sm text-[var(--color-text-secondary)]">
              Drawdown History Chart
            </span>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Kill Switch Status */}
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-3 flex items-center gap-2">
            <ShieldOff size={14} />
            Kill Switch
          </h2>
          <div className="space-y-2">
            {killSwitches.map((ks, i) => (
              <KillSwitchCard
                key={i}
                broker={ks.broker}
                status={ks.status}
                lastTest={ks.last_test}
              />
            ))}
          </div>
          <div className="mt-3 text-xs text-[var(--color-text-secondary)]">
            Triggers : drawdown auto, Telegram /kill, TWS, brackets broker-side
          </div>
        </div>

        {/* Correlation Matrix Placeholder */}
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-4">
            Matrice de Correlation
          </h2>
          <div className="flex items-center justify-center h-48 border border-dashed border-[var(--color-border)] rounded-lg">
            <span className="text-sm text-[var(--color-text-secondary)]">
              Correlation Matrix Heatmap
            </span>
          </div>
        </div>
      </div>

      {/* Footer: Circuit Breaker Summary */}
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-3">
          Circuit Breakers
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {[
            { label: 'Daily', threshold: '-1.5%', status: ov.cb_daily ?? 'OK' },
            { label: 'Hourly', threshold: '-1.0%', status: ov.cb_hourly ?? 'OK' },
            { label: 'Weekly', threshold: '-3.0%', status: ov.cb_weekly ?? 'OK' },
          ].map((cb) => (
            <div
              key={cb.label}
              className="flex items-center justify-between py-2 px-3 rounded-lg bg-[var(--color-bg-hover)]"
            >
              <div className="text-sm text-[var(--color-text-primary)]">
                {cb.label}{' '}
                <span className="font-mono text-xs text-[var(--color-text-secondary)]">
                  ({cb.threshold})
                </span>
              </div>
              <span
                className={`font-mono text-xs font-bold px-2 py-0.5 rounded ${
                  cb.status === 'OK'
                    ? 'bg-[var(--color-profit)]/15 text-[var(--color-profit)]'
                    : 'bg-[var(--color-loss)]/15 text-[var(--color-loss)]'
                }`}
              >
                {cb.status}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
