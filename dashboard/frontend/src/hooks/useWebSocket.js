import { useState, useEffect, useRef, useCallback } from 'react'

const RECONNECT_BASE_DELAY = 3000 // 3s
const MAX_RECONNECT_DELAY = 30000 // 30s

export function useWebSocket(url) {
  const [data, setData] = useState(null)
  const [connected, setConnected] = useState(false)
  const wsRef = useRef(null)
  const reconnectAttempt = useRef(0)
  const reconnectTimer = useRef(null)
  const unmountedRef = useRef(false)

  const connect = useCallback(() => {
    if (unmountedRef.current || !url) return

    try {
      const ws = new WebSocket(url)
      wsRef.current = ws

      ws.onopen = () => {
        if (unmountedRef.current) return
        setConnected(true)
        reconnectAttempt.current = 0
      }

      ws.onmessage = (event) => {
        if (unmountedRef.current) return
        try {
          const parsed = JSON.parse(event.data)
          setData(parsed)
        } catch {
          // Non-JSON message, store raw
          setData(event.data)
        }
      }

      ws.onerror = () => {
        // Error handling is done in onclose
      }

      ws.onclose = () => {
        if (unmountedRef.current) return
        setConnected(false)
        wsRef.current = null

        // Auto-reconnect with exponential backoff
        const delay = Math.min(
          RECONNECT_BASE_DELAY * Math.pow(1.5, reconnectAttempt.current),
          MAX_RECONNECT_DELAY
        )
        reconnectAttempt.current += 1

        reconnectTimer.current = setTimeout(() => {
          if (!unmountedRef.current) {
            connect()
          }
        }, delay)
      }
    } catch {
      // Connection failed, retry
      const delay = Math.min(
        RECONNECT_BASE_DELAY * Math.pow(1.5, reconnectAttempt.current),
        MAX_RECONNECT_DELAY
      )
      reconnectAttempt.current += 1
      reconnectTimer.current = setTimeout(() => {
        if (!unmountedRef.current) connect()
      }, delay)
    }
  }, [url])

  useEffect(() => {
    unmountedRef.current = false
    connect()

    return () => {
      unmountedRef.current = true
      clearTimeout(reconnectTimer.current)
      if (wsRef.current) {
        wsRef.current.close()
        wsRef.current = null
      }
    }
  }, [connect])

  return { data, connected }
}
