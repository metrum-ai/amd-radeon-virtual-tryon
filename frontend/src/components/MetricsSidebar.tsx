// Copyright Advanced Micro Devices, Inc.
// 
// SPDX-License-Identifier: MIT

import { useCallback, useMemo } from 'react'
import { usePrometheusQuery, QUERIES, MB_TO_GB } from '../hooks/usePrometheusQuery'
import MetricGraph from './MetricGraph'
import '../styles/MetricsSidebar.css'

const SPECS = [
  ['64', 'Ray Accel'],
  ['128', 'AI Accel'],
  ['64', 'CUs'],
  ['32 GB', 'VRAM'],
] as const

const MB_TO_GB_OPT = { transform: MB_TO_GB }

interface Props {
  open: boolean
  onToggle: () => void
}

export default function MetricsSidebar({ open, onToggle }: Props) {
  const promCompute = usePrometheusQuery(QUERIES.gpuCompute)
  const promMemory = usePrometheusQuery(QUERIES.gpuMemory, MB_TO_GB_OPT)
  const promTemp = usePrometheusQuery(QUERIES.gpuTemp)
  const promPower = usePrometheusQuery(QUERIES.gpuPower)
  const promCpu = usePrometheusQuery(QUERIES.cpuUtil)
  const promSysMem = usePrometheusQuery(QUERIES.sysMem)
  const promTps = usePrometheusQuery(QUERIES.tokensPerSec, { staleAfterPolls: 3 })

  const queries = useMemo(
    () => [promCompute, promMemory, promTemp, promPower, promCpu, promSysMem],
    [promCompute, promMemory, promTemp, promPower, promCpu, promSysMem],
  )
  const connected = queries.some(q => q.connected === true)
  const checked = queries.every(q => q.connected !== null)
  const handleToggle = useCallback(() => onToggle(), [onToggle])

  return (
    <aside className={`metrics-panel${open ? '' : ' metrics-panel--collapsed'}`}>
      {!open && (
        <button className="metrics-panel__collapsed-inner" onClick={handleToggle}>
          <span className="metrics-panel__collapsed-label">
            AMD Radeon&trade; AI PRO R9700S
          </span>
          <span className="metrics-panel__collapsed-chevron">&lsaquo;</span>
        </button>
      )}

      {open && (
        <>
          <div className="metrics-panel__header">
            <button
              aria-label="Collapse metrics panel"
              className="metrics-panel__close-btn"
              onClick={handleToggle}
            >
              &rsaquo;
            </button>
          </div>

          <section className="metrics-hero-showcase">
            <div className="metrics-gpu-image">
              <img
                src="/assets/radeon_gpu.png"
                alt="AMD Radeon AI PRO R9700S"
              />
            </div>
            <h2 className="metrics-hero-name">
              AMD Radeon&trade; AI PRO R9700S
            </h2>
            <div className="spec-strip">
              {SPECS.map(([val, key]) => (
                <div key={key} className="spec-pill">
                  <span className="spec-pill__val">{val}</span>
                  <span className="spec-pill__key">{key}</span>
                </div>
              ))}
            </div>
          </section>

          <div className="metrics-graphs">
            {checked && !connected && (
              <div className="metrics-offline-banner">
                <span className="metrics-offline-banner__title">
                  Prometheus not reachable
                </span>
                <span className="metrics-offline-banner__sub">
                  Waiting for live Prometheus data
                </span>
              </div>
            )}

            <MetricGraph label="LLM Tokens/Sec" series={promTps.series} timestamps={promTps.timestamps} unit=" t/s" color="#38BDF8" maxVal={50} />
            <MetricGraph label="GPU Compute" series={promCompute.series} timestamps={promCompute.timestamps} unit="%" color="#ED1C24" maxVal={100} />
            <MetricGraph label="GPU Memory" series={promMemory.series} timestamps={promMemory.timestamps} unit="GB" color="#FACC15" maxVal={32} />
            <MetricGraph label="GPU Temperature" series={promTemp.series} timestamps={promTemp.timestamps} unit="°C" color="#22C55E" maxVal={100} />
            <MetricGraph label="GPU Power" series={promPower.series} timestamps={promPower.timestamps} unit="W" color="#F26522" maxVal={300} />
            <MetricGraph label="CPU Utilization" series={promCpu.series} timestamps={promCpu.timestamps} unit="%" color="#EC4899" maxVal={100} />
            <MetricGraph label="System Memory" series={promSysMem.series} timestamps={promSysMem.timestamps} unit="GB" color="#A855F7" maxVal={64} />
          </div>

          <div className="metrics-panel__footer">
            <span className={`metrics-live-dot${connected ? ' metrics-live-dot--on' : ''}`} />
            <span className={`metrics-live-label${connected ? ' metrics-live-label--on' : ''}`}>
              {connected ? 'Live' : checked ? 'Offline' : '...'}
            </span>
          </div>
        </>
      )}
    </aside>
  )
}
