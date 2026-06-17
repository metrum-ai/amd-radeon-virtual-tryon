// Copyright Advanced Micro Devices, Inc.
// 
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useState } from 'react'
import TopBar from './components/TopBar'
import TryOnPanel from './components/TryOnPanel'
import AgentChat from './components/AgentChat'
import StylingPanel from './components/StylingPanel'
import CatalogBrowser from './components/CatalogBrowser'
import MetricsSidebar from './components/MetricsSidebar'
import {
  getCustomerVideo,
  runFashionPipeline,
} from './lib/fashionPipeline'
import type { CustomerProfile, Garment, PipelineCompletion, PipelinePreview, RecentTryOn } from './types/vto'

export default function App() {
  const [selectedGarments, setSelectedGarments] = useState<Garment[]>([])
  const [activeCategory, setActiveCategory] = useState('all')
  const [catalogOpen, setCatalogOpen] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [agentRecommendations, setAgentRecommendations] = useState<Garment[]>([])
  const [sessionId, setSessionId] = useState<string>(() => (
    sessionStorage.getItem('vtoSessionId') ?? crypto.randomUUID()
  ))
  const [recentTryOns, setRecentTryOns] = useState<RecentTryOn[]>([])
  const [recentOpen, setRecentOpen] = useState(false)
  const [previewTryOn, setPreviewTryOn] = useState<RecentTryOn | null>(null)
  const [customerProfile, setCustomerProfile] = useState<CustomerProfile>('')
  const [pipelinePreviews, setPipelinePreviews] = useState<PipelinePreview[]>([])
  const [completedPipeline, setCompletedPipeline] = useState<PipelineCompletion | null>(null)
  const [selectedGenerationIndices, setSelectedGenerationIndices] = useState<number[]>([])
  const [generationsOpen, setGenerationsOpen] = useState(false)
  const [generationsModalOpen, setGenerationsModalOpen] = useState(false)
  const sessionIdRef = useRef(sessionId)
  useEffect(() => { sessionIdRef.current = sessionId }, [sessionId])

  useEffect(() => {
    sessionStorage.setItem('vtoSessionId', sessionId)
  }, [sessionId])

  // Intentionally no session-restore effect here: recentTryOns should only
  // contain try-ons from the current page session so that a page refresh
  // always starts with an empty snapshots list.


  function selectGarment(g: Garment) {
    setSelectedGarments(prev => {
      // Toggle off if already selected.
      if (prev.some(s => s.id === g.id)) return prev.filter(s => s.id !== g.id)
      // One-piece always clears everything and becomes the only selection.
      if (g.overlayCategory === 'one-pieces') return [g]
      // If any one-piece is currently selected, replace it.
      if (prev.some(s => s.overlayCategory === 'one-pieces')) return [g]
      // Under the global max — just add.
      if (prev.length < 2) return [...prev, g]
      // At max (2): replace the garment of the same category if present,
      // otherwise replace the oldest garment (FIFO).
      const sameCatIndex = prev.findIndex(
        s => s.overlayCategory === g.overlayCategory
      )
      if (sameCatIndex !== -1) {
        const next = [...prev]
        next[sameCatIndex] = g
        return next
      }
      return [...prev.slice(1), g]
    })
    // Do not auto-switch the catalog category.  Jumping the browser to the
    // category of the just-selected garment forces the user to manually
    // navigate back when they want to mix a styling recommendation with a
    // catalogue item from a different category (e.g. top + bottom).
  }

  function handleAgentRecommendations(items: Garment[]) {
    if (items.length === 0) {
      setAgentRecommendations([])
      return
    }
    const nextIds = new Set(items.map(item => item.id))
    setAgentRecommendations(prev => [
      ...items,
      ...prev.filter(item => !nextIds.has(item.id)),
    ])
  }

  useEffect(() => {
    if (selectedGenerationIndices.length > 2) {
      setGenerationsModalOpen(true)
    }
  }, [selectedGenerationIndices.length])

  function toggleGeneration(idx: number) {
    if (idx === -1) { setSelectedGenerationIndices([]); return }
    setSelectedGenerationIndices(prev => {
      if (prev.includes(idx)) return prev.filter(i => i !== idx)
      return [...prev, idx]
    })
  }

  function handleNewSession() {
    const oldId = sessionId
    fetch(`/api/v1/vto/runtime/${oldId}`, { method: 'DELETE' }).catch(() => {})
    const nextId = crypto.randomUUID()
    sessionStorage.setItem('vtoSessionId', nextId)
    setSessionId(nextId)
      setAgentRecommendations([])
      setSelectedGarments([])
      setPipelinePreviews([])
      setCompletedPipeline(null)
      setRecentTryOns([])
      setPreviewTryOn(null)
      setSelectedGenerationIndices([])
      setGenerationsOpen(false)
      setGenerationsModalOpen(false)
  }

  function handleCustomerProfileChange(profile: CustomerProfile) {
    setCustomerProfile(profile)
    setPipelinePreviews([])
    setCompletedPipeline(null)
    setSelectedGenerationIndices([])
    setGenerationsOpen(false)
    setGenerationsModalOpen(false)
  }

  async function handleRunPipeline() {
    const garments = selectedGarments
    if (garments.length === 0) return

    const isOnePiece = garments.some(g => g.overlayCategory === 'one-pieces')
    const allSameCategory =
      garments.length > 1 &&
      garments.every(g => g.overlayCategory === garments[0].overlayCategory)

    // Same-category non-one-piece selections cannot be overlaid in a single
    // generation, so we run one pipeline job per garment. Mixed combos
    // (top + bottom) or one-piece still run as a single combined job.
    const garmentSets =
      allSameCategory && !isOnePiece
        ? garments.map(g => [g])
        : [garments]

    const startedSessionId = sessionId
    const startIndex = pipelinePreviews.length
    const originalVideoUrl = getCustomerVideo(customerProfile)?.preview

    // Pre-allocate preview slots so each parallel / sequential run has a
    // stable index for its own status updates.
    setPipelinePreviews(prev => [
      ...prev,
      ...garmentSets.map(() => ({
        status: 'uploading' as const,
        originalVideoUrl,
        message: 'Preparing catalog images...',
      })),
    ])

    let anySuccess = false

    for (let i = 0; i < garmentSets.length; i++) {
      const jobIndex = startIndex + i
      const runGarments = garmentSets[i]
      const feedbackGarment = runGarments[0]

      const update = (patch: Partial<PipelinePreview>) =>
        setPipelinePreviews(prev =>
          prev.map((p, idx) => (idx === jobIndex ? { ...p, ...patch } : p))
        )

      try {
        const result = await runFashionPipeline({
          sessionId,
          customerProfile,
          garments: runGarments,
          onStatus: (status, message, stage) =>
            update({
              status,
              message: message ?? statusMessage(status),
              stage,
            }),
        })
        // If the user started a new session while this pipeline was running,
        // discard remaining results.
        if (sessionIdRef.current !== startedSessionId) return
        update({
          status: 'done',
          jobId: result.jobId,
          originalVideoUrl: result.originalVideoUrl,
          outputVideoUrl: result.outputVideoUrl,
          message: 'Pipeline output ready',
        })
        if (feedbackGarment) {
          const triedAt = new Date().toLocaleTimeString([], {
            hour: '2-digit',
            minute: '2-digit',
          })
          setRecentTryOns(prev => [
            {
              garment: feedbackGarment,
              triedAt,
              jobId: result.jobId,
              keyframeUrl: `/pipeline/outputs/${result.jobId}/keyframes/preview`,
              outputVideoUrl: result.outputVideoUrl,
            },
            ...prev.filter(item => item.jobId !== result.jobId),
          ])
          setCompletedPipeline({
            jobId: result.jobId,
            garment: feedbackGarment,
            outputVideoUrl: result.outputVideoUrl,
          })
          anySuccess = true
        }
      } catch (error) {
        update({
          status: 'failed',
          originalVideoUrl,
          message:
            error instanceof Error ? error.message : 'Pipeline failed',
        })
      }
    }

    if (anySuccess) {
      setSelectedGarments([])
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', width: '100vw', overflow: 'hidden', background: 'var(--bg-base)' }}>
      <TopBar />

      <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
        {/* Main content */}
        <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minWidth: 0 }}>
          {/* Panels row */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'minmax(360px, 4fr) minmax(520px, 6fr)',
              flex: 1,
              gap: 12,
              minHeight: 0,
              padding: 12,
            }}
          >
            <AgentChat
              onRecommendations={handleAgentRecommendations}
              onNewSession={handleNewSession}
              sessionId={sessionId}
              customerProfile={customerProfile}
              onCustomerProfileChange={handleCustomerProfileChange}
              completedPipeline={completedPipeline}
              selectedGarment={selectedGarments[0] ?? null}
            />
            <div style={{ position: 'relative', display: 'flex', flexDirection: 'column', minWidth: 0, minHeight: 0, gap: 12 }}>
              {/* Overlay button row */}
              <div style={{ position: 'absolute', top: 10, right: 12, zIndex: 20, display: 'flex', gap: 8 }}>
                {(() => {
                  const doneCount = pipelinePreviews.filter(p => p.status === 'done' && p.outputVideoUrl).length
                  const selCount = selectedGenerationIndices.length
                  const hasSelections = selCount > 0
                  return (
                    <button
                      onClick={() => {
                        if (hasSelections && selCount > 2) { setGenerationsModalOpen(true) }
                        else { setGenerationsOpen(v => !v); setRecentOpen(false) }
                      }}
                      style={{
                        border: `1px solid ${hasSelections ? 'rgba(242,101,34,0.6)' : 'rgba(237,28,36,0.35)'}`,
                        background: hasSelections ? 'rgba(242,101,34,0.14)' : 'rgba(14,14,17,0.86)',
                        backdropFilter: 'blur(10px)',
                        color: hasSelections ? 'var(--amd-orange)' : 'var(--text-secondary)',
                        borderRadius: 8,
                        padding: '5px 10px',
                        fontSize: 11,
                        fontWeight: 700,
                        boxShadow: '0 8px 24px rgba(0,0,0,0.35)',
                        cursor: 'pointer',
                      }}
                    >
                      {hasSelections ? `Recent Try-Ons (${selCount})` : `Recent Try-Ons (${doneCount})`}
                    </button>
                  )
                })()}
                <button
                  onClick={() => { setRecentOpen(v => !v); setGenerationsOpen(false) }}
                  style={{
                    border: '1px solid rgba(237,28,36,0.35)',
                    background: 'rgba(14,14,17,0.86)',
                    backdropFilter: 'blur(10px)',
                    color: 'var(--text-secondary)',
                    borderRadius: 8,
                    padding: '5px 10px',
                    fontSize: 11,
                    fontWeight: 700,
                    boxShadow: '0 8px 24px rgba(0,0,0,0.35)',
                    cursor: 'pointer',
                  }}
                >
                  Recent Snapshots ({recentTryOns.length})
                </button>
              </div>

              {generationsOpen && (
                <GenerationsPopover
                  pipelinePreviews={pipelinePreviews}
                  selectedIndices={selectedGenerationIndices}
                  onToggle={toggleGeneration}
                  onClose={() => setGenerationsOpen(false)}
                />
              )}
              {recentOpen && (
                <RecentTryOnsPopover
                  items={recentTryOns}
                  onClose={() => setRecentOpen(false)}
                  onSelect={item => {
                    setPreviewTryOn(item)
                    setRecentOpen(false)
                  }}
                />
              )}
              {previewTryOn && (
                <TryOnPreviewModal
                  item={previewTryOn}
                  onClose={() => setPreviewTryOn(null)}
                />
              )}
              {generationsModalOpen && (
                <GenerationsCarouselModal
                  pipelinePreviews={pipelinePreviews}
                  selectedIndices={selectedGenerationIndices}
                  onClose={() => setGenerationsModalOpen(false)}
                />
              )}

              <div style={{ flex: 1, minHeight: 0, display: 'flex' }}>
                <TryOnPanel
                  customerProfile={customerProfile}
                  pipelinePreviews={pipelinePreviews}
                  selectedGenerationIndices={selectedGenerationIndices}
                />
              </div>
              <StylingPanel
                selectedGarments={selectedGarments}
                agentRecommendations={agentRecommendations}
                onSelectGarment={selectGarment}
                onRunPipeline={handleRunPipeline}
                pipelineRunning={pipelinePreviews.some(p => ['uploading', 'queued', 'running'].includes(p.status))}
                sessionId={sessionId}
                customerProfile={customerProfile}
              />
            </div>
          </div>

          {/* Catalog */}
          <CatalogBrowser
            open={catalogOpen}
            onToggle={() => setCatalogOpen(v => !v)}
            activeCategory={activeCategory}
            onCategoryChange={setActiveCategory}
            selectedGarmentIds={selectedGarments.map(g => g.id)}
            onSelectGarment={selectGarment}
          />
        </div>

        {/* GPU metrics */}
        <MetricsSidebar
          open={sidebarOpen}
          onToggle={() => setSidebarOpen(v => !v)}
        />
      </div>
    </div>
  )
}

function statusMessage(status: PipelinePreview['status']): string {
  if (status === 'uploading') return 'Uploading selected garments...'
  if (status === 'queued') return 'Pipeline job queued...'
  if (status === 'running') return 'Generating try-on video...'
  if (status === 'done') return 'Pipeline output ready'
  if (status === 'failed') return 'Pipeline failed'
  return ''
}

function sessionJobToRecentTryOn(job: {
  job_id: string
  input_garments?: string[]
  updated_at?: number
}): RecentTryOn {
  const firstGarment = job.input_garments?.[0]
  const name = firstGarment
    ? `Virtual Try-On · ${firstGarment.split('/').pop() ?? firstGarment}`
    : `Virtual Try-On · ${job.job_id}`
  return {
    garment: {
      id: `pipeline-${job.job_id}`,
      name,
      category: 'try-on',
      overlayCategory: 'tops',
      subcategory: 'Generated',
      brand: 'Pipeline',
      color: '',
      price: 0,
      gradient: 'linear-gradient(145deg, #1e1e26, #2a2a36)',
      image: `/pipeline/outputs/${job.job_id}/keyframes/preview`,
      tags: [],
    },
    triedAt: job.updated_at
      ? new Date(job.updated_at * 1000).toLocaleTimeString([], {
          hour: '2-digit',
          minute: '2-digit',
        })
      : 'Session try-on',
    jobId: job.job_id,
    keyframeUrl: `/pipeline/outputs/${job.job_id}/keyframes/preview`,
    outputVideoUrl: `/pipeline/outputs/${job.job_id}/slideshow.mp4`,
  }
}

function TryOnPreviewModal({
  item,
  onClose,
}: {
  item: RecentTryOn
  onClose: () => void
}) {
  const imageUrl = item.keyframeUrl ?? item.garment.image
  const downloadName = item.jobId
    ? `${item.jobId}-keyframe.png`
    : `${item.garment.name.replace(/[^a-z0-9]+/gi, '-').toLowerCase()}.png`

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Try-on keyframe preview"
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1200,
        display: 'grid',
        placeItems: 'center',
        padding: 24,
        background: 'rgba(0,0,0,0.76)',
        backdropFilter: 'blur(8px)',
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          width: 'min(760px, 92vw)',
          maxHeight: '88vh',
          overflow: 'hidden',
          border: '1px solid var(--border-light)',
          borderRadius: 'var(--radius-lg)',
          background: 'var(--card)',
          boxShadow: '0 28px 80px rgba(0,0,0,0.68)',
        }}
      >
        <div style={{ padding: '10px 12px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ color: 'var(--text-primary)', fontSize: 13, fontWeight: 800, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {item.garment.name}
            </div>
            <div style={{ color: 'var(--text-dim)', fontSize: 10, marginTop: 2 }}>
              {item.jobId ? `Job ${item.jobId}` : 'Catalog item'} · {item.triedAt}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
            <a
              href={imageUrl}
              download={downloadName}
              style={{
                border: '1px solid rgba(242,101,34,0.55)',
                borderRadius: 8,
                background: 'var(--gradient-amd)',
                color: '#fff',
                fontSize: 11,
                fontWeight: 800,
                padding: '7px 10px',
                textDecoration: 'none',
              }}
            >
              Download
            </a>
            <button
              onClick={onClose}
              aria-label="Close preview"
              style={{
                border: '1px solid var(--border)',
                borderRadius: 8,
                background: 'var(--bg-elevated)',
                color: 'var(--text-secondary)',
                fontSize: 14,
                fontWeight: 800,
                padding: '5px 10px',
              }}
            >
              x
            </button>
          </div>
        </div>
        <div style={{ background: '#050507', padding: 14, display: 'grid', placeItems: 'center' }}>
          <img
            src={imageUrl}
            alt={`${item.garment.name} try-on keyframe`}
            style={{ maxWidth: '100%', maxHeight: '70vh', objectFit: 'contain', borderRadius: 8 }}
          />
        </div>
      </div>
    </div>
  )
}

function RecentTryOnsPopover({
  items,
  onClose,
  onSelect,
}: {
  items: RecentTryOn[]
  onClose: () => void
  onSelect: (item: RecentTryOn) => void
}) {
  return (
    <div
      style={{
        position: 'absolute',
        top: 46,
        right: 12,
        zIndex: 30,
        width: 280,
        maxWidth: 'calc(100% - 24px)',
        border: '1px solid var(--border-light)',
        borderRadius: 'var(--radius-lg)',
        background: 'rgba(18,18,21,0.96)',
        boxShadow: '0 24px 60px rgba(0,0,0,0.55)',
        overflow: 'hidden',
        animation: 'agentPopOut 0.2s ease both',
      }}
    >
      <div style={{ padding: '10px 12px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: 12, fontWeight: 800, color: 'var(--text-primary)' }}>Recent Snapshots</span>
        <button onClick={onClose} style={{ background: 'none', border: 0, color: 'var(--text-dim)', fontSize: 16 }}>x</button>
      </div>
      <div style={{ maxHeight: 400, overflowY: 'auto', padding: '6px 8px', display: 'flex', flexDirection: 'column', gap: 5 }}>
        {items.length === 0 && (
          <div style={{ padding: 14, border: '1px dashed var(--border)', borderRadius: 8, color: 'var(--text-dim)', fontSize: 12, textAlign: 'center' }}>
            No try-ons yet
          </div>
        )}
        {items.map(item => (
          <button
            key={item.jobId ?? item.garment.id}
            onClick={() => onSelect(item)}
            style={{
              display: 'flex', alignItems: 'center', gap: 10,
              width: '100%', padding: '6px 8px',
              border: '1px solid var(--border)', borderRadius: 8,
              background: 'var(--bg-elevated)', textAlign: 'left',
              cursor: 'pointer',
            }}
          >
            {/* Video / keyframe thumbnail */}
            <div style={{ width: 40, height: 56, borderRadius: 5, overflow: 'hidden', flexShrink: 0, background: '#08080a' }}>
              {item.outputVideoUrl ? (
                <video
                  autoPlay muted loop playsInline
                  src={item.outputVideoUrl}
                  style={{ width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'top center', display: 'block' }}
                />
              ) : (
                <img
                  src={item.keyframeUrl ?? item.garment.image}
                  alt=""
                  style={{ width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'top center', display: 'block' }}
                />
              )}
            </div>

            {/* Job ID + timestamp */}
            <div style={{ flex: 1, minWidth: 0 }}>
              <span style={{
                display: 'block', fontSize: 10, fontFamily: 'var(--font-mono)',
                color: 'var(--text-primary)', fontWeight: 600,
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {item.jobId ?? '—'}
              </span>
              <span style={{ display: 'block', fontSize: 9, color: 'var(--text-dim)', marginTop: 3 }}>
                {item.triedAt}
              </span>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}

function GenerationsCarouselModal({
  pipelinePreviews,
  selectedIndices,
  onClose,
}: {
  pipelinePreviews: PipelinePreview[]
  selectedIndices: number[]
  onClose: () => void
}) {
  const items = selectedIndices
    .map(i => ({ idx: i, preview: pipelinePreviews[i] }))
    .filter(item => item.preview?.outputVideoUrl)

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Generations carousel"
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 1200,
        background: 'rgba(0,0,0,0.88)', backdropFilter: 'blur(14px)',
        display: 'flex', flexDirection: 'column',
      }}
    >
      <div onClick={e => e.stopPropagation()} style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        {/* Header */}
        <div style={{
          padding: '12px 20px', flexShrink: 0,
          borderBottom: '1px solid var(--border)',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          background: 'rgba(14,14,17,0.72)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{ width: 3, height: 16, background: 'var(--gradient-amd)', borderRadius: 2 }} />
            <span style={{ fontSize: 14, fontWeight: 800, color: 'var(--text-primary)', letterSpacing: '0.04em' }}>
              Generations Compare
            </span>
            <span style={{
              fontSize: 10, fontWeight: 700, color: 'var(--amd-orange)',
              background: 'rgba(242,101,34,0.12)', border: '1px solid rgba(242,101,34,0.3)',
              borderRadius: 999, padding: '2px 8px',
            }}>
              {items.length} generations
            </span>
          </div>
          <button
            onClick={onClose}
            style={{
              border: '1px solid var(--border-light)', borderRadius: 8,
              background: 'var(--bg-elevated)', color: 'var(--text-secondary)',
              fontSize: 12, fontWeight: 700, padding: '6px 14px', cursor: 'pointer',
            }}
          >
            Close
          </button>
        </div>

        {/* Horizontal carousel */}
        <div style={{
          flex: 1, minHeight: 0,
          display: 'flex', overflowX: 'auto', overflowY: 'hidden',
          gap: 10, padding: '14px 16px',
          scrollSnapType: 'x mandatory',
        }}>
          {items.map(item => (
            <CarouselVideoCard
              key={item.idx}
              label={`Generation ${item.idx + 1}`}
              src={item.preview.outputVideoUrl!}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

function CarouselVideoCard({ label, src }: { label: string; src: string }) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [playing, setPlaying] = useState(false)
  const [muted, setMuted] = useState(true)
  const [volume, setVolume] = useState(0.5)

  // Keep DOM muted property in sync with state.
  useEffect(() => {
    const v = videoRef.current
    if (v) v.muted = muted
  }, [muted])

  function togglePlay() {
    const v = videoRef.current
    if (!v) return
    if (v.paused) void v.play()
    else v.pause()
  }

  function toggleMute() {
    const v = videoRef.current
    if (!v) return
    v.muted = !v.muted
    setMuted(v.muted)
  }

  function handleVolume(val: number) {
    const v = videoRef.current
    if (!v) return
    v.volume = val
    v.muted = val === 0
    setVolume(val)
    setMuted(val === 0)
  }

  return (
    <div style={{
      flex: '0 0 auto',
      width: 'min(30vw, 340px)', minWidth: 220,
      height: '100%',
      position: 'relative',
      borderRadius: 12, overflow: 'hidden',
      background: 'var(--bg-elevated)',
      border: '1px solid var(--border-light)',
      scrollSnapAlign: 'start',
    }}>
      {/* Blurred background fill */}
      <video
        muted playsInline src={src}
        ref={node => {
          if (node && videoRef.current) {
            node.currentTime = videoRef.current.currentTime
            if (!videoRef.current.paused) void node.play()
          }
        }}
        style={{
          position: 'absolute', inset: 0, width: '100%', height: '100%',
          objectFit: 'cover', filter: 'blur(18px) brightness(0.35)',
          transform: 'scale(1.08)', pointerEvents: 'none',
        }}
      />
      {/* Main video */}
      <video
        ref={videoRef}
        muted
        playsInline
        loop
        src={src}
        style={{ position: 'relative', width: '100%', height: '100%', objectFit: 'contain', display: 'block' }}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
      />
      {/* Label */}
      <span style={{
        position: 'absolute', top: 10, left: 10,
        padding: '4px 8px', borderRadius: 999,
        background: 'rgba(0,0,0,0.62)', color: '#fff',
        fontSize: 10, fontWeight: 800, letterSpacing: '0.04em', textTransform: 'uppercase',
      }}>
        {label}
      </span>
      {/* Controls */}
      <div style={{
        position: 'absolute', bottom: 10, left: '50%', transform: 'translateX(-50%)',
        display: 'flex', gap: 6, alignItems: 'center',
        background: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(8px)',
        borderRadius: 999, padding: '5px 14px',
      }}>
        <button
          onClick={togglePlay}
          style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#fff', padding: '2px 4px', display: 'flex', alignItems: 'center' }}
        >
          {playing ? (
            <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
              <rect x="6" y="4" width="4" height="16" rx="1" />
              <rect x="14" y="4" width="4" height="16" rx="1" />
            </svg>
          ) : (
            <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
              <polygon points="5,3 19,12 5,21" />
            </svg>
          )}
        </button>
        <button
          onClick={toggleMute}
          style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#fff', padding: '2px 4px', display: 'flex', alignItems: 'center' }}
        >
          {muted ? (
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
              <line x1="23" y1="9" x2="17" y2="15" /><line x1="17" y1="9" x2="23" y2="15" />
            </svg>
          ) : (
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
              <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
            </svg>
          )}
        </button>
        <input
          type="range" min={0} max={1} step={0.02}
          value={muted ? 0 : volume}
          onChange={e => handleVolume(parseFloat(e.target.value))}
          style={{ width: 60, height: 3, cursor: 'pointer', accentColor: '#fff', background: 'transparent', outline: 'none', border: 'none' }}
        />
      </div>
    </div>
  )
}

function GenerationsPopover({
  pipelinePreviews,
  selectedIndices,
  onToggle,
  onClose,
}: {
  pipelinePreviews: PipelinePreview[]
  selectedIndices: number[]
  onToggle: (idx: number) => void
  onClose: () => void
}) {
  const doneItems = pipelinePreviews
    .map((p, idx) => ({ ...p, idx }))
    .filter(p => p.status === 'done' && p.outputVideoUrl)

  return (
    <div style={{
      position: 'absolute', top: 46, right: 12, zIndex: 30,
      width: 296, maxWidth: 'calc(100% - 24px)',
      border: '1px solid var(--border-light)',
      borderRadius: 'var(--radius-lg)',
      background: 'rgba(18,18,21,0.96)',
      boxShadow: '0 24px 60px rgba(0,0,0,0.55)',
      overflow: 'hidden',
      animation: 'agentPopOut 0.2s ease both',
    }}>
      <div style={{ padding: '10px 12px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: 12, fontWeight: 800, color: 'var(--text-primary)' }}>Recent Try-Ons</span>
        <button onClick={onClose} style={{ background: 'none', border: 0, color: 'var(--text-dim)', fontSize: 16, cursor: 'pointer' }}>×</button>
      </div>
      <div style={{ maxHeight: 400, overflowY: 'auto', padding: '6px 8px', display: 'flex', flexDirection: 'column', gap: 5 }}>
        {doneItems.length === 0 && (
          <div style={{ padding: 14, border: '1px dashed var(--border)', borderRadius: 8, color: 'var(--text-dim)', fontSize: 12, textAlign: 'center' }}>
            No completed generations yet
          </div>
        )}
        {doneItems.map(item => {
          const isSelected = selectedIndices.includes(item.idx)
          return (
            <button
              key={item.idx}
              onClick={() => onToggle(item.idx)}
              style={{
                display: 'flex', alignItems: 'center', gap: 10,
                width: '100%', padding: '6px 8px',
                border: `1px solid ${isSelected ? 'rgba(242,101,34,0.8)' : 'var(--border)'}`,
                borderRadius: 8,
                background: isSelected ? 'rgba(242,101,34,0.08)' : 'var(--bg-elevated)',
                textAlign: 'left',
                cursor: 'pointer',
                transition: 'border-color 0.15s, background 0.15s',
              }}
            >
              <div style={{ width: 40, height: 56, borderRadius: 5, overflow: 'hidden', flexShrink: 0, background: '#08080a', position: 'relative' }}>
                <video
                  muted playsInline preload="metadata"
                  src={item.outputVideoUrl}
                  onLoadedMetadata={e => { (e.target as HTMLVideoElement).currentTime = 1 }}
                  style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
                />
                {isSelected && (
                  <div style={{ position: 'absolute', top: 2, right: 2, width: 14, height: 14, borderRadius: 3, background: 'rgba(242,101,34,1)', display: 'grid', placeItems: 'center' }}>
                    <svg width="8" height="8" viewBox="0 0 12 12" fill="none" stroke="#fff" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="2 6 5 9 10 3" />
                    </svg>
                  </div>
                )}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <span style={{
                  display: 'block', fontSize: 10, fontFamily: 'var(--font-mono)',
                  color: 'var(--text-primary)', fontWeight: 600,
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                  Gen {item.idx + 1}{item.jobId ? ` · ${item.jobId.slice(-5)}` : ''}
                </span>
                <span style={{ display: 'block', fontSize: 9, color: 'var(--text-dim)', marginTop: 3 }}>
                  {isSelected ? 'Selected for compare' : 'Click to select'}
                </span>
              </div>
            </button>
          )
        })}
      </div>
      {selectedIndices.length > 0 && (
        <div style={{ padding: '8px 12px', borderTop: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>{selectedIndices.length} selected</span>
          <button
            onClick={() => { onToggle(-1) /* trigger clear via parent */ }}
            style={{ fontSize: 10, color: 'var(--amd-orange)', background: 'none', border: 'none', cursor: 'pointer', fontWeight: 700 }}
          >
            Clear
          </button>
        </div>
      )}
    </div>
  )
}
