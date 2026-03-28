import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  CartesianGrid,
} from 'recharts'

const COLORS = {
  total: '#22c55e',
  ibkr: '#3b82f6',
  binance: '#f59e0b',
}

function formatDollar(value) {
  if (Math.abs(value) >= 1000) {
    return `$${(value / 1000).toFixed(1)}K`
  }
  return `$${value.toFixed(0)}`
}

function formatDate(timestamp) {
  const d = new Date(timestamp)
  return d.toLocaleDateString('fr-FR', { day: '2-digit', month: 'short' })
}

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div
      style={{
        background: '#1a1a2e',
        border: '1px solid #333',
        borderRadius: 8,
        padding: '10px 14px',
      }}
    >
      <div style={{ color: '#6b7280', fontSize: 12, marginBottom: 6 }}>
        {formatDate(label)}
      </div>
      {payload.map((entry) => (
        <div
          key={entry.dataKey}
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            gap: 16,
            fontSize: 13,
          }}
        >
          <span style={{ color: entry.color }}>{entry.name}</span>
          <span style={{ color: '#e1e2e8', fontFamily: 'monospace' }}>
            {formatDollar(entry.value)}
          </span>
        </div>
      ))}
    </div>
  )
}

export default function EquityCurve({ data, period, showBrokers = true }) {
  if (!data || data.length === 0) {
    return (
      <div className="flex items-center justify-center h-[350px] text-[var(--color-text-secondary)] text-sm">
        Aucune donnee
      </div>
    )
  }

  return (
    <ResponsiveContainer width="100%" height={350}>
      <LineChart data={data} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#2a2b3d" />
        <XAxis
          dataKey="timestamp"
          tickFormatter={formatDate}
          tick={{ fill: '#6b7280', fontSize: 11 }}
          stroke="#2a2b3d"
          minTickGap={40}
        />
        <YAxis
          tickFormatter={formatDollar}
          tick={{ fill: '#6b7280', fontSize: 11 }}
          stroke="#2a2b3d"
          width={60}
        />
        <Tooltip content={<CustomTooltip />} />
        <Legend
          verticalAlign="bottom"
          height={30}
          wrapperStyle={{ fontSize: 12, color: '#6b7280' }}
        />
        <Line
          type="monotone"
          dataKey="total"
          name="Total"
          stroke={COLORS.total}
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4, fill: COLORS.total }}
        />
        {showBrokers && (
          <Line
            type="monotone"
            dataKey="ibkr"
            name="IBKR"
            stroke={COLORS.ibkr}
            strokeWidth={1.5}
            strokeDasharray="6 3"
            dot={false}
            activeDot={{ r: 3, fill: COLORS.ibkr }}
          />
        )}
        {showBrokers && (
          <Line
            type="monotone"
            dataKey="binance"
            name="Binance"
            stroke={COLORS.binance}
            strokeWidth={1.5}
            strokeDasharray="6 3"
            dot={false}
            activeDot={{ r: 3, fill: COLORS.binance }}
          />
        )}
      </LineChart>
    </ResponsiveContainer>
  )
}
