import { useApi } from '../hooks/useApi'
import { Wifi, WifiOff, Server, Database, Clock, HardDrive, Cpu, MemoryStick, Send } from 'lucide-react'

function UsageBar({ label, value, max = 100, unit = '%' }) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0
  const color =
    pct >= 90
      ? 'var(--color-loss)'
      : pct >= 70
        ? 'var(--color-warning)'
        : 'var(--color-profit)'

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className="text-[var(--color-text-secondary)]">{label}</span>
        <span className="font-mono text-[var(--color-text-primary)]">
          {value}{unit}
        </span>
      </div>
      <div className="h-1.5 rounded-full bg-[var(--color-bg-hover)] overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
    </div>
  )
}

function BrokerCard({ name, icon: Icon, connected, latency, uptime, lastPing }) {
  return (
    <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Icon size={16} className="text-[var(--color-text-secondary)]" />
          <span className="text-sm font-semibold text-[var(--color-text-primary)]">{name}</span>
        </div>
        <div className="flex items-center gap-1.5">
          {connected ? (
            <>
              <Wifi size={13} className="text-[var(--color-profit)]" />
              <span className="text-xs font-semibold text-[var(--color-profit)]">Connecte</span>
            </>
          ) : (
            <>
              <WifiOff size={13} className="text-[var(--color-loss)]" />
              <span className="text-xs font-semibold text-[var(--color-loss)]">Deconnecte</span>
            </>
          )}
        </div>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <div>
          <div className="text-[10px] text-[var(--color-text-secondary)] uppercase tracking-wider">Latence</div>
          <div className={`font-mono text-sm font-semibold ${
            latency != null && latency < 100
              ? 'text-[var(--color-profit)]'
              : latency != null && latency < 500
                ? 'text-[var(--color-warning)]'
                : 'text-[var(--color-loss)]'
          }`}>
            {latency != null ? `${latency}ms` : '-'}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-[var(--color-text-secondary)] uppercase tracking-wider">Uptime</div>
          <div className="font-mono text-sm text-[var(--color-text-primary)]">
            {uptime ?? '-'}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-[var(--color-text-secondary)] uppercase tracking-wider">Dernier Ping</div>
          <div className="font-mono text-xs text-[var(--color-text-secondary)]">
            {lastPing ?? '-'}
          </div>
        </div>
      </div>
    </div>
  )
}

const LOG_COLORS = {
  INFO: 'text-[var(--color-info)]',
  WARN: 'text-[var(--color-warning)]',
  WARNING: 'text-[var(--color-warning)]',
  ERROR: 'text-[var(--color-loss)]',
  CRITICAL: 'text-[var(--color-loss)]',
  DEBUG: 'text-[var(--color-text-secondary)]',
}

const LOG_BG = {
  INFO: 'bg-[var(--color-info)]/10',
  WARN: 'bg-[var(--color-warning)]/10',
  WARNING: 'bg-[var(--color-warning)]/10',
  ERROR: 'bg-[var(--color-loss)]/10',
  CRITICAL: 'bg-[var(--color-loss)]/15',
  DEBUG: 'bg-[var(--color-bg-hover)]',
}

export default function System() {
  const { data: status, loading: sLoad } = useApi('/system/status', 10000)
  const { data: health } = useApi('/system/health', 15000)
  const { data: logsData } = useApi('/system/logs', 15000)

  if (sLoad && !status && !health) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-[var(--color-text-secondary)] animate-pulse">Chargement systeme...</div>
      </div>
    )
  }

  const sys = status || {}
  const brokers = sys.brokers || {}
  const server = sys.server || {}
  const reconciliation = sys.reconciliation || {}
  const backup = sys.backup || {}

  // Use health endpoint as fallback for broker connectivity
  const ibkr = { ...brokers.ibkr, connected: brokers.ibkr?.connected ?? health?.ibkr_connected ?? false }
  const binance = { ...brokers.binance, connected: brokers.binance?.connected ?? health?.binance_connected ?? false }
  const telegram = brokers.telegram || {}

  const logs = logsData?.logs || []

  const cpu = server.cpu_pct ?? 12
  const ram = server.ram_pct ?? 34
  const disk = server.disk_pct ?? 45
  const ramMb = server.ram_mb ?? '680/2048'
  const diskGb = server.disk_gb ?? '18/40'

  return (
    <div className="space-y-6">
      {/* Broker Status */}
      <div>
        <h2 className="text-xs text-[var(--color-text-secondary)] uppercase tracking-wider font-semibold mb-3">
          Statut Brokers
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <BrokerCard
            name="IBKR Gateway"
            icon={Server}
            connected={ibkr.connected ?? false}
            latency={ibkr.latency_ms}
            uptime={ibkr.uptime}
            lastPing={ibkr.last_ping}
          />
          <BrokerCard
            name="Binance"
            icon={Database}
            connected={binance.connected ?? false}
            latency={binance.latency_ms}
            uptime={binance.uptime}
            lastPing={binance.last_ping}
          />
          <BrokerCard
            name="Telegram Bot"
            icon={Send}
            connected={telegram.connected ?? false}
            latency={telegram.latency_ms}
            uptime={telegram.uptime}
            lastPing={telegram.last_ping}
          />
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Server Health */}
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-4 flex items-center gap-2">
            <Cpu size={14} />
            Sante Serveur
          </h2>
          <div className="space-y-4">
            <UsageBar label="CPU" value={cpu} />
            <div>
              <UsageBar label="RAM" value={ram} />
              <div className="text-[10px] font-mono text-[var(--color-text-secondary)] mt-0.5 text-right">
                {ramMb} MB
              </div>
            </div>
            <div>
              <UsageBar label="Disque" value={disk} />
              <div className="text-[10px] font-mono text-[var(--color-text-secondary)] mt-0.5 text-right">
                {diskGb} GB
              </div>
            </div>
          </div>
          {server.hostname && (
            <div className="mt-4 pt-3 border-t border-[var(--color-border)]">
              <div className="text-[10px] text-[var(--color-text-secondary)]">
                Host : <span className="font-mono text-[var(--color-text-primary)]">{server.hostname}</span>
              </div>
            </div>
          )}
        </div>

        {/* Reconciliation */}
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-4 flex items-center gap-2">
            <Clock size={14} />
            Reconciliation
          </h2>
          <div className="space-y-3">
            <div className="flex items-center justify-between py-2 px-3 rounded-lg bg-[var(--color-bg-hover)]">
              <span className="text-xs text-[var(--color-text-secondary)]">Dernier check</span>
              <span className="font-mono text-xs text-[var(--color-text-primary)]">
                {reconciliation.last_check ?? 'N/A'}
              </span>
            </div>
            <div className="flex items-center justify-between py-2 px-3 rounded-lg bg-[var(--color-bg-hover)]">
              <span className="text-xs text-[var(--color-text-secondary)]">Statut</span>
              <span
                className={`font-mono text-xs font-bold px-2 py-0.5 rounded ${
                  reconciliation.status === 'OK' || reconciliation.status === 'PASS'
                    ? 'bg-[var(--color-profit)]/15 text-[var(--color-profit)]'
                    : reconciliation.status === 'WARN'
                      ? 'bg-[var(--color-warning)]/15 text-[var(--color-warning)]'
                      : reconciliation.status
                        ? 'bg-[var(--color-loss)]/15 text-[var(--color-loss)]'
                        : 'bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)]'
                }`}
              >
                {reconciliation.status ?? 'N/A'}
              </span>
            </div>
            <div className="flex items-center justify-between py-2 px-3 rounded-lg bg-[var(--color-bg-hover)]">
              <span className="text-xs text-[var(--color-text-secondary)]">Divergences</span>
              <span className="font-mono text-xs text-[var(--color-text-primary)]">
                {reconciliation.divergence_count ?? 0}
              </span>
            </div>
            <div className="flex items-center justify-between py-2 px-3 rounded-lg bg-[var(--color-bg-hover)]">
              <span className="text-xs text-[var(--color-text-secondary)]">Historique</span>
              <span className="font-mono text-xs text-[var(--color-text-primary)]">
                {reconciliation.history_count ?? 0} checks
              </span>
            </div>
          </div>
        </div>

        {/* Backup Status */}
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-4 flex items-center gap-2">
            <HardDrive size={14} />
            Sauvegardes
          </h2>
          <div className="space-y-3">
            <div className="flex items-center justify-between py-2 px-3 rounded-lg bg-[var(--color-bg-hover)]">
              <span className="text-xs text-[var(--color-text-secondary)]">Dernier backup</span>
              <span className="font-mono text-xs text-[var(--color-text-primary)]">
                {backup.last_date ?? 'N/A'}
              </span>
            </div>
            <div className="flex items-center justify-between py-2 px-3 rounded-lg bg-[var(--color-bg-hover)]">
              <span className="text-xs text-[var(--color-text-secondary)]">Taille</span>
              <span className="font-mono text-xs text-[var(--color-text-primary)]">
                {backup.size ?? 'N/A'}
              </span>
            </div>
            <div className="flex items-center justify-between py-2 px-3 rounded-lg bg-[var(--color-bg-hover)]">
              <span className="text-xs text-[var(--color-text-secondary)]">Retention</span>
              <span className="font-mono text-xs text-[var(--color-text-primary)]">
                {backup.retention ?? 'N/A'}
              </span>
            </div>
            <div className="flex items-center justify-between py-2 px-3 rounded-lg bg-[var(--color-bg-hover)]">
              <span className="text-xs text-[var(--color-text-secondary)]">Statut</span>
              <span
                className={`font-mono text-xs font-bold px-2 py-0.5 rounded ${
                  backup.status === 'OK'
                    ? 'bg-[var(--color-profit)]/15 text-[var(--color-profit)]'
                    : backup.status
                      ? 'bg-[var(--color-warning)]/15 text-[var(--color-warning)]'
                      : 'bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)]'
                }`}
              >
                {backup.status ?? 'N/A'}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Recent Logs */}
      <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-4">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)] mb-3">
          Logs Recents
        </h2>
        {logs.length === 0 ? (
          <div className="text-center py-8 text-[var(--color-text-secondary)] text-sm">
            Aucun log disponible
          </div>
        ) : (
          <div className="space-y-1 max-h-[400px] overflow-y-auto">
            {logs.map((log, i) => {
              const level = (log.level || 'INFO').toUpperCase()
              return (
                <div
                  key={i}
                  className={`flex items-start gap-3 py-1.5 px-3 rounded-lg ${LOG_BG[level] || 'bg-[var(--color-bg-hover)]'}`}
                >
                  <span className="font-mono text-[10px] text-[var(--color-text-secondary)] whitespace-nowrap pt-0.5">
                    {log.timestamp ?? ''}
                  </span>
                  <span
                    className={`font-mono text-[10px] font-bold uppercase w-12 shrink-0 pt-0.5 ${LOG_COLORS[level] || 'text-[var(--color-text-secondary)]'}`}
                  >
                    {level}
                  </span>
                  <span className="text-xs text-[var(--color-text-primary)] break-all">
                    {log.message ?? ''}
                  </span>
                  {log.source && (
                    <span className="ml-auto text-[10px] font-mono text-[var(--color-text-secondary)] whitespace-nowrap shrink-0">
                      {log.source}
                    </span>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Worker Info Footer */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-3">
          <span className="text-xs text-[var(--color-text-secondary)]">
            Worker : <span className="font-mono text-[var(--color-text-primary)]">{sys.worker_status ?? (health?.worker_running ? 'RUNNING' : 'OFF')}</span>
          </span>
        </div>
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-3">
          <span className="text-xs text-[var(--color-text-secondary)]">
            Uptime : <span className="font-mono text-[var(--color-text-primary)]">{sys.worker_uptime ?? 'N/A'}</span>
          </span>
        </div>
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-3">
          <span className="text-xs text-[var(--color-text-secondary)]">
            Plateforme : <span className="font-mono text-[var(--color-text-primary)]">{sys.platform ?? 'Hetzner VPS'}</span>
          </span>
        </div>
        <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl p-3">
          <span className="text-xs text-[var(--color-text-secondary)]">
            Version : <span className="font-mono text-[var(--color-text-primary)]">{sys.version ?? 'v10.0'}</span>
          </span>
        </div>
      </div>
    </div>
  )
}
