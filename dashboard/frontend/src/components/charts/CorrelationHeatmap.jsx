/**
 * Matrice de correlation inter-strategies.
 * Utilise un grid de cellules colorees (vert = decorelle, rouge = correle).
 */
export default function CorrelationHeatmap({ strategies, matrix }) {
  if (!strategies || !matrix || strategies.length === 0) {
    return (
      <div className="flex items-center justify-center h-48 text-sm text-[var(--color-text-secondary)]">
        Pas assez de donnees pour la matrice
      </div>
    )
  }

  const n = strategies.length
  const cellSize = Math.min(36, Math.floor(300 / n))

  function getColor(val) {
    if (val == null) return 'transparent'
    const abs = Math.abs(val)
    if (val >= 0.7) return 'rgba(239, 68, 68, 0.8)'    // rouge fort
    if (val >= 0.4) return 'rgba(239, 68, 68, 0.4)'    // rouge moyen
    if (val >= 0.1) return 'rgba(239, 68, 68, 0.15)'   // rouge faible
    if (val >= -0.1) return 'rgba(107, 114, 128, 0.2)'  // neutre
    if (val >= -0.4) return 'rgba(34, 197, 94, 0.2)'   // vert faible
    return 'rgba(34, 197, 94, 0.5)'                      // vert fort (decorelle)
  }

  // Shorten strategy names
  const labels = strategies.map(s => {
    const name = s.replace(/_/g, ' ')
    return name.length > 8 ? name.slice(0, 7) + '.' : name
  })

  return (
    <div className="overflow-x-auto">
      <div className="inline-block">
        {/* Header row */}
        <div className="flex" style={{ marginLeft: cellSize * 2.5 }}>
          {labels.map((label, i) => (
            <div key={i} style={{ width: cellSize, height: cellSize * 2 }}
              className="flex items-end justify-center">
              <span className="text-[8px] text-[var(--color-text-secondary)] transform -rotate-45 origin-bottom-left whitespace-nowrap">
                {label}
              </span>
            </div>
          ))}
        </div>
        {/* Matrix rows */}
        {matrix.map((row, i) => (
          <div key={i} className="flex items-center">
            <div style={{ width: cellSize * 2.5 }} className="text-right pr-2">
              <span className="text-[9px] text-[var(--color-text-secondary)] font-mono truncate">
                {labels[i]}
              </span>
            </div>
            {row.map((val, j) => (
              <div key={j}
                style={{
                  width: cellSize, height: cellSize,
                  backgroundColor: i === j ? 'rgba(107, 114, 128, 0.3)' : getColor(val),
                }}
                className="border border-[var(--color-bg-primary)]/50 flex items-center justify-center cursor-default"
                title={`${strategies[i]} / ${strategies[j]}: ${val?.toFixed(2) ?? 'N/A'}`}
              >
                {cellSize >= 28 && val != null && i !== j && (
                  <span className="text-[8px] font-mono text-[var(--color-text-primary)]">
                    {val.toFixed(1)}
                  </span>
                )}
              </div>
            ))}
          </div>
        ))}
      </div>
      {/* Legend */}
      <div className="flex items-center gap-4 mt-3 text-[9px] text-[var(--color-text-secondary)]">
        <div className="flex items-center gap-1">
          <div className="w-3 h-3 rounded" style={{ backgroundColor: 'rgba(34, 197, 94, 0.5)' }} />
          <span>Decorelle (&lt;-0.4)</span>
        </div>
        <div className="flex items-center gap-1">
          <div className="w-3 h-3 rounded" style={{ backgroundColor: 'rgba(107, 114, 128, 0.2)' }} />
          <span>Neutre</span>
        </div>
        <div className="flex items-center gap-1">
          <div className="w-3 h-3 rounded" style={{ backgroundColor: 'rgba(239, 68, 68, 0.8)' }} />
          <span>Correle (&gt;0.7)</span>
        </div>
      </div>
    </div>
  )
}
