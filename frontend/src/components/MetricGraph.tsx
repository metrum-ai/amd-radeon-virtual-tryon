// Copyright Advanced Micro Devices, Inc.
// 
// SPDX-License-Identifier: MIT

import { useState, useRef, useMemo, useCallback } from 'react'

export const HISTORY_LEN = 30

function fmtNum(v: number | null | undefined): string {
  if (v === null || v === undefined || isNaN(v)) return '--'
  return v % 1 ? v.toFixed(1) : String(v)
}

function fmtTime(ts: number | null): string {
  if (!ts) return '--:--:--'
  return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function fmtVal(v: number | null | undefined, unit: string): string {
  if (v === null || v === undefined) return '--'
  return (v % 1 ? v.toFixed(1) : String(v)) + unit
}

// Per SKILL.md: GPU 0 = full, GPU 1 = 65%, GPU 2 = 40%, GPU 3 = 25% opacity of primary color
function seriesColor(color: string, idx: number): string {
  if (idx === 0) return color
  const opacities = ['A6', '66', '40']  // 65%, 40%, 25% in hex
  const op = opacities[Math.min(idx - 1, opacities.length - 1)]
  return `${color}${op}`
}

interface Props {
  label: string
  series: Record<string, (number | null)[]>
  timestamps: (number | null)[]
  unit: string
  color: string
  maxVal?: number
}

export default function MetricGraph({ label, series, timestamps, unit, color, maxVal = 100 }: Props) {
  const [hover, setHover] = useState<{ index: number; pxX: number } | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  const keys = useMemo(() => Object.keys(series).sort(), [JSON.stringify(Object.keys(series))])
  const hasData = keys.length > 0 && keys.some(k => series[k]?.some(v => v !== null))
  const dataLen = useMemo(() => {
    return Math.max(
      timestamps.length,
      ...keys.map(k => series[k]?.length ?? 0),
      1,
    )
  }, [keys, series, timestamps.length])

  const graphH = 80, graphW = 200
  const padL = 28, padB = 16, padT = 4, padR = 10
  const plotW = graphW - padL - padR
  const plotH = graphH - padB - padT

  const ticks = useMemo(() => {
    return Array.from({ length: 5 }, (_, i) => Math.round((maxVal / 4) * i))
  }, [maxVal])

  const pointCoords = useMemo(() => {
    const coords: Record<string, { x: number; y: number; v: number | null }[]> = {}
    keys.forEach(k => {
      const history = series[k]
      if (!history) return
      coords[k] = history.map((v, i) => {
        const clamped = v === null ? 0 : Math.max(0, Math.min(maxVal, v))
        const denom = Math.max(history.length - 1, 1)
        return {
          x: padL + (i / denom) * plotW,
          y: padT + plotH - (clamped / maxVal) * plotH,
          v,
        }
      })
    })
    return coords
  }, [keys.join(','), series, maxVal, plotW, plotH])

  const buildPolyline = useCallback((k: string) => {
    return pointCoords[k]?.map(p => `${p.x},${p.y}`).join(' ') || ''
  }, [pointCoords])

  const handleMouseMove = useCallback((e: React.MouseEvent<SVGSVGElement>) => {
    const svg = svgRef.current
    const container = containerRef.current
    if (!svg || !container) return
    const rect = svg.getBoundingClientRect()
    const containerRect = container.getBoundingClientRect()
    const svgX = ((e.clientX - rect.left) / rect.width) * graphW
    const relX = svgX - padL
    if (relX < -4 || relX > plotW + 4) { setHover(null); return }
    const maxIndex = Math.max(dataLen - 1, 0)
    const idx = Math.round((relX / plotW) * maxIndex)
    const clampedIndex = Math.max(0, Math.min(maxIndex, idx))
    const pointX = padL + (clampedIndex / Math.max(maxIndex, 1)) * plotW
    const pxX = ((pointX / graphW) * rect.width) + rect.left - containerRect.left
    setHover({ index: clampedIndex, pxX })
  }, [dataLen, plotW])

  const isMulti = keys.length > 1
  const [headerHover, setHeaderHover] = useState(false)

  const avgCurrent = useMemo(() => {
    const vals = keys.map(k => series[k]?.[series[k].length - 1]).filter((v): v is number => v !== null && v !== undefined)
    if (!vals.length) return null
    return Math.round((vals.reduce((a, b) => a + b, 0) / vals.length) * 10) / 10
  }, [keys, series])

  const hoverX = hover !== null
    ? padL + (hover.index / Math.max(dataLen - 1, 1)) * plotW
    : null
  const tooltipLeft = hover !== null
    ? Math.max(
        4,
        Math.min(
          hover.pxX < 120 ? hover.pxX + 8 : hover.pxX - 116,
          (containerRef.current?.clientWidth ?? 220) - 120,
        ),
      )
    : 0

  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 6 }}>
        <div style={{ fontFamily: 'var(--font)', fontSize: 10, color: 'var(--text-secondary)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{label}</div>
        {hasData ? (
          <div style={{ position: 'relative' }}
            onMouseEnter={() => isMulti && setHeaderHover(true)}
            onMouseLeave={() => setHeaderHover(false)}
          >
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color, fontWeight: 700, cursor: isMulti ? 'default' : 'auto', whiteSpace: 'nowrap', display: 'flex', alignItems: 'baseline', gap: 2 }}>
              {fmtNum(avgCurrent)}<span style={{ fontFamily: 'var(--font)', fontSize: 9, color: 'var(--text-dim)', fontWeight: 400 }}>{unit}</span>
            </div>
            {isMulti && headerHover && (
              <div style={{
                position: 'absolute', right: 0, top: '100%', marginTop: 4,
                background: 'var(--bg-base)', border: '1px solid var(--border)', borderRadius: 6,
                padding: '6px 10px', zIndex: 20, minWidth: 100,
                boxShadow: '0 4px 12px rgba(0,0,0,0.5)',
              }}>
                {keys.map((k, i) => {
                  const lineColor = seriesColor(color, i)
                  const val = series[k]?.[series[k].length - 1]
                  return (
                    <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 5, marginBottom: i < keys.length - 1 ? 4 : 0 }}>
                      <div style={{ width: 6, height: 6, borderRadius: 3, background: lineColor, flexShrink: 0 }} />
                      <span style={{ fontSize: 9, color: 'var(--text-secondary)', fontWeight: 500 }}>GPU {k}</span>
                      <span style={{ fontSize: 10, color: lineColor, fontWeight: 700, marginLeft: 'auto' }}>
                        {fmtNum(val)}<span style={{ fontSize: 8, color: 'var(--text-dim)', fontWeight: 400, marginLeft: 1 }}>{unit}</span>
                      </span>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        ) : (
          <span style={{ fontSize: 11, color: 'var(--text-dim)' }}>--</span>
        )}
      </div>
      <div ref={containerRef} style={{
        background: 'var(--elevated)', borderRadius: 4,
        border: '1px solid var(--border)', padding: '6px 6px 2px 2px',
        position: 'relative',
      }}>
        <svg
          ref={svgRef}
          width={graphW} height={graphH}
          style={{ display: 'block', width: '100%', height: 'auto', cursor: hasData ? 'crosshair' : 'default' }}
          viewBox={`0 0 ${graphW} ${graphH}`}
          onMouseMove={hasData ? handleMouseMove : undefined}
          onMouseLeave={() => setHover(null)}
        >
          {ticks.map((tick, i) => {
            const y = padT + plotH - (tick / maxVal) * plotH
            return (
              <g key={i}>
                <line x1={padL} y1={y} x2={padL + plotW} y2={y} stroke="var(--border)" strokeWidth="0.5" />
                <text x={padL - 4} y={y + 3} textAnchor="end" fill="var(--text-dim)" fontSize="7" fontFamily="Inter, sans-serif">{tick}</text>
              </g>
            )
          })}
          {['-30s', '-15s', 'now'].map((lbl, i) => {
            const anchor = i === 0 ? 'start' : i === 2 ? 'end' : 'middle'
            return (
              <text key={i} x={padL + (i / 2) * plotW} y={graphH - 2} textAnchor={anchor} fill="var(--text-dim)" fontSize="7" fontFamily="Inter, sans-serif">{lbl}</text>
            )
          })}

          {keys.map((k, idx) => {
            const lineColor = seriesColor(color, idx)
            const pts = buildPolyline(k)
            if (!pts) return null
            const coords = pointCoords[k]
            const fillPts = `${padL},${padT + plotH} ${pts} ${padL + plotW},${padT + plotH}`
            const isHero = idx === 0
            return (
              <g key={k}>
                {isHero && <polygon points={fillPts} fill={`${color}12`} />}
                <polyline points={pts} fill="none" stroke={lineColor} strokeWidth={isHero ? 1.5 : 1}
                  vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round" />
                {coords.map((p, i) => p.v !== null && (
                  <circle key={i} cx={p.x} cy={p.y}
                    r={hover?.index === i ? 3.5 : 1.8}
                    fill={lineColor} opacity={hover?.index === i ? 1 : 0.5} />
                ))}
              </g>
            )
          })}

          {hover !== null && hoverX !== null && (
            <line x1={hoverX} y1={padT} x2={hoverX} y2={padT + plotH} stroke="var(--text-dim)" strokeWidth="0.5" strokeDasharray="2,2" />
          )}
        </svg>

        {hover !== null && hasData && (
          <div style={{
            position: 'absolute', left: tooltipLeft, top: 4,
            background: 'var(--bg-base)', border: '1px solid var(--border)', borderRadius: 6,
            padding: '5px 8px', pointerEvents: 'none', zIndex: 10, minWidth: 80,
            boxShadow: '0 4px 12px rgba(0,0,0,0.5)',
          }}>
            <div style={{ fontSize: 8, color: 'var(--text-dim)', marginBottom: 3, fontFamily: 'Inter, sans-serif', letterSpacing: '0.04em' }}>
              {fmtTime(timestamps?.[hover.index])}
            </div>
            {keys.map((k, idx) => {
              const lineColor = seriesColor(color, idx)
              const val = series[k]?.[hover.index]
              return (
                <div key={k} style={{ fontSize: 10, fontFamily: 'Inter, sans-serif', fontWeight: 600, color: lineColor, display: 'flex', alignItems: 'center', gap: 4 }}>
                  {isMulti && <span style={{ width: 6, height: 6, borderRadius: 3, background: lineColor, display: 'inline-block', flexShrink: 0 }} />}
                  {isMulti && <span style={{ fontSize: 8, color: 'var(--text-secondary)', fontWeight: 500 }}>GPU {k}</span>}
                  <span>{fmtVal(val, unit)}</span>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {isMulti && hasData && (
        <div style={{ display: 'flex', gap: 10, marginTop: 4 }}>
          {keys.map((k, i) => (
            <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 4, minWidth: 52 }}>
              <div style={{ width: 6, height: 6, borderRadius: 3, background: seriesColor(color, i) }} />
              <span style={{ fontSize: 8, color: 'var(--text-secondary)' }}>GPU {k}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
