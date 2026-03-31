import { useParams, Link } from 'react-router-dom'
import { useApi } from '../hooks/useApi'
import { TierBadge, PhaseBadge, PHASE_ORDER, PHASE_CONFIG } from '../components/StrategyBadge'
import { ArrowLeft, Shield, Target, Clock, BarChart3, Zap, Info } from 'lucide-react'

const LIFECYCLE_STEPS = ['CODE', 'WF_PENDING', 'PAPER', 'PROBATION', 'LIVE']

function LifecycleTimeline({ currentPhase }) {
  if (!currentPhase || currentPhase === 'REJECTED') {
    return (
      <div className="flex items-center gap-2 text-xs">
        <span className="text-red-400 font-semibold">✕ REJECTED</span>
        <span className="text-[var(--color-text-secondary)]">— Rejete par walk-forward</span>
      </div>
    )
  }
  const currentIdx = LIFECYCLE_STEPS.indexOf(currentPhase)
  return (
    <div className="flex items-center gap-0">
      {LIFECYCLE_STEPS.map((step, i) => {
        const cfg = PHASE_CONFIG[step]
        const isActive = i <= currentIdx
        const isCurrent = step === currentPhase
        return (
          <div key={step} className="flex items-center">
            {i > 0 && (
              <div className={`w-8 h-0.5 ${isActive ? 'bg-emerald-500' : 'bg-[var(--color-border)]'}`} />
            )}
            <div className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-semibold border ${
              isCurrent ? `${cfg.bg} ${cfg.text} ${cfg.border}` :
              isActive ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' :
              'bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)] border-[var(--color-border)]'
            }`}>
              <span>{cfg.icon}</span>
              <span>{cfg.label}</span>
            </div>
          </div>
        )
      })}
    </div>
  )
}

export default function StrategyDetail() {
  const { id } = useParams()
  const { data, loading, error } = useApi(`/strategies/${id}`, 60000)

  if (loading) return <div className="text-center py-12 text-[var(--color-text-secondary)]">Chargement...</div>
  if (error || !data || data.error) return (
    <div className="text-center py-12">
      <p className="text-[var(--color-loss)]">Strategie introuvable: {id}</p>
      <Link to="/strategies" className="text-[var(--color-accent)] text-sm mt-2 inline-block">Retour aux strategies</Link>
    </div>
  )

  const params = data.parameters || {}
  const bt = data.backtest || {}

  return (
    <div className="space-y-6 max-w-5xl">
      {/* Back + Header */}
      <div>
        <Link to="/strategies" className="inline-flex items-center gap-1 text-sm text-[var(--color-text-secondary)] hover:text-[var(--color-accent)] mb-3">
          <ArrowLeft size={14} /> Retour
        </Link>
        <div className="flex items-center gap-3">
          <TierBadge tier={data.tier} />
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">{data.name}</h1>
          <span className="px-2 py-0.5 rounded text-xs bg-[var(--color-info)]/15 text-[var(--color-info)]">{data.frequency}</span>
          {data.edge_type && (
            <span className="px-2 py-0.5 rounded text-xs bg-[var(--color-accent)]/15 text-[var(--color-accent)]">{data.edge_type}</span>
          )}
        </div>
        <div className="mt-1 text-sm text-[var(--color-text-secondary)]">
          Allocation: <span className="font-mono text-[var(--color-text-primary)]">{data.allocation_pct}%</span>
          {' '} | Sharpe: <span className="font-mono text-[var(--color-text-primary)]">{data.sharpe}</span>
          {data.broker && <>{' '} | Broker: <span className="font-mono">{data.broker}</span></>}
          {data.asset_class && <>{' '} | Classe: <span className="font-mono">{data.asset_class}</span></>}
        </div>
      </div>

      {/* Lifecycle Timeline */}
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-xs font-semibold text-[var(--color-text-secondary)] uppercase tracking-wider">Cycle de Vie</h2>
          {data.phase_since && (
            <span className="text-xs text-[var(--color-text-secondary)]">
              Phase actuelle depuis: <span className="font-mono">{data.phase_since}</span>
            </span>
          )}
        </div>
        <LifecycleTimeline currentPhase={data.phase} />
      </div>

      {/* Description de l'edge */}
      {data.description && (
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-5">
          <div className="flex items-center gap-2 mb-3">
            <Info size={16} className="text-[var(--color-info)]" />
            <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">Comment ca marche</h2>
          </div>
          <p className="text-sm text-[var(--color-text-secondary)] leading-relaxed">{data.description}</p>

          {data.why_it_works && (
            <div className="mt-4 pt-3 border-t border-[var(--color-border)]">
              <div className="flex items-center gap-2 mb-2">
                <Zap size={14} className="text-[var(--color-warning)]" />
                <span className="text-xs font-semibold text-[var(--color-text-primary)] uppercase tracking-wider">Pourquoi ca marche</span>
              </div>
              <p className="text-sm text-[var(--color-text-secondary)] leading-relaxed">{data.why_it_works}</p>
            </div>
          )}
        </div>
      )}

      {/* Parametres + SL/TP */}
      {Object.keys(params).length > 0 && (
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-5">
          <div className="flex items-center gap-2 mb-4">
            <Target size={16} className="text-[var(--color-accent)]" />
            <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">Parametres & Stop Loss / Take Profit</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-[var(--color-text-secondary)] uppercase tracking-wider border-b border-[var(--color-border)]">
                  <th className="text-left py-2 pr-4">Parametre</th>
                  <th className="text-left py-2 pr-4">Valeur</th>
                  <th className="text-left py-2">Description</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(params).map(([key, param]) => {
                  const isSL = key.toLowerCase().includes('stop')
                  const isTP = key.toLowerCase().includes('take') || key.toLowerCase().includes('profit') || key.toLowerCase().includes('target')
                  const isTiming = key.toLowerCase().includes('timing') || key.toLowerCase().includes('jour')
                  return (
                    <tr key={key} className={`border-b border-[var(--color-border)]/30 ${isSL ? 'bg-red-500/5' : isTP ? 'bg-green-500/5' : ''}`}>
                      <td className="py-2.5 pr-4">
                        <div className="flex items-center gap-2">
                          {isSL && <Shield size={12} className="text-[var(--color-loss)]" />}
                          {isTP && <Target size={12} className="text-[var(--color-profit)]" />}
                          {isTiming && <Clock size={12} className="text-[var(--color-info)]" />}
                          <span className={`font-mono text-xs ${isSL ? 'text-[var(--color-loss)]' : isTP ? 'text-[var(--color-profit)]' : 'text-[var(--color-text-primary)]'}`}>
                            {key.replace(/_/g, ' ')}
                          </span>
                        </div>
                      </td>
                      <td className="py-2.5 pr-4">
                        <span className={`font-mono text-sm font-semibold ${isSL ? 'text-[var(--color-loss)]' : isTP ? 'text-[var(--color-profit)]' : 'text-[var(--color-text-primary)]'}`}>
                          {param.value}
                        </span>
                      </td>
                      <td className="py-2.5 text-[var(--color-text-secondary)] text-xs">{param.description}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Metriques backtest */}
      {Object.keys(bt).length > 0 && (
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-5">
          <div className="flex items-center gap-2 mb-4">
            <BarChart3 size={16} className="text-[var(--color-profit)]" />
            <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">Resultats Backtest</h2>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
            <div>
              <div className="text-xs text-[var(--color-text-secondary)]">Sharpe</div>
              <div className="font-mono text-xl font-bold text-[var(--color-text-primary)]">{bt.sharpe}</div>
            </div>
            <div>
              <div className="text-xs text-[var(--color-text-secondary)]">Win Rate</div>
              <div className="font-mono text-xl font-bold text-[var(--color-profit)]">{bt.win_rate}%</div>
            </div>
            <div>
              <div className="text-xs text-[var(--color-text-secondary)]">Profit Factor</div>
              <div className="font-mono text-xl font-bold">{bt.profit_factor}</div>
            </div>
            <div>
              <div className="text-xs text-[var(--color-text-secondary)]">Max Drawdown</div>
              <div className="font-mono text-xl font-bold text-[var(--color-loss)]">{bt.max_dd}%</div>
            </div>
            <div>
              <div className="text-xs text-[var(--color-text-secondary)]">Trades</div>
              <div className="font-mono text-xl font-bold">{bt.trades}</div>
            </div>
          </div>
        </div>
      )}

      {/* Tickers */}
      {data.tickers && data.tickers.length > 0 && (
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-5">
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-3">Tickers trades</h2>
          <div className="flex flex-wrap gap-2">
            {data.tickers.map((t, i) => (
              <span key={i} className="px-2 py-1 rounded bg-[var(--color-bg-hover)] text-xs font-mono text-[var(--color-text-primary)] border border-[var(--color-border)]">
                {t}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
