// Copyright Advanced Micro Devices, Inc.
//
// SPDX-License-Identifier: MIT

import { useState, useEffect, useRef, useCallback, Fragment } from 'react'
import type { CustomerProfile, PipelinePreview } from '../types/vto'

interface Props {
  customerProfile: CustomerProfile
  pipelinePreviews: PipelinePreview[]
  selectedGenerationIndices?: number[]
  sessionId?: string
}

const DEMO_DOWNLOADS: Partial<Record<CustomerProfile, { href: string; filename: string; label: string }>> = {
  female: {
    href: '/demo-videos/Female-VTO.mp4',
    filename: 'Female-VTO.mp4',
    label: 'Download female customer demo video',
  },
  male: {
    href: '/demo-videos/Male-VTO.mp4',
    filename: 'Male-VTO.mp4',
    label: 'Download male customer demo video',
  },
  'female-senior': {
    href: '/demo-videos/Female-Senior-VTO.mp4',
    filename: 'Female-Senior-VTO.mp4',
    label: 'Download female senior customer demo video',
  },
  'male-senior': {
    href: '/demo-videos/Male-Senior-VTO.mp4',
    filename: 'Male-Senior-VTO.mp4',
    label: 'Download male senior customer demo video',
  },
}

export default function TryOnPanel({
  customerProfile,
  pipelinePreviews,
  selectedGenerationIndices = [],
  sessionId,
}: Props) {
  return (
    <div style={{ flex: 2, display: 'flex', flexDirection: 'column', background: 'var(--card)', overflow: 'hidden', minWidth: 0 }}>
      <PanelHeader>
        {/* paddingRight reserves room for the Recent Try-Ons/Snapshots buttons,
            which float on top of this header via position:absolute in App.tsx
            and don't otherwise participate in this row's layout. */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1, minWidth: 0, paddingRight: 320 }}>
          <AccentBar />
          <span style={{ ...labelStyle, flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>Virtual Try-On View</span>
        </div>
      </PanelHeader>

      <div style={{ flex: 1, position: 'relative', overflow: 'hidden', minHeight: 0 }}>
        <DemoComparisonFeed
          customerProfile={customerProfile}
          pipelinePreviews={pipelinePreviews}
          selectedGenerationIndices={selectedGenerationIndices}
        />
      </div>
    </div>
  )
}

function DownloadIconButton({
  download,
  disabledTitle = 'No video available to download',
}: {
  download?: { href: string; filename: string; label: string }
  disabledTitle?: string
}) {
  const commonStyle = {
    alignItems: 'center',
    backdropFilter: 'blur(10px)',
    background: download ? 'var(--gradient-amd)' : 'rgba(32,32,38,0.70)',
    border: '1px solid rgba(242,101,34,0.5)',
    borderRadius: 8,
    color: download ? '#fff' : 'var(--text-dim)',
    display: 'flex',
    height: 32,
    justifyContent: 'center',
    opacity: download ? 1 : 0.5,
    position: 'absolute' as const,
    right: 10,
    top: 10,
    textDecoration: 'none',
    width: 34,
    zIndex: 6,
  }

  const icon = (
    <svg
      aria-hidden="true"
      fill="none"
      height="16"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="2"
      viewBox="0 0 24 24"
      width="16"
    >
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="7 10 12 15 17 10" />
      <line x1="12" x2="12" y1="15" y2="3" />
    </svg>
  )

  if (!download) {
    return (
      <button
        aria-label={disabledTitle}
        disabled
        style={{ ...commonStyle, cursor: 'not-allowed' }}
        title={disabledTitle}
      >
        {icon}
      </button>
    )
  }

  return (
    <a
      aria-label={download.label}
      download={download.filename}
      href={download.href}
      style={{ ...commonStyle, cursor: 'pointer' }}
      title={download.label}
    >
      {icon}
    </a>
  )
}

function DemoComparisonFeed({
  customerProfile,
  pipelinePreviews,
  selectedGenerationIndices = [],
}: {
  customerProfile: CustomerProfile
  pipelinePreviews: PipelinePreview[]
  selectedGenerationIndices?: number[]
}) {
  const [viewIdx, setViewIdx] = useState(0)
  const demoDownload = DEMO_DOWNLOADS[customerProfile]

  useEffect(() => {
    if (pipelinePreviews.length > 0) {
      setViewIdx(pipelinePreviews.length - 1)
    }
  }, [pipelinePreviews.length])

  if (!demoDownload) {
    return <InactiveFeed customerProfile={customerProfile} />
  }

  const preview = pipelinePreviews[viewIdx] ?? { status: 'idle' as const }

  // Multi-pane comparison mode when generations are selected
  if (selectedGenerationIndices.length > 0) {
    const originalSrc = preview.originalVideoUrl ?? demoDownload.href
    const genPanes = selectedGenerationIndices
      .map((i, ordinal) => ({ preview: pipelinePreviews[i], ordinal, idx: i }))
      .filter(g => g.preview?.outputVideoUrl)
      .map(g => ({ label: `Generation ${g.idx + 1}`, src: g.preview.outputVideoUrl! }))

    if (genPanes.length > 0) {
      return (
        <ResizableRow panes={[
          { label: 'Original', src: originalSrc },
          ...genPanes,
        ]} />
      )
    }
  }
  const total = pipelinePreviews.length
  const showNav = total > 1

  // Split view: original on left, try-on / progress on right
  const jobId = preview.outputVideoUrl?.split('/outputs/')[1]?.split('/')[0]
  const outputDownload = preview.status === 'done' && preview.outputVideoUrl && jobId
    ? { href: preview.outputVideoUrl, filename: `tryon-${jobId}.mp4`, label: `Download try-on video (job ${jobId})` }
    : undefined

  return (
    <div style={{ width: '100%', height: '100%', display: 'flex', minHeight: 0, overflow: 'hidden' }}>
      <div style={{ flex: 1, position: 'relative', minWidth: 0, overflow: 'hidden', borderRight: '1px solid var(--border)' }}>
        <VideoPane label="Original Customer Video" src={preview.originalVideoUrl ?? demoDownload.href} />
      </div>
      <div style={{ flex: 1, position: 'relative', minWidth: 0, overflow: 'hidden' }}>
        {preview.outputVideoUrl ? (
          <VideoPane label={total > 1 ? `Try-On ${viewIdx + 1} of ${total}` : 'Virtual Try-On'} src={preview.outputVideoUrl} />
        ) : preview.status === 'idle' ? (
          <PipelinePlaceholder status="idle" message={undefined} />
        ) : (
          <PipelineFull status={preview.status} message={preview.message} stage={preview.stage} />
        )}
        {outputDownload && <DownloadIconButton download={outputDownload} />}
        {showNav && (
          <>
            <div style={{
              position: 'absolute', top: 10, left: 10,
              background: 'rgba(0,0,0,0.62)', backdropFilter: 'blur(6px)',
              border: '1px solid rgba(255,255,255,0.1)',
              color: '#fff', fontSize: 10, fontWeight: 800,
              padding: '3px 8px', borderRadius: 999,
              letterSpacing: '0.06em', pointerEvents: 'none', zIndex: 5,
            }}>
              {viewIdx + 1} / {total}
            </div>
            {viewIdx > 0 && (
              <button onClick={() => setViewIdx(i => i - 1)} title="Previous job"
                style={{ position: 'absolute', left: 8, top: '50%', transform: 'translateY(-50%)',
                  zIndex: 5, width: 32, height: 32, borderRadius: '50%', border: 'none',
                  background: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(6px)',
                  color: '#fff', fontSize: 18, lineHeight: 1,
                  display: 'grid', placeItems: 'center', cursor: 'pointer',
                  boxShadow: '0 2px 10px rgba(0,0,0,0.4)', transition: 'background 0.15s' }}
                onMouseEnter={e => (e.currentTarget.style.background = 'rgba(242,101,34,0.7)')}
                onMouseLeave={e => (e.currentTarget.style.background = 'rgba(0,0,0,0.55)')}>
                ‹
              </button>
            )}
            {viewIdx < total - 1 && (
              <button onClick={() => setViewIdx(i => i + 1)} title="Next job"
                style={{ position: 'absolute', right: 48, top: '50%', transform: 'translateY(-50%)',
                  zIndex: 5, width: 32, height: 32, borderRadius: '50%', border: 'none',
                  background: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(6px)',
                  color: '#fff', fontSize: 18, lineHeight: 1,
                  display: 'grid', placeItems: 'center', cursor: 'pointer',
                  boxShadow: '0 2px 10px rgba(0,0,0,0.4)', transition: 'background 0.15s' }}
                onMouseEnter={e => (e.currentTarget.style.background = 'rgba(242,101,34,0.7)')}
                onMouseLeave={e => (e.currentTarget.style.background = 'rgba(0,0,0,0.55)')}>
                ›
              </button>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function VideoPane({ label, src }: { label: string; src: string }) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const backdropRef = useRef<HTMLVideoElement>(null)
  const [playing, setPlaying] = useState(false)
  const [muted, setMuted] = useState(true)
  const [volume, setVolume] = useState(0.5)

  useEffect(() => {
    void videoRef.current?.play()
  }, [src])

  // Keep DOM muted property in sync with state (React doesn't reliably
  // update muted on re-renders for video elements).
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
    <div style={{ position: 'absolute', inset: 0, background: '#050507', overflow: 'hidden', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      {/* Blurred backdrop — fills black strips for portrait videos */}
      <video
        ref={backdropRef}
        muted
        playsInline
        loop
        src={src}
        style={{
          position: 'absolute', inset: 0, width: '100%', height: '100%',
          objectFit: 'cover', filter: 'blur(18px) brightness(0.35)',
          transform: 'scale(1.08)', pointerEvents: 'none',
        }}
      />
      <video
        ref={videoRef}
        muted
        loop
        playsInline
        src={src}
        style={{ position: 'relative', width: '100%', height: '100%', objectFit: 'contain', display: 'block' }}
        onPlay={() => {
          setPlaying(true)
          void backdropRef.current?.play()
        }}
        onPause={() => {
          setPlaying(false)
          backdropRef.current?.pause()
        }}
      />
      <span style={{ position: 'absolute', top: 10, left: 10, padding: '4px 8px', borderRadius: 999, background: 'rgba(0,0,0,0.62)', color: '#fff', fontSize: 10, fontWeight: 800, letterSpacing: '0.04em', textTransform: 'uppercase' }}>
        {label}
      </span>
      <div style={{
        position: 'absolute', bottom: 10, left: '50%', transform: 'translateX(-50%)',
        display: 'flex', gap: 6, alignItems: 'center',
        background: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(8px)',
        borderRadius: 999, padding: '5px 14px',
      }}>
        <button
          onClick={togglePlay}
          title={playing ? 'Pause' : 'Play'}
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
          title={muted ? 'Unmute' : 'Mute'}
          style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#fff', padding: '2px 4px', display: 'flex', alignItems: 'center' }}
        >
          {muted ? (
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
              <line x1="23" y1="9" x2="17" y2="15" />
              <line x1="17" y1="9" x2="23" y2="15" />
            </svg>
          ) : (
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
              <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
            </svg>
          )}
        </button>
        <input
          type="range"
          min={0}
          max={1}
          step={0.02}
          value={muted ? 0 : volume}
          onChange={e => handleVolume(parseFloat(e.target.value))}
          title="Volume"
          style={{
            width: 60, height: 3, cursor: 'pointer', accentColor: '#fff',
            background: 'transparent', outline: 'none', border: 'none',
          }}
        />
      </div>
    </div>
  )
}

const PIPELINE_STEPS: { key: PipelinePreview['status']; label: string; detail: string }[] = [
  { key: 'uploading', label: 'Upload garments', detail: 'Fetching catalog images' },
  { key: 'queued',    label: 'Job queued',       detail: 'Waiting for pipeline worker' },
  { key: 'running',  label: 'Generate try-on',   detail: 'Frame selection · FASHN inference · Slideshow' },
  { key: 'done',     label: 'Complete',           detail: 'Try-on video ready' },
]
const STEP_ORDER: PipelinePreview['status'][] = ['uploading', 'queued', 'running', 'done']

function PipelinePlaceholder({
  status,
  message,
}: {
  status: PipelinePreview['status']
  message?: string
}) {
  const [elapsed, setElapsed] = useState(0)
  const startRef = useRef<number | null>(null)

  // Track elapsed time only during 'running'
  const tick = useCallback(() => {
    setElapsed(Math.floor((Date.now() - (startRef.current ?? Date.now())) / 1000))
  }, [])

  useEffect(() => {
    if (status === 'running') {
      if (startRef.current === null) startRef.current = Date.now()
      const id = setInterval(tick, 1000)
      return () => clearInterval(id)
    }
    if (status !== 'uploading' && status !== 'queued') {
      startRef.current = null
      setElapsed(0)
    }
  }, [status, tick])

  if (status === 'idle') {
    return (
      <div style={{ height: '100%', background: 'var(--bg-elevated)', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 10, minWidth: 0, minHeight: 0, padding: 18, textAlign: 'center' }}>
        <div style={{ width: 48, height: 48, borderRadius: '50%', border: '1px solid rgba(242,101,34,0.35)', display: 'grid', placeItems: 'center', color: 'var(--amd-orange)' }}>
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
            <path d="M20.38 3.46 16 2a4 4 0 0 1-8 0L3.62 3.46a2 2 0 0 0-1.34 2.23l.58 3.57a1 1 0 0 0 .99.84H5v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V10h1.15a1 1 0 0 0 .99-.84l.58-3.57a2 2 0 0 0-1.34-2.23z" />
          </svg>
        </div>
        <span style={{ fontSize: 12, color: 'var(--text-secondary)', fontWeight: 700 }}>Virtual Try-On</span>
        <span style={{ fontSize: 10, color: 'var(--text-dim)', maxWidth: 220 }}>Choose catalog items and generate try-on</span>
      </div>
    )
  }

  const isFailed = status === 'failed'
  const currentIdx = STEP_ORDER.indexOf(status)

  return (
    <div style={{ height: '100%', background: 'var(--bg-elevated)', display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 0, minWidth: 0, minHeight: 0, padding: '20px 22px' }}>
      <div style={{ fontSize: 11, fontWeight: 800, color: 'var(--text-secondary)', letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: 16 }}>
        Pipeline Progress
      </div>

      {PIPELINE_STEPS.map((step, idx) => {
        const stepIdx = STEP_ORDER.indexOf(step.key)
        const isDone    = !isFailed && currentIdx > stepIdx
        const isActive  = !isFailed && currentIdx === stepIdx
        const isPending = isFailed ? stepIdx > currentIdx : currentIdx < stepIdx

        const dotColor = isFailed && isActive
          ? 'var(--sev-critical)'
          : isDone
            ? 'var(--amd-orange)'
            : isActive
              ? 'var(--amd-orange)'
              : 'var(--border)'

        const stepDetail = isActive && step.key === 'running' && elapsed > 0
          ? `${elapsed}s elapsed · ${message ?? 'Frame selection · Inference · Slideshow'}`
          : step.detail

        return (
          <div key={step.key} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, paddingBottom: idx < PIPELINE_STEPS.length - 1 ? 14 : 0, position: 'relative' }}>
            {/* Connector line */}
            {idx < PIPELINE_STEPS.length - 1 && (
              <div style={{
                position: 'absolute',
                left: 9,
                top: 20,
                width: 2,
                height: 'calc(100% - 6px)',
                background: isDone ? 'rgba(242,101,34,0.5)' : 'var(--border)',
                borderRadius: 1,
              }} />
            )}

            {/* Step dot / icon */}
            <div style={{
              width: 20,
              height: 20,
              borderRadius: '50%',
              flexShrink: 0,
              marginTop: 1,
              border: `2px solid ${dotColor}`,
              background: isDone ? 'rgba(242,101,34,0.15)' : 'transparent',
              display: 'grid',
              placeItems: 'center',
              position: 'relative',
              zIndex: 1,
            }}>
              {isDone && (
                <svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="var(--amd-orange)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="2 6 5 9 10 3" />
                </svg>
              )}
              {isActive && !isFailed && (
                <div style={{
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  background: 'var(--amd-orange)',
                  animation: 'livePulse 1s ease-in-out infinite',
                }} />
              )}
              {isFailed && isActive && (
                <svg width="8" height="8" viewBox="0 0 12 12" fill="none" stroke="var(--sev-critical)" strokeWidth="2.5" strokeLinecap="round">
                  <line x1="2" y1="2" x2="10" y2="10" /><line x1="10" y1="2" x2="2" y2="10" />
                </svg>
              )}
            </div>

            {/* Step text */}
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{
                fontSize: 11,
                fontWeight: 700,
                color: isPending ? 'var(--text-dim)' : isFailed && isActive ? 'var(--sev-critical)' : 'var(--text-primary)',
                lineHeight: 1.4,
              }}>
                {step.label}
              </div>
              <div style={{ fontSize: 9, color: 'var(--text-dim)', marginTop: 1, lineHeight: 1.4 }}>
                {isActive && isFailed
                  ? (message ?? 'Pipeline failed')
                  : isActive || isDone
                    ? stepDetail
                    : null}
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}

const GENERATE_SUBNODES = [
  { label: 'Selecting keyframes',     cumSecs: 12,  stages: ['extract_frames', 'select_keyframes', 'detect_garments'] },
  { label: 'FASHN inference',         cumSecs: 42,  stages: ['generate_tryon'] },
  { label: 'Generating styling tips', cumSecs: 52,  stages: ['generate_tips'] },
  { label: 'Assembling slideshow',    cumSecs: 60,  stages: ['assemble_slideshow'] },
  { label: 'Adding music',            cumSecs: 180, stages: ['generate_music', 'mux_music'] },
]

function PipelineFull({ status, message, stage }: { status: PipelinePreview['status']; message?: string; stage?: string }) {
  const [elapsed, setElapsed] = useState(0)
  const startRef = useRef<number | null>(null)
  const tick = useCallback(() => {
    setElapsed(Math.floor((Date.now() - (startRef.current ?? Date.now())) / 1000))
  }, [])

  useEffect(() => {
    if (status === 'running') {
      if (startRef.current === null) startRef.current = Date.now()
      const id = setInterval(tick, 1000)
      return () => clearInterval(id)
    }
    startRef.current = null
    setElapsed(0)
  }, [status, tick])

  const isFailed = status === 'failed'
  const currentIdx = STEP_ORDER.indexOf(status)

  const activeSubIdx = (() => {
    if (stage) {
      const byStage = GENERATE_SUBNODES.findIndex(s => s.stages.includes(stage))
      if (byStage !== -1) return byStage
    }
    const byTime = GENERATE_SUBNODES.findIndex(s => elapsed < s.cumSecs)
    return byTime === -1 ? GENERATE_SUBNODES.length - 1 : byTime
  })()
  const doneSubCount = activeSubIdx

  return (
    <div style={{
      position: 'absolute', inset: 0, zIndex: 2,
      display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
      padding: '24px 28px', background: 'var(--bg-elevated)',
    }}>
      {isFailed ? (
        <>
          <div style={{ width: 52, height: 52, borderRadius: '50%',
            background: 'rgba(237,28,36,0.1)', border: '2px solid rgba(237,28,36,0.45)',
            display: 'grid', placeItems: 'center', marginBottom: 16 }}>
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="var(--sev-critical)" strokeWidth="2.2" strokeLinecap="round">
              <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </div>
          <div style={{ fontSize: 16, fontWeight: 800, color: '#fff', marginBottom: 8 }}>Pipeline Failed</div>
          <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.4)', maxWidth: 260, lineHeight: 1.6, textAlign: 'center' }}>
            {message ?? 'An error occurred. Try again.'}
          </div>
        </>
      ) : (
        <>
          <div style={{ textAlign: 'center', marginBottom: 28 }}>
            <div style={{ width: 36, height: 2.5, background: 'var(--gradient-amd)', borderRadius: 2, margin: '0 auto 14px' }} />
            <div style={{ fontFamily: 'var(--font-condensed)', fontSize: 16, fontWeight: 800, color: '#fff',
              letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 5 }}>
              Generating Virtual Try-On
            </div>
            <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.35)', height: 14 }}>
              {status === 'running' && elapsed > 0 ? `${elapsed}s elapsed` : ' '}
            </div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', width: '100%' }}>
            {PIPELINE_STEPS.map((step, idx) => {
              const stepIdx  = STEP_ORDER.indexOf(step.key)
              const isDone   = currentIdx > stepIdx
              const isActive = currentIdx === stepIdx
              const isPending = currentIdx < stepIdx
              const dotColor = isDone || isActive ? 'var(--amd-orange)' : 'rgba(255,255,255,0.18)'
              const showSubs = isActive && step.key === 'running'
              return (
                <div key={step.key} style={{ display: 'flex', alignItems: 'flex-start', gap: 14,
                  paddingBottom: idx < PIPELINE_STEPS.length - 1 ? 18 : 0, position: 'relative' }}>
                  {idx < PIPELINE_STEPS.length - 1 && (
                    <div style={{ position: 'absolute', left: 19, top: 42, width: 2,
                      height: `calc(100% - 20px)`,
                      background: isDone ? 'rgba(242,101,34,0.55)' : 'rgba(255,255,255,0.1)', borderRadius: 1 }} />
                  )}

                  <div style={{ width: 40, height: 40, borderRadius: '50%', flexShrink: 0,
                    border: `2px solid ${dotColor}`,
                    background: isDone ? 'rgba(242,101,34,0.16)' : isActive ? 'rgba(242,101,34,0.08)' : 'rgba(255,255,255,0.03)',
                    display: 'grid', placeItems: 'center', position: 'relative', zIndex: 1 }}>
                    {isDone ? (
                      <svg width="16" height="16" viewBox="0 0 12 12" fill="none" stroke="var(--amd-orange)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="2 6 5 9 10 3" />
                      </svg>
                    ) : isActive ? (
                      <div style={{ width: 14, height: 14, borderRadius: '50%', background: 'var(--amd-orange)',
                        animation: 'livePulse 1s ease-in-out infinite' }} />
                    ) : (
                      <div style={{ width: 10, height: 10, borderRadius: '50%', background: 'rgba(255,255,255,0.12)' }} />
                    )}
                  </div>

                  <div style={{ flex: 1, paddingTop: 8 }}>
                    <div style={{ fontSize: 13, fontWeight: 700, lineHeight: 1.3, marginBottom: 3,
                      color: isPending ? 'rgba(255,255,255,0.2)' : '#fff' }}>
                      {step.label}
                    </div>
                    {(isActive || isDone) && !showSubs && (
                      <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.4)', lineHeight: 1.5 }}>
                        {step.detail}
                      </div>
                    )}

                    {showSubs && (
                      <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
                        {GENERATE_SUBNODES.map((sub, si) => {
                          const subDone   = si < doneSubCount
                          const subActive = si === activeSubIdx
                          return (
                            <div key={si} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                              <div style={{ width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
                                background: subDone ? 'var(--amd-orange)' : subActive ? 'rgba(242,101,34,0.85)' : 'rgba(255,255,255,0.12)',
                                animation: subActive ? 'livePulse 1s ease-in-out infinite' : 'none',
                                boxShadow: subActive ? '0 0 8px rgba(242,101,34,0.65)' : 'none' }} />
                              <span style={{ fontSize: 11, fontWeight: subActive ? 600 : 400,
                                color: subDone || subActive ? 'rgba(255,255,255,0.72)' : 'rgba(255,255,255,0.22)' }}>
                                {sub.label}
                              </span>
                              {subDone && (
                                <svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="var(--amd-orange)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" style={{ marginLeft: 'auto' }}>
                                  <polyline points="2 6 5 9 10 3" />
                                </svg>
                              )}
                            </div>
                          )
                        })}
                      </div>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}

function InactiveFeed({ customerProfile }: { customerProfile: CustomerProfile }) {
  const message = customerProfile ? 'Demo video selected' : 'Select customer profile'
  const detail = customerProfile
    ? 'Use the download icon on the video'
    : 'Choose a customer profile'
  return (
    <div style={{ width: '100%', height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', background: 'var(--bg-elevated)', gap: 10 }}>
      <div style={{ width: 48, height: 48, borderRadius: '50%', background: 'var(--hover)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="var(--text-dim)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M23 7l-7 5 7 5V7z" /><rect x="1" y="5" width="15" height="14" rx="2" ry="2" />
        </svg>
      </div>
      <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{message}</span>
      <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>{detail}</span>
    </div>
  )
}

function PanelHeader({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ padding: '10px 14px', background: 'var(--bg-surface)', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
      {children}
    </div>
  )
}

function AccentBar() {
  return <div style={{ width: 3, height: 13, background: 'var(--gradient-primary)', borderRadius: 2, flexShrink: 0 }} />
}

const labelStyle: React.CSSProperties = {
  fontFamily: 'var(--font-condensed)',
  fontSize: 'var(--fs-md)',
  fontWeight: 700,
  letterSpacing: '0.12em',
  textTransform: 'uppercase',
  color: 'var(--text-secondary)',
}

// ------------------------------------------------------------------ //
// Resizable multi-pane row for generation comparison
// ------------------------------------------------------------------ //

function ResizableRow({ panes }: { panes: Array<{ label: string; src: string }> }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [widths, setWidths] = useState<number[]>(() => panes.map(() => 100 / panes.length))
  // Holds the teardown for an in-progress drag so listeners can be removed if
  // the component unmounts (or a new drag starts) before mouseup fires.
  const dragCleanupRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    setWidths(panes.map(() => 100 / panes.length))
  }, [panes.length])

  // Remove any dangling drag listeners on unmount to avoid a leak mid-drag.
  useEffect(() => () => { dragCleanupRef.current?.() }, [])

  function startDrag(handleIdx: number, e: React.MouseEvent) {
    e.preventDefault()
    const startX = e.clientX
    const startWidths = [...widths]
    const containerW = containerRef.current?.offsetWidth ?? 800
    const MIN_PCT = (180 / containerW) * 100
    const sumPair = startWidths[handleIdx] + startWidths[handleIdx + 1]

    function onMove(ev: MouseEvent) {
      const dx = ev.clientX - startX
      const deltaPct = (dx / containerW) * 100
      const leftW = Math.max(MIN_PCT, Math.min(sumPair - MIN_PCT, startWidths[handleIdx] + deltaPct))
      setWidths(prev => {
        const next = [...prev]
        next[handleIdx] = leftW
        next[handleIdx + 1] = sumPair - leftW
        return next
      })
    }

    function onUp() {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      dragCleanupRef.current = null
    }

    // Clear any previous (unfinished) drag before registering this one.
    dragCleanupRef.current?.()
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    dragCleanupRef.current = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }

  return (
    <div ref={containerRef} style={{ width: '100%', height: '100%', display: 'flex' }}>
      {panes.map((pane, idx) => (
        <Fragment key={idx}>
          <div style={{ flex: `0 0 ${widths[idx] ?? 100 / panes.length}%`, minWidth: 0, overflow: 'hidden', position: 'relative' }}>
            <VideoPane label={pane.label} src={pane.src} />
          </div>
          {idx < panes.length - 1 && (
            <div
              onMouseDown={e => startDrag(idx, e)}
              onMouseEnter={e => (e.currentTarget.style.background = 'rgba(242,101,34,0.55)')}
              onMouseLeave={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.07)')}
              style={{
                width: 5, flexShrink: 0, cursor: 'col-resize',
                background: 'rgba(255,255,255,0.07)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                transition: 'background 0.12s', userSelect: 'none', zIndex: 4,
              }}
            >
              <div style={{ width: 1, height: 28, borderRadius: 1, background: 'rgba(255,255,255,0.22)' }} />
            </div>
          )}
        </Fragment>
      ))}
    </div>
  )
}

