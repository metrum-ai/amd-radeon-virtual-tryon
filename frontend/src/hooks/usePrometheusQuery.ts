// Copyright Advanced Micro Devices, Inc.
//
// SPDX-License-Identifier: MIT

import { useState, useEffect, useRef, useCallback } from 'react'

// Hardcoded path; nginx proxies /prometheus → Prometheus in both dev and prod.
const PROM_BASE = '/prometheus'
export const HISTORY_LEN = 20
const POLL_INTERVAL = 5000

export interface PrometheusResult {
  series: Record<string, (number | null)[]>
  timestamps: (number | null)[]
  connected: boolean | null
}

interface Options {
  interval?: number
  transform?: ((v: number) => number) | null
  // Push null after this many consecutive polls returning the same value.
  // Lets episodic metrics (e.g. tokens/sec) drop to "--" when idle rather
  // than holding the last inference's value forever.
  staleAfterPolls?: number
}

const EMPTY_TIMESTAMPS: (number | null)[] = Array(HISTORY_LEN).fill(null)

export function usePrometheusQuery(
  query: string,
  { interval = POLL_INTERVAL, transform = null, staleAfterPolls }: Options = {},
): PrometheusResult {
  const [series, setSeries] = useState<Record<string, (number | null)[]>>({})
  const [timestamps, setTimestamps] = useState<(number | null)[]>(() => EMPTY_TIMESTAMPS)
  const [connected, setConnected] = useState<boolean | null>(null)

  const queryRef = useRef(query)
  queryRef.current = query
  const connectedRef = useRef(connected)
  connectedRef.current = connected
  const transformRef = useRef(transform)
  transformRef.current = transform
  const staleAfterPollsRef = useRef(staleAfterPolls)
  staleAfterPollsRef.current = staleAfterPolls

  // Per-key staleness tracking: last seen value and consecutive-unchanged count.
  const lastValRef = useRef<Record<string, number>>({})
  const staleCountRef = useRef<Record<string, number>>({})

  const poll = useCallback(async () => {
    try {
      const url = `${PROM_BASE}/api/v1/query?query=` + encodeURIComponent(queryRef.current)
      const res = await fetch(url)
      if (!res.ok) throw new Error(res.statusText)
      const json = await res.json()

      if (json.status === 'success' && json.data?.result?.length > 0) {
        setConnected(true)
        const now = Date.now()
        const xform = transformRef.current
        const staleAfter = staleAfterPollsRef.current

        setSeries(prev => {
          const next = { ...prev }
          let changed = false
          for (const r of json.data.result) {
            const key = (r.metric as Record<string, string>).gpu_id ?? 'all'
            let val = parseFloat(r.value[1])
            if (xform) val = xform(val)
            if (isNaN(val)) continue
            const rounded = Math.round(val * 10) / 10
            const arr = next[key] ?? EMPTY_TIMESTAMPS

            if (staleAfter != null) {
              if (lastValRef.current[key] === rounded) {
                staleCountRef.current[key] = (staleCountRef.current[key] ?? 0) + 1
              } else {
                staleCountRef.current[key] = 0
                lastValRef.current[key] = rounded
              }
              // Value unchanged for too long → treat as idle, push null
              if (staleCountRef.current[key] >= staleAfter) {
                next[key] = [...arr.slice(1), null]
                changed = true
                continue
              }
            }

            next[key] = [...arr.slice(1), rounded]
            changed = true
          }
          return changed ? next : prev
        })
        setTimestamps(prev => [...prev.slice(1), now])
      }
    } catch {
      if (connectedRef.current === null) setConnected(false)
    }
  }, [])

  useEffect(() => {
    let active = true
    const wrappedPoll = () => { if (active) poll() }
    wrappedPoll()
    const id = setInterval(wrappedPoll, interval)
    return () => { active = false; clearInterval(id) }
  }, [poll, interval])

  return { series, timestamps, connected }
}

export const QUERIES: Record<string, string> = {
  gpuCompute:   'avg by (gpu_id) (avg_over_time(gpu_gfx_activity[10s]))',
  gpuMemory:    'avg by (gpu_id) (avg_over_time(gpu_used_vram[10s]))',
  gpuTemp:      'avg by (gpu_id) (avg_over_time(gpu_edge_temperature[10s]))',
  gpuPower:     'avg by (gpu_id) (avg_over_time(gpu_average_package_power[10s]))',
  cpuUtil:      '100 - (avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)',
  sysMem:       '(avg_over_time(node_memory_MemTotal_bytes[10s]) - avg_over_time(node_memory_MemAvailable_bytes[10s])) / 1024 / 1024 / 1024',
  tokensPerSec: 'rate(ollama_generated_tokens_total[30s])',
}

export const MB_TO_GB = (v: number): number => v / 1024
