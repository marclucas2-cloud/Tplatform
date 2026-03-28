import { useState } from 'react'
import { useApi } from '../hooks/useApi'
import MetricCard from '../components/MetricCard'
import { FileText, Download, Info, AlertTriangle } from 'lucide-react'

const SAMPLE_SUMMARY = {
  ibkr: { plus_values: 850, moins_values: -230, pv_nette: 620, pfu: 186 },
  binance: { plus_values: 420, moins_values: -180, pv_nette: 240, pfu: 72 },
  total: { plus_values: 1270, moins_values: -410, pv_nette: 860, pfu: 258 },
}

const SAMPLE_MONTHLY = [
  { month: 'Janvier', pv_brute: 210, mv_brute: -45, pv_nette: 165, pfu: 49.5 },
  { month: 'Février', pv_brute: 185, mv_brute: -80, pv_nette: 105, pfu: 31.5 },
  { month: 'Mars', pv_brute: 320, mv_brute: -95, pv_nette: 225, pfu: 67.5 },
  { month: 'Avril', pv_brute: 0, mv_brute: 0, pv_nette: 0, pfu: 0 },
  { month: 'Mai', pv_brute: 0, mv_brute: 0, pv_nette: 0, pfu: 0 },
  { month: 'Juin', pv_brute: 0, mv_brute: 0, pv_nette: 0, pfu: 0 },
  { month: 'Juillet', pv_brute: 0, mv_brute: 0, pv_nette: 0, pfu: 0 },
  { month: 'Août', pv_brute: 0, mv_brute: 0, pv_nette: 0, pfu: 0 },
  { month: 'Septembre', pv_brute: 0, mv_brute: 0, pv_nette: 0, pfu: 0 },
  { month: 'Octobre', pv_brute: 0, mv_brute: 0, pv_nette: 0, pfu: 0 },
  { month: 'Novembre', pv_brute: 0, mv_brute: 0, pv_nette: 0, pfu: 0 },
  { month: 'Décembre', pv_brute: 0, mv_brute: 0, pv_nette: 0, pfu: 0 },
]

function fmtCurrency(val, showSign = false) {
  if (val == null) return '—'
  const prefix = showSign && val > 0 ? '+' : ''
  return `${prefix}$${Math.abs(val).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`
}

export default function Tax() {
  const [year, setYear] = useState(2026)
  const { data: summaryData, loading: sLoad } = useApi(`/tax/summary?year=${year}`)
  const { data: monthlyData, loading: mLoad } = useApi(`/tax/monthly?year=${year}`)

  const summary = summaryData || SAMPLE_SUMMARY
  const monthly = monthlyData?.months || SAMPLE_MONTHLY
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
        <MetricCard label="PFU 30% Du" value={summary.total.pfu} prefix="$" color="text-[var(--color-warning)]" />
        <MetricCard label="Plus-Values" value={summary.total.plus_values} prefix="+$" color="text-[var(--color-profit)]" />
        <MetricCard label="Moins-Values" value={Math.abs(summary.total.moins_values)} prefix="-$" color="text-[var(--color-loss)]" />
      </div>

      {/* Summary Table */}
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-3">
          Resume par compte — {year}
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
