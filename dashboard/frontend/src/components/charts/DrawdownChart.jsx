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
      <div style={{ fontFamily: 'monospace', fontSize: 14, color: '#ef4444' }}>
        {value.toFixed(2)}%
      </div>
    </div>
  )
}

export default function DrawdownChart({ data, killSwitchThreshold = -5 }) {
  if (!data || data.length === 0) {
    return (
      <div className="flex items-center justify-center h-[250px] text-[var(--color-text-secondary)] text-sm">
        Aucune donnee
      </div>
    )
  }

  const minDD = Math.min(
    ...data.map((d) => d.drawdown_pct),
    killSwitchThreshold
  )
  const yMin = Math.floor(minDD - 1)

  return (
    <ResponsiveContainer width="100%" height={250}>
      <AreaChart data={data} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
        <defs>
          <linearGradient id="drawdownFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#ef4444" stopOpacity={0.05} />
            <stop offset="100%" stopColor="#ef4444" stopOpacity={0.35} />
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
          domain={[yMin, 0]}
          tickFormatter={(v) => `${v}%`}
          tick={{ fill: '#6b7280', fontSize: 11 }}
          stroke="#2a2b3d"
          width={50}
          reversed
        />
        <Tooltip content={<CustomTooltip />} />
        <ReferenceLine
          y={killSwitchThreshold}
          stroke="#ef4444"
          strokeDasharray="8 4"
          strokeWidth={1.5}
          label={{
            value: `Kill switch (${killSwitchThreshold}%)`,
            position: 'right',
            fill: '#ef4444',
            fontSize: 11,
          }}
        />
        <Area
          type="monotone"
          dataKey="drawdown_pct"
          stroke="#ef4444"
          strokeWidth={1.5}
          fill="url(#drawdownFill)"
          activeDot={{ r: 3, fill: '#ef4444' }}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}
