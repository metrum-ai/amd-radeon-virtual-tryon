// Copyright Advanced Micro Devices, Inc.
// 
// SPDX-License-Identifier: MIT

import React, { useState, useEffect, useMemo } from 'react'

function Skel({ height, width = '100%', radius = 4 }: { height: number; width?: number | string; radius?: number }) {
  return (
    <div style={{
      height, width, borderRadius: radius, flexShrink: 0,
      background: 'linear-gradient(90deg, #1a1a1e 25%, #252528 50%, #1a1a1e 75%)',
      backgroundSize: '200% 100%',
      animation: 'shimmer 1.5s ease-in-out infinite',
    }} />
  )
}
import type { Garment } from '../types/vto'

interface ApiGarment {
  item_id: string
  name: string
  category: string
  overlay_category: 'tops' | 'bottoms' | 'one-pieces'
  brand: string
  color: string
  price: number
  image_path: string
}

interface ApiRec {
  garment: ApiGarment
  reasoning: string
  score: number
  recommendation_type: string
}

function toGarment(g: ApiGarment): Garment {
  const image = g.image_path.startsWith('/')
    ? g.image_path
    : `/${g.image_path}`

  return {
    id: g.item_id,
    name: g.name,
    category: g.category as Garment['category'],
    overlayCategory: g.overlay_category,
    subcategory: '',
    brand: g.brand,
    color: g.color,
    price: g.price,
    gradient: 'linear-gradient(145deg, #1e1e26, #2a2a36)',
    image,
    tags: [],
  }
}

const env = (import.meta as ImportMeta & {
  env?: Record<string, string | undefined>
}).env
const VISIBLE_RECOMMENDATION_COUNT = Number(
  env?.VITE_VISIBLE_RECOMMENDATION_COUNT ?? 3
)
const MAX_RECOMMENDATION_COUNT = Number(
  env?.VITE_MAX_RECOMMENDATION_COUNT ?? 8
)

interface Props {
  selectedGarments: Garment[]
  agentRecommendations: Garment[]
  onSelectGarment: (g: Garment) => void
  onRunPipeline: () => void
  pipelineRunning: boolean
  sessionId: string
  customerProfile: string
}

interface StylingApiResponse {
  recommendations: ApiRec[]
  styling_score: number
  suggestions: string[]
}

export default function StylingPanel({
  selectedGarments,
  agentRecommendations,
  onSelectGarment,
  onRunPipeline,
  pipelineRunning,
  sessionId,
  customerProfile,
}: Props) {
  const [apiRecs, setApiRecs] = useState<ApiRec[]>([])
  const [loading, setLoading] = useState(false)
  const [previewGarment, setPreviewGarment] = useState<Garment | null>(null)
  const [recPage, setRecPage] = useState(0)

  const ITEMS_PER_PAGE = 3

  useEffect(() => {
    setApiRecs([])
    setRecPage(0)
  }, [sessionId])

  // Fetch API recommendations whenever the selection or session changes.
  // This populates the panel when agent chat recommendations are empty and
  // provides complementary suggestions after the user selects a garment.
  useEffect(() => {
    if (!sessionId) return
    const ctrl = new AbortController()
    const showSkeleton = agentRecommendations.length === 0
    if (showSkeleton) setLoading(true)

    const body = JSON.stringify({
      session_id: sessionId,
      garment_ids: selectedGarments.map(g => g.id),
      customer_gender: customerProfile.startsWith('female')
        ? 'women'
        : customerProfile.startsWith('male')
          ? 'men'
          : undefined,
    })

    fetch('/api/v1/vto/styling/recommend', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      signal: ctrl.signal,
    })
      .then(r => (r.ok ? r.json() : Promise.reject(new Error(`${r.status}`))))
      .then((data: StylingApiResponse) => {
        setApiRecs(data.recommendations ?? [])
      })
      .catch(err => {
        if (err.name === 'AbortError') return
        setApiRecs([])
      })
      .finally(() => {
        if (showSkeleton) setLoading(false)
      })

    return () => ctrl.abort()
  }, [sessionId, selectedGarments.length, customerProfile, agentRecommendations.length])

  // Reset carousel page when recommendations change
  useEffect(() => { setRecPage(0) }, [agentRecommendations.length, apiRecs.length])

  const selectedGarmentIds = new Set(selectedGarments.map(g => g.id))
  const outfitMax = selectedGarments.some(g => g.overlayCategory === 'one-pieces') ? 1 : 2

  // Merge agent chat recommendations with API recommendations,
  // preferring agent picks and deduplicating by garment ID.
  const recs = useMemo(() => {
    const agentItems = agentRecommendations
      .slice(0, MAX_RECOMMENDATION_COUNT)
      .map((g, i) => ({
        garment: g,
        reason: [g.color, g.subcategory || g.category].filter(Boolean).join(' · '),
        score: parseFloat((0.97 - i * 0.04).toFixed(2)),
      }))

    const apiItems = apiRecs
      .slice(0, MAX_RECOMMENDATION_COUNT)
      .map(r => ({
        garment: toGarment(r.garment),
        reason: r.reasoning,
        score: r.score,
      }))

    const seen = new Set<string>()
    const merged: typeof agentItems = []
    for (const item of [...agentItems, ...apiItems]) {
      if (!seen.has(item.garment.id)) {
        seen.add(item.garment.id)
        merged.push(item)
      }
    }
    return merged.slice(0, MAX_RECOMMENDATION_COUNT)
  }, [agentRecommendations, apiRecs])

  const totalPages = Math.max(1, Math.ceil(recs.length / ITEMS_PER_PAGE))
  const safePage = Math.min(recPage, totalPages - 1)
  const pageRecs = recs.slice(safePage * ITEMS_PER_PAGE, (safePage + 1) * ITEMS_PER_PAGE)

  return (
    <div style={{ flex: '0 1 124px', minHeight: 0, display: 'flex', flexDirection: 'column', background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', minWidth: 0, overflow: 'hidden' }}>
      {/* Header */}
      <div style={{ padding: '6px 10px', background: 'var(--bg-surface)', borderBottom: '1px solid var(--border)', flexShrink: 0, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ width: 3, height: 14, background: 'var(--gradient-amd)', borderRadius: 2 }} />
          <span style={{ fontFamily: 'var(--font-condensed)', fontSize: 14, fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-secondary)' }}>Styling Recommendations</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {/* Carousel controls */}
          {recs.length > ITEMS_PER_PAGE && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <button
                onClick={() => setRecPage(p => Math.max(0, p - 1))}
                disabled={safePage === 0}
                style={{
                  width: 22, height: 22, borderRadius: 4, border: '1px solid var(--border)',
                  background: 'var(--bg-elevated)', color: safePage === 0 ? 'var(--text-dim)' : 'var(--text-secondary)',
                  fontSize: 12, fontWeight: 800, display: 'grid', placeItems: 'center',
                  cursor: safePage === 0 ? 'not-allowed' : 'pointer', padding: 0,
                }}
              >‹</button>
              <span style={{ fontSize: 10, color: 'var(--text-dim)', minWidth: 28, textAlign: 'center' }}>
                {safePage + 1}/{totalPages}
              </span>
              <button
                onClick={() => setRecPage(p => Math.min(totalPages - 1, p + 1))}
                disabled={safePage >= totalPages - 1}
                style={{
                  width: 22, height: 22, borderRadius: 4, border: '1px solid var(--border)',
                  background: 'var(--bg-elevated)', color: safePage >= totalPages - 1 ? 'var(--text-dim)' : 'var(--text-secondary)',
                  fontSize: 12, fontWeight: 800, display: 'grid', placeItems: 'center',
                  cursor: safePage >= totalPages - 1 ? 'not-allowed' : 'pointer', padding: 0,
                }}
              >›</button>
            </div>
          )}
          <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>
            {selectedGarments.length}/{outfitMax} selected
          </span>
          <button
            disabled={pipelineRunning || selectedGarments.length === 0}
            onClick={onRunPipeline}
            style={{
              border: (pipelineRunning || selectedGarments.length === 0) ? '1px solid var(--border-light)' : '1px solid rgba(242,101,34,0.55)',
              borderRadius: 6,
              background: (pipelineRunning || selectedGarments.length === 0) ? 'var(--bg-elevated)' : 'var(--gradient-amd)',
              color: (pipelineRunning || selectedGarments.length === 0) ? 'var(--text-dim)' : '#fff',
              fontSize: 10,
              fontWeight: 800,
              padding: '4px 8px',
              cursor: (pipelineRunning || selectedGarments.length === 0) ? 'not-allowed' : 'pointer',
            }}
          >
            {pipelineRunning ? 'Generating...' : 'Generate Try-On'}
          </button>
        </div>
      </div>

      <div style={{ flex: 1, minHeight: 0, overflowX: 'hidden', overflowY: 'hidden', padding: 8 }}>
        <div role="listbox" aria-multiselectable="true" style={{ display: 'flex', gap: 8, height: '100%' }}>
          {loading && Array.from({ length: 3 }).map((_, i) => (
            <div key={i} style={{ flex: '1 1 160px', maxWidth: 218, minWidth: 0, display: 'flex', alignItems: 'center', gap: 9, padding: '7px 8px', background: 'var(--elevated)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border)', height: '100%' }}>
              <Skel height={44} width={44} radius={6} />
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 6 }}>
                <Skel height={11} width="80%" />
                <Skel height={9} width="55%" />
                <Skel height={8} width="35%" />
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 5, alignItems: 'flex-end' }}>
                <Skel height={12} width={32} />
                <Skel height={10} width={28} />
              </div>
            </div>
          ))}
          {!loading && recs.length === 0 && selectedGarments.length === 0 && (
            <div style={{ minWidth: 240, padding: '10px 12px', background: 'var(--elevated)', borderRadius: 'var(--radius-md)', border: '1px dashed var(--border)', textAlign: 'center', display: 'grid', placeItems: 'center' }}>
              <span style={{ fontSize: 11, color: 'var(--text-dim)' }}>No recommendations yet</span>
            </div>
          )}
          {!loading && pageRecs.map(r => {
            const isSelected = selectedGarmentIds.has(r.garment.id)
            const isMaxed = selectedGarments.length >= outfitMax
            return (
              <RecommendationRow
                key={r.garment.id}
                garment={r.garment}
                reason={r.reason}
                score={r.score}
                selected={isSelected}
                selectionDisabled={!isSelected && isMaxed}
                onToggle={onSelectGarment}
                onPreview={setPreviewGarment}
              />
            )
          })}
        </div>
      </div>
      {previewGarment && (
        <GarmentDetailModal
          garment={previewGarment}
          onClose={() => setPreviewGarment(null)}
          onTryOn={() => {
            onSelectGarment(previewGarment)
            setPreviewGarment(null)
          }}
        />
      )}
    </div>
  )
}

function RecommendationRow({
  garment,
  reason,
  score,
  selected,
  selectionDisabled,
  onToggle,
  onPreview,
}: {
  garment: Garment
  reason: string
  score: number
  selected: boolean
  selectionDisabled: boolean
  onToggle: (g: Garment) => void
  onPreview: (g: Garment) => void
}) {
  const [imgError, setImgError] = React.useState(false)
  return (
    <div
      role="option"
      aria-selected={selected}
      tabIndex={selectionDisabled ? -1 : 0}
      onClick={() => {
        if (!selectionDisabled) onToggle(garment)
      }}
      onKeyDown={e => {
        if (selectionDisabled) return
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onToggle(garment)
        }
      }}
      style={{
        flex: '1 1 160px',
        maxWidth: 218,
        minWidth: 0,
        display: 'flex',
        alignItems: 'center',
        gap: 9,
        padding: '7px 8px',
        background: selected ? 'rgba(237,28,36,0.12)' : 'var(--elevated)',
        border: `1px solid ${selected ? 'rgba(237,28,36,0.65)' : 'var(--border)'}`,
        borderRadius: 'var(--radius-md)',
        cursor: selectionDisabled ? 'not-allowed' : 'pointer',
        opacity: selectionDisabled ? 0.52 : 1,
        textAlign: 'left',
        height: '100%',
      }}
      onMouseEnter={e => {
        if (!selected && !selectionDisabled) e.currentTarget.style.borderColor = 'var(--border-light)'
      }}
      onMouseLeave={e => {
        if (!selected) e.currentTarget.style.borderColor = 'var(--border)'
      }}
    >
      <div style={{ width: 44, height: 44, borderRadius: 'var(--radius-sm)', background: garment.gradient, flexShrink: 0, overflow: 'hidden', position: 'relative' }}>
        {!imgError && (
          <img src={garment.image} alt={garment.name} onError={() => setImgError(true)} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
        )}
        {selected && (
          <span style={{ position: 'absolute', top: 3, right: 3, width: 14, height: 14, borderRadius: '50%', background: 'var(--amd-red)', color: '#fff', fontSize: 10, display: 'grid', placeItems: 'center', fontWeight: 900 }}>
            ✓
          </span>
        )}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{garment.name}</div>
        <div style={{ fontSize: 10, color: 'var(--text-dim)', marginTop: 3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{reason}</div>
        <button
          onClick={e => {
            e.stopPropagation()
            onPreview(garment)
          }}
          style={{ marginTop: 5, border: 0, background: 'transparent', color: 'var(--teal)', fontSize: 10, fontWeight: 800, padding: 0 }}
        >
          View
        </button>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 3, flexShrink: 0 }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 800, color: 'var(--sev-safe)' }}>{Math.round(score * 100)}%</span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-dim)' }}>${garment.price.toFixed(2)}</span>
      </div>
    </div>
  )
}

function GarmentDetailModal({
  garment,
  onClose,
  onTryOn,
}: {
  garment: Garment
  onClose: () => void
  onTryOn: () => void
}) {
  const [imgError, setImgError] = React.useState(false)
  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1000,
        background: 'rgba(0,0,0,0.72)',
        backdropFilter: 'blur(8px)',
        display: 'grid',
        placeItems: 'center',
        padding: 24,
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          width: 'min(760px, 94vw)',
          maxHeight: '88vh',
          display: 'flex',
          flexWrap: 'wrap',
          background: 'var(--card)',
          border: '1px solid var(--border-light)',
          borderRadius: 'var(--radius-lg)',
          overflow: 'hidden',
          boxShadow: '0 24px 70px rgba(0,0,0,0.65)',
          animation: 'agentPopOut 0.22s ease both',
        }}
      >
        <div style={{ minHeight: 420, background: garment.gradient, flex: '1.1 1 260px', minWidth: 0 }}>
          {!imgError && (
            <img
              src={garment.image}
              alt={garment.name}
              onError={() => setImgError(true)}
              style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
            />
          )}
        </div>
        <div style={{ padding: 22, display: 'flex', flexDirection: 'column', gap: 14, flex: '0.9 1 220px', minWidth: 0 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
            <div>
              <div style={{ fontSize: 11, color: 'var(--text-dim)', fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
                Dress View
              </div>
              <h2 style={{ margin: '6px 0 0', fontSize: 24, lineHeight: 1.1, color: 'var(--text-primary)' }}>
                {garment.name}
              </h2>
            </div>
            <button onClick={onClose} style={{ alignSelf: 'flex-start', background: 'none', border: 0, color: 'var(--text-dim)', fontSize: 18 }}>x</button>
          </div>
          <div style={{ display: 'grid', gap: 8, color: 'var(--text-secondary)', fontSize: 13 }}>
            <span>{garment.brand || 'Meridian'}</span>
            <span>{[garment.color, garment.category].filter(Boolean).join(' · ')}</span>
            <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-primary)', fontWeight: 800 }}>${garment.price.toFixed(2)}</span>
          </div>
          <button
            onClick={onTryOn}
            style={{
              marginTop: 'auto',
              border: 0,
              borderRadius: 8,
              background: 'var(--gradient-amd)',
              color: '#fff',
              padding: '10px 14px',
              fontSize: 13,
              fontWeight: 800,
            }}
          >
            Try On
          </button>
        </div>
      </div>
    </div>
  )
}
