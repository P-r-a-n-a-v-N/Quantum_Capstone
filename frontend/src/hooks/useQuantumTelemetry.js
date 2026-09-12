import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Single source of live telemetry for the whole dashboard.
 *
 * Lifecycle, in order:
 *   1. REST `/api/telemetry`  -- paints the grid immediately, before the socket
 *      has even finished its handshake.
 *   2. REST `/api/history`    -- seeds the queue chart from SQLite, so the chart
 *      has a shape on first paint instead of being empty for a minute.
 *   3. WebSocket `/ws/telemetry` -- takes over and pushes every subsequent tick.
 *
 * Reconnection uses exponential backoff with jitter. Without jitter, every tab
 * a user has open would reconnect on the same schedule and hit the server in a
 * synchronised burst the moment it came back up.
 */

const MAX_CHART_POINTS = 180 // ~36 minutes at a 12s cadence
const BASE_RECONNECT_MS = 1000
const MAX_RECONNECT_MS = 30000

function websocketUrl() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws/telemetry`
}

/** Collapse a snapshot's backends into one timestamped row for the chart. */
function toChartRow(snapshot) {
  const row = { t: new Date(snapshot.generated_at).getTime() }
  for (const backend of snapshot.backends) {
    if (!backend.is_simulator) row[backend.name] = backend.queue_length
  }
  return row
}

export function useQuantumTelemetry() {
  const [snapshot, setSnapshot] = useState(null)
  const [chartData, setChartData] = useState([])
  const [connectionState, setConnectionState] = useState('connecting')
  const [error, setError] = useState(null)

  const socketRef = useRef(null)
  const retryTimerRef = useRef(null)
  const attemptRef = useRef(0)
  // Guards against a reconnect being scheduled after the component unmounts,
  // which would otherwise resurrect a socket for a dead page.
  const activeRef = useRef(true)

  const pushSnapshot = useCallback((next) => {
    setSnapshot(next)
    setChartData((previous) => {
      const row = toChartRow(next)
      // Snapshots can repeat if a tick is re-broadcast; never duplicate an x value.
      if (previous.length && previous[previous.length - 1].t === row.t) return previous
      const merged = [...previous, row]
      return merged.length > MAX_CHART_POINTS
        ? merged.slice(merged.length - MAX_CHART_POINTS)
        : merged
    })
  }, [])

  const connect = useCallback(() => {
    if (!activeRef.current) return

    let socket
    try {
      socket = new WebSocket(websocketUrl())
    } catch {
      scheduleReconnect()
      return
    }
    socketRef.current = socket

    socket.onopen = () => {
      if (!activeRef.current) return
      attemptRef.current = 0
      setConnectionState('open')
      setError(null)
    }

    socket.onmessage = (event) => {
      if (!activeRef.current) return
      if (event.data === 'pong') return
      try {
        pushSnapshot(JSON.parse(event.data))
      } catch {
        // A malformed frame must not tear down a working connection.
      }
    }

    socket.onerror = () => {
      if (activeRef.current) setError('WebSocket error')
    }

    socket.onclose = () => {
      if (!activeRef.current) return
      setConnectionState('reconnecting')
      scheduleReconnect()
    }

    function scheduleReconnect() {
      if (!activeRef.current) return
      const attempt = attemptRef.current++
      const backoff = Math.min(BASE_RECONNECT_MS * 2 ** attempt, MAX_RECONNECT_MS)
      // Jitter stops every open tab from reconnecting in lockstep.
      const delay = backoff * (0.7 + Math.random() * 0.6)
      retryTimerRef.current = window.setTimeout(connect, delay)
    }
  }, [pushSnapshot])

  // --- initial paint: REST first, socket second --------------------------
  useEffect(() => {
    activeRef.current = true

    const seed = async () => {
      try {
        const response = await fetch('/api/telemetry')
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        const initial = await response.json()
        if (activeRef.current) pushSnapshot(initial)
      } catch (caught) {
        if (activeRef.current) setError(`Could not reach the dashboard API: ${caught.message}`)
      }
    }

    const seedChart = async () => {
      try {
        const response = await fetch('/api/history?minutes=40')
        if (!response.ok) return // history is optional; the chart fills in live
        const points = await response.json()
        if (!activeRef.current || points.length === 0) return

        // Pivot the flat (backend, queue, time) log into one row per timestamp.
        const byTimestamp = new Map()
        for (const point of points) {
          const key = new Date(point.recorded_at).getTime()
          if (!byTimestamp.has(key)) byTimestamp.set(key, { t: key })
          byTimestamp.get(key)[point.backend] = point.queue_length
        }
        const rows = [...byTimestamp.values()].sort((a, b) => a.t - b.t)
        setChartData((live) => {
          const earliestLive = live.length ? live[0].t : Infinity
          const historical = rows.filter((row) => row.t < earliestLive)
          return [...historical, ...live].slice(-MAX_CHART_POINTS)
        })
      } catch {
        // Chart seeding is best-effort by design.
      }
    }

    seed().then(seedChart)
    connect()

    return () => {
      activeRef.current = false
      if (retryTimerRef.current) window.clearTimeout(retryTimerRef.current)
      const socket = socketRef.current
      if (socket) {
        // Detach handlers before closing so onclose cannot schedule a reconnect
        // for a component that no longer exists.
        socket.onclose = null
        socket.onerror = null
        socket.onmessage = null

        // Closing a socket that is still handshaking logs a browser warning and
        // leaves the server with a half-open connection. Under React StrictMode
        // this happens on every mount in development. Wait for the handshake to
        // finish, then close cleanly.
        if (socket.readyState === WebSocket.CONNECTING) {
          socket.onopen = () => socket.close()
        } else if (socket.readyState === WebSocket.OPEN) {
          socket.close()
        }
      }
    }
  }, [connect, pushSnapshot])

  return {
    snapshot,
    chartData,
    connectionState,
    error,
    isLive: snapshot?.source === 'live',
    isLoading: snapshot === null && error === null,
  }
}
