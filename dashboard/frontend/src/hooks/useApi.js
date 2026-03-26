import { useState, useEffect, useCallback } from 'react'

const API_BASE = '/api'

export function useApi(endpoint, refreshInterval = null) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}${endpoint}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = await res.json()
      setData(json)
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [endpoint])

  useEffect(() => {
    fetchData()
    if (refreshInterval) {
      const id = setInterval(fetchData, refreshInterval)
      return () => clearInterval(id)
    }
  }, [fetchData, refreshInterval])

  return { data, loading, error, refetch: fetchData }
}
