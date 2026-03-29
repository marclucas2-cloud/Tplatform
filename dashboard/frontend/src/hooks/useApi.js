import { useState, useEffect, useCallback } from 'react'

const API_BASE = '/api'
const TOKEN_KEY = 'dashboard_token'

function getAuthHeaders() {
  const token = localStorage.getItem(TOKEN_KEY)
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export function useApi(endpoint, refreshInterval = null) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}${endpoint}`, {
        headers: getAuthHeaders(),
      })
      if (res.status === 401) {
        localStorage.removeItem(TOKEN_KEY)
        window.location.href = '/login'
        return
      }
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

/**
 * Standalone authenticated fetch for non-hook usage.
 */
export async function apiFetch(endpoint, options = {}) {
  const res = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    headers: { ...getAuthHeaders(), ...options.headers },
  })
  if (res.status === 401) {
    localStorage.removeItem(TOKEN_KEY)
    window.location.href = '/login'
    throw new Error('Unauthorized')
  }
  return res
}
