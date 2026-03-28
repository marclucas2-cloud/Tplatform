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

function CustomTooltip({ active, payload }) {
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
      <div style={{ color: '#6b7280', fontSize: 12, marginBottom: 4 }}>
        {d.bucket}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16 }}>
        <span style={{ color: '#e1e2e8', fontSize: 13 }}>Trades</span>
        <span style={{ fontFamily: 'monospace', color: '#e1e2e8', fontSize: 13 }}>
          {d.count}
        </span>
      </div>
      {d.avg_pnl !== undefined && d.avg_pnl !== null && (
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16 }}>
          <span style={{ color: '#e1e2e8', fontSize: 13 }}>P&L moyen</span>
          <span
            style={{
              fontFamily: 'monospace',
              fontSize: 13,
              color: d.avg_pnl >= 0 ? '#22c55e' : '#ef4444',
            }}
          >
            {d.avg_pnl >= 0 ? '+' : ''}${d.avg_pnl.toFixed(2)}
          </span>
        </div>
      )}
    </div>
  )
}

function isPositiveBucket(bucket) {
  if (typeof bucket !== 'string') return false
  const match = bucket.match(/-?\d+(\.\d+)?/)
  if (!match) return false
  return parseFloat(match[0]) >= 0
}

export default function DistributionChart({ data }) {
  if (!data || data.length === 0) {
    return (
      <div className="flex items-center justify-center h-[300px] text-[var(--color-text-secondary)] text-sm">
        Aucune donnee
      </div>
    )
  }

  return (
    <ResponsiveContainer width="100%" height={300}>
      <BarChart data={data} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#2a2b3d" vertical={false} />
        <XAxis
          dataKey="bucket"
          tick={{ fill: '#6b7280', fontSize: 10 }}
          stroke="#2a2b3d"
          interval={0}
          angle={-30}
          textAnchor="end"
          height={50}
        />
        <YAxis
          tick={{ fill: '#6b7280', fontSize: 11 }}
          stroke="#2a2b3d"
          width={40}
          allowDecimals={false}
        />
        <Tooltip content={<CustomTooltip />} cursor={{ fill: '#2a2b3d' }} />
        <Bar dataKey="count" radius={[4, 4, 0, 0]} maxBarSize={40}>
          {data.map((entry, index) => (
            <Cell
              key={index}
              fill={isPositiveBucket(entry.bucket) ? '#22c55e' : '#ef4444'}
              fillOpacity={0.85}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}
