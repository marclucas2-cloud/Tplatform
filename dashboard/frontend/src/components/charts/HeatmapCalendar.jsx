import { useMemo, useState } from 'react'

const DAY_LABELS = ['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim']
const MONTH_NAMES = [
  'Jan', 'Fev', 'Mar', 'Avr', 'Mai', 'Juin',
  'Juil', 'Aou', 'Sep', 'Oct', 'Nov', 'Dec',
]

function getPnlColor(pnl) {
  if (pnl === null || pnl === undefined) return '#1a1b26'
  if (pnl > 200) return '#16a34a'
  if (pnl > 50) return '#22c55e'
  if (pnl > 0) return '#4ade80'
  if (pnl > -50) return '#f87171'
  if (pnl > -200) return '#ef4444'
  return '#dc2626'
}

function getPnlOpacity(pnl) {
  if (pnl === null || pnl === undefined) return 0.3
  const abs = Math.abs(pnl)
  if (abs > 200) return 1
  if (abs > 100) return 0.85
  if (abs > 50) return 0.7
  if (abs > 10) return 0.55
  return 0.45
}

export default function HeatmapCalendar({ data }) {
  const [hoveredCell, setHoveredCell] = useState(null)

  const { grid, months } = useMemo(() => {
    if (!data || data.length === 0) return { grid: [], months: [] }

    // Build lookup map
    const lookup = {}
    data.forEach((d) => {
      lookup[d.date] = d
    })

    // Find date range
    const dates = data.map((d) => new Date(d.date)).sort((a, b) => a - b)
    const startDate = new Date(dates[0])
    const endDate = new Date(dates[dates.length - 1])

    // Move start to previous Monday
    const startDay = startDate.getDay()
    const mondayOffset = startDay === 0 ? -6 : 1 - startDay
    startDate.setDate(startDate.getDate() + mondayOffset)

    // Build weeks grid
    const weeks = []
    const monthMarkers = []
    const current = new Date(startDate)
    let currentWeek = []
    let lastMonth = -1

    while (current <= endDate || currentWeek.length > 0) {
      // Monday = 0 in our grid
      const dayOfWeek = current.getDay()
      const adjustedDay = dayOfWeek === 0 ? 6 : dayOfWeek - 1

      const dateStr = current.toISOString().slice(0, 10)
      const entry = lookup[dateStr] || null

      currentWeek.push({
        date: dateStr,
        dayIndex: adjustedDay,
        pnl: entry?.pnl ?? null,
        count: entry?.count ?? 0,
        month: current.getMonth(),
        day: current.getDate(),
      })

      // Track month boundaries
      if (current.getMonth() !== lastMonth) {
        monthMarkers.push({
          weekIndex: weeks.length,
          label: MONTH_NAMES[current.getMonth()],
        })
        lastMonth = current.getMonth()
      }

      current.setDate(current.getDate() + 1)

      // End of week (Sunday) or past end date
      if (adjustedDay === 6 || current > endDate) {
        weeks.push(currentWeek)
        currentWeek = []
        if (current > endDate) break
      }
    }

    return { grid: weeks, months: monthMarkers }
  }, [data])

  if (!data || data.length === 0) {
    return (
      <div className="flex items-center justify-center h-[200px] text-[var(--color-text-secondary)] text-sm">
        Aucune donnee
      </div>
    )
  }

  return (
    <div className="relative">
      {/* Day labels column */}
      <div className="flex gap-0.5">
        <div className="flex flex-col gap-0.5 mr-1.5 pt-[18px]">
          {DAY_LABELS.map((label) => (
            <div
              key={label}
              className="text-[10px] text-[var(--color-text-secondary)] leading-none flex items-center justify-end"
              style={{ width: 24, height: 30 }}
            >
              {label}
            </div>
          ))}
        </div>

        {/* Grid */}
        <div className="overflow-x-auto">
          {/* Month labels */}
          <div className="flex gap-0.5 mb-0.5" style={{ height: 14 }}>
            {grid.map((_, weekIdx) => {
              const marker = months.find((m) => m.weekIndex === weekIdx)
              return (
                <div
                  key={weekIdx}
                  className="text-[10px] text-[var(--color-text-secondary)]"
                  style={{ width: 30, minWidth: 30 }}
                >
                  {marker?.label || ''}
                </div>
              )
            })}
          </div>

          {/* Calendar rows */}
          {DAY_LABELS.map((_, dayIdx) => (
            <div key={dayIdx} className="flex gap-0.5">
              {grid.map((week, weekIdx) => {
                const cell = week.find((c) => c.dayIndex === dayIdx)
                if (!cell) {
                  return (
                    <div
                      key={weekIdx}
                      style={{
                        width: 30,
                        height: 30,
                        minWidth: 30,
                      }}
                    />
                  )
                }
                const cellKey = `${weekIdx}-${dayIdx}`
                const isHovered = hoveredCell === cellKey
                return (
                  <div
                    key={weekIdx}
                    className="relative cursor-pointer transition-transform"
                    style={{
                      width: 30,
                      height: 30,
                      minWidth: 30,
                      backgroundColor: getPnlColor(cell.pnl),
                      opacity: getPnlOpacity(cell.pnl),
                      borderRadius: 4,
                      border: isHovered
                        ? '2px solid #e1e2e8'
                        : '1px solid rgba(42,43,61,0.5)',
                      transform: isHovered ? 'scale(1.15)' : 'scale(1)',
                      zIndex: isHovered ? 10 : 1,
                    }}
                    onMouseEnter={() => setHoveredCell(cellKey)}
                    onMouseLeave={() => setHoveredCell(null)}
                  >
                    {isHovered && (
                      <div
                        className="absolute z-20 whitespace-nowrap pointer-events-none"
                        style={{
                          bottom: '110%',
                          left: '50%',
                          transform: 'translateX(-50%)',
                          background: '#1a1a2e',
                          border: '1px solid #333',
                          borderRadius: 6,
                          padding: '6px 10px',
                          fontSize: 11,
                        }}
                      >
                        <div style={{ color: '#6b7280' }}>{cell.date}</div>
                        {cell.pnl !== null ? (
                          <>
                            <div
                              style={{
                                fontFamily: 'monospace',
                                color: cell.pnl >= 0 ? '#22c55e' : '#ef4444',
                                fontWeight: 600,
                              }}
                            >
                              {cell.pnl >= 0 ? '+' : ''}${cell.pnl.toFixed(2)}
                            </div>
                            {cell.count > 0 && (
                              <div style={{ color: '#6b7280' }}>
                                {cell.count} trade{cell.count > 1 ? 's' : ''}
                              </div>
                            )}
                          </>
                        ) : (
                          <div style={{ color: '#6b7280' }}>Pas de donnees</div>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          ))}
        </div>
      </div>

      {/* Legend */}
      <div className="flex items-center gap-2 mt-3 ml-8">
        <span className="text-[10px] text-[var(--color-text-secondary)]">Perte</span>
        {['#dc2626', '#ef4444', '#f87171'].map((c) => (
          <div
            key={c}
            style={{
              width: 12,
              height: 12,
              backgroundColor: c,
              borderRadius: 2,
              opacity: 0.85,
            }}
          />
        ))}
        <div
          style={{
            width: 12,
            height: 12,
            backgroundColor: '#1a1b26',
            borderRadius: 2,
            border: '1px solid #2a2b3d',
          }}
        />
        {['#4ade80', '#22c55e', '#16a34a'].map((c) => (
          <div
            key={c}
            style={{
              width: 12,
              height: 12,
              backgroundColor: c,
              borderRadius: 2,
              opacity: 0.85,
            }}
          />
        ))}
        <span className="text-[10px] text-[var(--color-text-secondary)]">Gain</span>
      </div>
    </div>
  )
}
