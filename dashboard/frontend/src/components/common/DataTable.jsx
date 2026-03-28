import { useState, useMemo } from 'react'
import { ChevronDown, ChevronUp, ChevronLeft, ChevronRight } from 'lucide-react'

/**
 * columns format: [{ key, label, align ('left'|'right'|'center'), format (fn) }]
 */
export default function DataTable({ columns = [], data = [], sortable = true, pageSize = 20 }) {
  const [sortKey, setSortKey] = useState(null)
  const [sortDir, setSortDir] = useState('desc')
  const [page, setPage] = useState(0)

  const toggleSort = (key) => {
    if (!sortable) return
    if (sortKey === key) {
      setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'))
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
    setPage(0)
  }

  const sorted = useMemo(() => {
    if (!sortKey) return data
    return [...data].sort((a, b) => {
      const va = a[sortKey]
      const vb = b[sortKey]
      if (va == null) return 1
      if (vb == null) return -1
      if (typeof va === 'number' && typeof vb === 'number') {
        return sortDir === 'desc' ? vb - va : va - vb
      }
      const sa = String(va).toLowerCase()
      const sb = String(vb).toLowerCase()
      if (sortDir === 'desc') return sb.localeCompare(sa)
      return sa.localeCompare(sb)
    })
  }, [data, sortKey, sortDir])

  const totalPages = Math.max(1, Math.ceil(sorted.length / pageSize))
  const paged = sorted.slice(page * pageSize, (page + 1) * pageSize)

  const alignClass = (align) => {
    if (align === 'right') return 'text-right'
    if (align === 'center') return 'text-center'
    return 'text-left'
  }

  return (
    <div className="bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-[var(--color-text-secondary)] uppercase tracking-wider bg-[var(--color-bg-primary)]">
              {columns.map((col) => (
                <th
                  key={col.key}
                  className={`py-3 px-4 ${alignClass(col.align)} ${sortable ? 'cursor-pointer hover:text-[var(--color-text-primary)] select-none' : ''}`}
                  onClick={() => toggleSort(col.key)}
                >
                  <span className="inline-flex items-center gap-1">
                    {col.label}
                    {sortable && sortKey === col.key && (
                      sortDir === 'desc'
                        ? <ChevronDown size={12} />
                        : <ChevronUp size={12} />
                    )}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {paged.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className="py-12 text-center text-[var(--color-text-secondary)]">
                  Aucune donnee
                </td>
              </tr>
            ) : (
              paged.map((row, i) => (
                <tr
                  key={row.id || i}
                  className="border-t border-[var(--color-border)]/50 hover:bg-[var(--color-bg-hover)] transition-colors"
                >
                  {columns.map((col) => {
                    const raw = row[col.key]
                    const formatted = col.format ? col.format(raw, row) : raw
                    return (
                      <td key={col.key} className={`py-3 px-4 font-mono ${alignClass(col.align)}`}>
                        {formatted ?? '—'}
                      </td>
                    )
                  })}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between px-4 py-2.5 border-t border-[var(--color-border)] bg-[var(--color-bg-primary)]">
          <span className="text-xs text-[var(--color-text-secondary)]">
            {sorted.length} resultats — page {page + 1}/{totalPages}
          </span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="p-1 rounded hover:bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)] disabled:opacity-30 disabled:cursor-not-allowed"
            >
              <ChevronLeft size={16} />
            </button>
            {/* Page number pills — show max 5 */}
            {Array.from({ length: Math.min(totalPages, 5) }, (_, i) => {
              let pageNum
              if (totalPages <= 5) {
                pageNum = i
              } else if (page < 3) {
                pageNum = i
              } else if (page > totalPages - 4) {
                pageNum = totalPages - 5 + i
              } else {
                pageNum = page - 2 + i
              }
              return (
                <button
                  key={pageNum}
                  onClick={() => setPage(pageNum)}
                  className={`w-7 h-7 rounded text-xs font-mono transition-colors ${
                    page === pageNum
                      ? 'bg-[var(--color-accent)]/20 text-[var(--color-accent)]'
                      : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]'
                  }`}
                >
                  {pageNum + 1}
                </button>
              )
            })}
            <button
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={page >= totalPages - 1}
              className="p-1 rounded hover:bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)] disabled:opacity-30 disabled:cursor-not-allowed"
            >
              <ChevronRight size={16} />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
