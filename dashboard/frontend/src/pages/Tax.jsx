import { useState } from 'react'
import { useApi } from '../hooks/useApi'
import MetricCard from '../components/MetricCard'
import { FileText, Download, Info, AlertTriangle, Coins, PiggyBank } from 'lucide-react'
import Tooltip from '../components/common/Tooltip'
import { TOOLTIPS } from '../utils/tooltips'

const DEFAULT_ACCOUNT = { plus_values: 0, moins_values: 0, pv_nette: 0, pfu: 0 }

function fmtCurrency(val, showSign = false) {
  if (val == null) return '—'
  const prefix = showSign && val > 0 ? '+' : ''
  return `${prefix}$${Math.abs(val).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`
}

function mapAccount(raw) {
  if (!raw) return { ...DEFAULT_ACCOUNT }
  return {
    plus_values: raw.pv ?? raw.plus_values ?? 0,
    moins_values: raw.mv ?? raw.moins_values ?? 0,
    pv_nette: raw.net ?? raw.pv_nette ?? 0,
    pfu: raw.pfu ?? 0,
  }
}

export default function Tax() {
  const [year, setYear] = useState(2026)
  const { data: summaryData, loading: sLoad } = useApi(`/tax/summary?year=${year}`, 60000)
  const { data: monthlyData, loading: mLoad } = useApi(`/tax/monthly?year=${year}`, 60000)

  const rawSummary = summaryData?.summary ?? summaryData
  const summary = {
    ibkr: mapAccount(rawSummary?.ibkr),
    binance: mapAccount(rawSummary?.binance),
    total: mapAccount(rawSummary?.total),
  }
  const monthly = monthlyData?.months || []
  const loading = sLoad && mLoad

  if (loading && !summaryData && !monthlyData) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-[var(--color-text-secondary)] animate-pulse">Chargement fiscalite...</div>
      </div>
    )
  }

  const yearOptions = [2024, 2025, 2026]

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-[var(--color-text-primary)]">
          Fiscalite — PFU 30% (France)
        </h1>
        <select
          value={year}
          onChange={(e) => setYear(Number(e.target.value))}
          className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-lg px-3 py-1.5 text-sm text-[var(--color-text-primary)] font-mono focus:outline-none focus:border-[var(--color-accent)]"
        >
          {yearOptions.map((y) => (
            <option key={y} value={y}>{y}</option>
          ))}
        </select>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <MetricCard label="PV Nette Totale" value={summary.total.pv_nette} prefix="$" color="text-[var(--color-profit)]" />
        <MetricCard label={<Tooltip text={TOOLTIPS.pfu}>PFU 30% Du</Tooltip>} value={summary.total.pfu} prefix="$" color="text-[var(--color-warning)]" />
        <MetricCard label="Plus-Values" value={summary.total.plus_values} prefix="+$" color="text-[var(--color-profit)]" />
        <MetricCard label="Moins-Values" value={Math.abs(summary.total.moins_values)} prefix="-$" color="text-[var(--color-loss)]" />
      </div>

      {/* Summary Table */}
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-3">
          Résumé par compte — {year}
        </h2>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-[var(--color-text-secondary)] uppercase tracking-wider border-b border-[var(--color-border)]">
              <th className="text-left py-2 px-2"></th>
              <th className="text-right py-2 px-2">IBKR</th>
              <th className="text-right py-2 px-2">Binance</th>
              <th className="text-right py-2 px-2 font-bold">TOTAL</th>
            </tr>
          </thead>
          <tbody>
            <tr className="border-b border-[var(--color-border)]/50 hover:bg-[var(--color-bg-hover)]">
              <td className="py-2.5 px-2 text-[var(--color-text-secondary)]">Plus-values</td>
              <td className="py-2.5 px-2 text-right font-mono text-[var(--color-profit)]">{fmtCurrency(summary.ibkr.plus_values, true)}</td>
              <td className="py-2.5 px-2 text-right font-mono text-[var(--color-profit)]">{fmtCurrency(summary.binance.plus_values, true)}</td>
              <td className="py-2.5 px-2 text-right font-mono font-semibold text-[var(--color-profit)]">{fmtCurrency(summary.total.plus_values, true)}</td>
            </tr>
            <tr className="border-b border-[var(--color-border)]/50 hover:bg-[var(--color-bg-hover)]">
              <td className="py-2.5 px-2 text-[var(--color-text-secondary)]">Moins-values</td>
              <td className="py-2.5 px-2 text-right font-mono text-[var(--color-loss)]">{fmtCurrency(summary.ibkr.moins_values)}</td>
              <td className="py-2.5 px-2 text-right font-mono text-[var(--color-loss)]">{fmtCurrency(summary.binance.moins_values)}</td>
              <td className="py-2.5 px-2 text-right font-mono font-semibold text-[var(--color-loss)]">{fmtCurrency(summary.total.moins_values)}</td>
            </tr>
            <tr className="border-b border-[var(--color-border)]/50 hover:bg-[var(--color-bg-hover)]">
              <td className="py-2.5 px-2 text-[var(--color-text-primary)] font-semibold">PV nette</td>
              <td className="py-2.5 px-2 text-right font-mono text-[var(--color-profit)]">{fmtCurrency(summary.ibkr.pv_nette, true)}</td>
              <td className="py-2.5 px-2 text-right font-mono text-[var(--color-profit)]">{fmtCurrency(summary.binance.pv_nette, true)}</td>
              <td className="py-2.5 px-2 text-right font-mono font-bold text-[var(--color-profit)]">{fmtCurrency(summary.total.pv_nette, true)}</td>
            </tr>
            <tr className="hover:bg-[var(--color-bg-hover)]">
              <td className="py-2.5 px-2 text-[var(--color-warning)] font-semibold">PFU 30%</td>
              <td className="py-2.5 px-2 text-right font-mono text-[var(--color-warning)]">${summary.ibkr.pfu}</td>
              <td className="py-2.5 px-2 text-right font-mono text-[var(--color-warning)]">${summary.binance.pfu}</td>
              <td className="py-2.5 px-2 text-right font-mono font-bold text-[var(--color-warning)]">${summary.total.pfu}</td>
            </tr>
          </tbody>
        </table>
      </div>

      {/* Info Boxes */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="bg-blue-500/10 border border-blue-500/30 rounded-xl p-4">
          <div className="flex items-start gap-3">
            <Info size={18} className="text-[var(--color-info)] mt-0.5 shrink-0" />
            <div>
              <h3 className="text-sm font-semibold text-[var(--color-info)] mb-1">PFU — Prelevement Forfaitaire Unique</h3>
              <p className="text-xs text-[var(--color-text-secondary)] leading-relaxed">
                Le PFU (Prelevement Forfaitaire Unique) est de 30% en France.
                Il se compose de 12.8% d'impot sur le revenu + 17.2% de prelevements sociaux.
              </p>
            </div>
          </div>
        </div>
        <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-xl p-4">
          <div className="flex items-start gap-3">
            <AlertTriangle size={18} className="text-[var(--color-warning)] mt-0.5 shrink-0" />
            <div>
              <h3 className="text-sm font-semibold text-[var(--color-warning)] mb-1">Specificites Crypto</h3>
              <p className="text-xs text-[var(--color-text-secondary)] leading-relaxed">
                Les echanges crypto-crypto ne sont PAS imposables. Seule la conversion vers EUR/fiat
                declenche l'impot. Les interets Earn sont imposes comme des revenus de capitaux mobiliers.
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Crypto-Crypto Savings & Provision */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="bg-emerald-500/10 border border-emerald-500/30 rounded-xl p-4">
          <div className="flex items-start gap-3">
            <Coins size={18} className="text-emerald-400 mt-0.5 shrink-0" />
            <div>
              <h3 className="text-sm font-semibold text-emerald-400 mb-1">
                <Tooltip text={TOOLTIPS.crypto_crypto}>Economie Crypto-Crypto</Tooltip>
              </h3>
              <p className="text-xs text-[var(--color-text-secondary)] leading-relaxed mb-2">
                Vos echanges crypto-crypto (BTC/ETH/USDC) ne sont PAS imposables en France.
                Seules les conversions vers EUR declenchent l'impot.
              </p>
              <div className="flex items-center gap-4 text-sm">
                <span className="text-[var(--color-text-secondary)]">
                  Gains crypto-crypto: <span className="font-mono font-semibold text-emerald-400">~40%</span> du total
                </span>
                <span className="text-[var(--color-text-secondary)]">
                  Taux effectif: <span className="font-mono font-semibold text-emerald-400">~18%</span> vs 30% nominal
                </span>
              </div>
            </div>
          </div>
        </div>
        <div className="bg-purple-500/10 border border-purple-500/30 rounded-xl p-4">
          <div className="flex items-start gap-3">
            <PiggyBank size={18} className="text-purple-400 mt-0.5 shrink-0" />
            <div>
              <h3 className="text-sm font-semibold text-purple-400 mb-1">Provision Recommandee</h3>
              <p className="text-xs text-[var(--color-text-secondary)] leading-relaxed mb-2">
                Montant a mettre de cote pour les impots. Base sur les plus-values realisees
                converties en EUR uniquement.
              </p>
              <div className="font-mono text-2xl font-bold text-purple-400">
                ${summary.total.pfu.toLocaleString()}
              </div>
              <div className="text-[10px] text-[var(--color-text-secondary)] mt-1">
                a provisionner pour la declaration {year}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Monthly Breakdown */}
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-3">
          Detail mensuel — {year}
        </h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-[var(--color-text-secondary)] uppercase tracking-wider border-b border-[var(--color-border)]">
                <th className="text-left py-2 px-2">Mois</th>
                <th className="text-right py-2 px-2">PV brute</th>
                <th className="text-right py-2 px-2">MV brute</th>
                <th className="text-right py-2 px-2">PV nette</th>
                <th className="text-right py-2 px-2">PFU 30%</th>
              </tr>
            </thead>
            <tbody>
              {monthly.map((m, i) => {
                const hasData = m.pv_brute !== 0 || m.mv_brute !== 0
                return (
                  <tr
                    key={i}
                    className={`border-b border-[var(--color-border)]/50 hover:bg-[var(--color-bg-hover)] ${!hasData ? 'opacity-40' : ''}`}
                  >
                    <td className="py-2 px-2 text-[var(--color-text-primary)]">{m.month}</td>
                    <td className="py-2 px-2 text-right font-mono text-[var(--color-profit)]">
                      {hasData ? fmtCurrency(m.pv_brute, true) : '—'}
                    </td>
                    <td className="py-2 px-2 text-right font-mono text-[var(--color-loss)]">
                      {hasData ? fmtCurrency(m.mv_brute) : '—'}
                    </td>
                    <td className={`py-2 px-2 text-right font-mono font-semibold ${m.pv_nette >= 0 ? 'text-[var(--color-profit)]' : 'text-[var(--color-loss)]'}`}>
                      {hasData ? fmtCurrency(m.pv_nette, true) : '—'}
                    </td>
                    <td className="py-2 px-2 text-right font-mono text-[var(--color-warning)]">
                      {hasData ? `$${m.pfu.toFixed(0)}` : '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
            <tfoot>
              <tr className="border-t-2 border-[var(--color-border)] font-semibold">
                <td className="py-2.5 px-2 text-[var(--color-text-primary)]">TOTAL</td>
                <td className="py-2.5 px-2 text-right font-mono text-[var(--color-profit)]">
                  {fmtCurrency(monthly.reduce((s, m) => s + m.pv_brute, 0), true)}
                </td>
                <td className="py-2.5 px-2 text-right font-mono text-[var(--color-loss)]">
                  {fmtCurrency(monthly.reduce((s, m) => s + m.mv_brute, 0))}
                </td>
                <td className="py-2.5 px-2 text-right font-mono text-[var(--color-profit)]">
                  {fmtCurrency(monthly.reduce((s, m) => s + m.pv_nette, 0), true)}
                </td>
                <td className="py-2.5 px-2 text-right font-mono text-[var(--color-warning)]">
                  ${monthly.reduce((s, m) => s + m.pfu, 0).toFixed(0)}
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
      </div>

      {/* Export Buttons */}
      <div className="flex items-center gap-3">
        <button className="flex items-center gap-2 bg-[var(--color-bg-card)] border border-[var(--color-border)] hover:border-[var(--color-accent)] rounded-lg px-4 py-2.5 text-sm text-[var(--color-text-primary)] transition-colors">
          <FileText size={16} />
          Exporter CSV pour comptable
        </button>
        <button className="flex items-center gap-2 bg-[var(--color-bg-card)] border border-[var(--color-border)] hover:border-[var(--color-accent)] rounded-lg px-4 py-2.5 text-sm text-[var(--color-text-primary)] transition-colors">
          <Download size={16} />
          Telecharger rapport PDF
        </button>
      </div>
    </div>
  )
}
