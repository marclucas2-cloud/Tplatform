import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  ReferenceLine,
} from 'recharts'

function formatDate(date) {
  const d = new Date(date)
  return d.toLocaleDateString('fr-FR', { day: '2-digit', month: 'short' })
}

function getSharpeColor(value) {
  if (value >= 1) return '#22c55e'
  if (value >= 0) return '#f59e0b'
  return '#ef4444'
}

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  const value = payload[0].value
  return (
    <div
      style={{
        background: '#1a1a2e',
        border: '1px solid #333',
        borderRadius: 8,
        padding: '10px 14px',
      }}
    >
      <div style={{ color: '#6b7280', fontSize: 12, marginBottom: 4 }}>
        {formatDate(label)}
      </div>
      <div
        style={{
          fontFamily: 'monospace',
          fontSize: 14,
          color: getSharpeColor(value),
        }}
      >
        Sharpe: {value.toFixed(2)}
      </div>
    </div>
  )
}

function CustomDot({ cx, cy, payload }) {
  if (!payload || cx === undefined || cy === undefined) return null
  return (
    <circle cx={cx} cy={cy} r={3} fill={getSharpeColor(payload.sharpe)} />
  )
}

export default function RollingSharpeChart({ data }) {
  if (!data || data.length === 0) {
    return (
      <div className="flex items-center justify-center h-[250px] text-[var(--color-text-secondary)] text-sm">
        Aucune donnee
      </div>
    )
  }

  const minSharpe = Math.min(...data.map((d) => d.sharpe))
  const maxSharpe = Math.max(...data.map((d) => d.sharpe))
  const yMin = Math.floor(Math.min(minSharpe - 0.5, -1))
  const yMax = Math.ceil(Math.max(maxSharpe + 0.5, 2))

  return (
    <ResponsiveContainer width="100%" height={250}>
      <AreaChart
        data={data}
        margin={{ top: 5, right: 20, bottom: 5, left: 10 }}
      >
        <defs>
          <linearGradient id="sharpeFillAll" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#8b5cf6" stopOpacity={0.25} />
            <stop offset="100%" stopColor="#8b5cf6" stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#2a2b3d" />
        <XAxis
          dataKey="date"
          tickFormatter={formatDate}
          tick={{ fill: '#6b7280', fontSize: 11 }}
          stroke="#2a2b3d"
          minTickGap={40}
        />
        <YAxis
          domain={[yMin, yMax]}
          tick={{ fill: '#6b7280', fontSize: 11 }}
          stroke="#2a2b3d"
          width={40}
        />
        <Tooltip content={<CustomTooltip />} />
        <ReferenceLine
          y={0}
          stroke="#6b7280"
          strokeDasharray="4 4"
          strokeWidth={1}
        />
        <ReferenceLine
          y={1}
          stroke="#22c55e"
          strokeDasharray="4 4"
          strokeWidth={1}
          label={{
            value: 'Sharpe = 1',
            position: 'right',
            fill: '#22c55e',
            fontSize: 10,
          }}
        />
        <Area
          type="monotone"
          dataKey="sharpe"
          stroke="#8b5cf6"
          strokeWidth={2}
          fill="url(#sharpeFillAll)"
          activeDot={<CustomDot />}
          dot={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}
